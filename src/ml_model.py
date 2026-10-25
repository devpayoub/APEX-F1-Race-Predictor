"""Gradient-boosted prior: grid + form -> finish bucket, trained on history."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from .features import DriverFeature
from .sources import jolpica

BUCKETS = ["P1", "P2", "P3", "P4-6", "P7-10", "P11-15", "P16+"]


def _bucket(pos: int) -> int:
    if pos <= 3:
        return pos - 1
    if pos <= 6:
        return 3
    if pos <= 10:
        return 4
    if pos <= 15:
        return 5
    return 6


def _laplace(hist: list[float], prior: float = 10.0) -> float:
    if not hist:
        return prior
    return (sum(hist) + prior) / (len(hist) + 1.0)


@dataclass
class TrainingFrame:
    X: np.ndarray
    y: np.ndarray
    groups: np.ndarray
    team_ids: dict[str, int] = field(default_factory=dict)


def build_training_frame(seasons: list[int]) -> TrainingFrame:
    rows: list[list[float]] = []
    targets: list[int] = []
    groups: list[int] = []
    team_ids: dict[str, int] = {}

    drv_fin: dict[str, list[float]] = defaultdict(list)
    drv_pts: dict[str, list[float]] = defaultdict(list)
    drv_dnf: dict[str, list[int]] = defaultdict(list)
    team_pts: dict[str, list[float]] = defaultdict(list)
    circ_fin: dict[tuple[str, str], list[float]] = defaultdict(list)

    for season in seasons:
        try:
            races = jolpica.season_results(season)
        except Exception:  # noqa: BLE001
            continue
        n_races = max(1, len(races))
        for race in races:
            rnd = int(race.get("round") or 0)
            circ = (race.get("Circuit") or {}).get("circuitId") or "unknown"
            results = race.get("Results", [])
            if not results:
                continue

            for res in results:
                code = (res.get("Driver") or {}).get("code") or "???"
                team = (res.get("Constructor") or {}).get("name") or "Unknown"
                grid_raw = res.get("grid")
                try:
                    grid = float(grid_raw) if grid_raw not in (None, "") else 20.0
                except (TypeError, ValueError):
                    grid = 20.0
                if grid <= 0:
                    grid = 20.0
                team_ids.setdefault(team, len(team_ids))

                d_fin = drv_fin[code][-5:]
                d_pts = drv_pts[code][-5:]
                t_pts = team_pts[team][-10:]
                d_dnf = drv_dnf[code]

                rows.append([
                    grid,
                    rnd / n_races,
                    float(np.mean(d_fin)) if d_fin else 11.0,
                    float(np.mean(d_pts)) if d_pts else 0.0,
                    (sum(t_pts) / (len(t_pts) / 2.0)) if t_pts else 0.0,
                    _laplace(circ_fin[(circ, code)]),
                    (sum(d_dnf) / len(d_dnf)) if d_dnf else 0.08,
                    1.0 if season >= 2026 else 0.0,
                ])
                try:
                    pos = int(res.get("position"))
                except (TypeError, ValueError):
                    pos = 20
                targets.append(_bucket(pos))
                groups.append(season * 1000 + rnd)

            # ---- update history after the race ----
            for res in results:
                code = (res.get("Driver") or {}).get("code") or "???"
                team = (res.get("Constructor") or {}).get("name") or "Unknown"
                try:
                    pos = float(res.get("position"))
                except (TypeError, ValueError):
                    pos = 20.0
                status = str(res.get("status") or "")
                dnf = 0 if (status.startswith("Finished") or "Lap" in status) else 1
                drv_fin[code].append(pos)
                drv_pts[code].append(float(res.get("points") or 0))
                drv_dnf[code].append(dnf)
                team_pts[team].append(float(res.get("points") or 0))
                circ_fin[(circ, code)].append(pos)

    X = np.array(rows, dtype=float) if rows else np.zeros((0, 8))
    y = np.array(targets, dtype=int)
    g = np.array(groups, dtype=int)
    return TrainingFrame(X=X, y=y, groups=g, team_ids=team_ids)


@dataclass
class PriorModel:
    model: object
    team_ids: dict[str, int]
    cv_logloss: float | None
    n_rows: int
    ok: bool


def train(seasons: list[int] | None = None) -> PriorModel:
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.model_selection import GroupKFold

    seasons = seasons or [2022, 2023, 2024, 2025, 2026]
    frame = build_training_frame(seasons)
    if len(frame.y) < 200:
        return PriorModel(model=None, team_ids={}, cv_logloss=None, n_rows=len(frame.y), ok=False)

    model = HistGradientBoostingClassifier(
        max_iter=400, learning_rate=0.05, max_depth=6,
        l2_regularization=1.0, min_samples_leaf=25, random_state=42,
    )
    # time-series style CV by race weekend
    scores = []
    try:
        gkf = GroupKFold(n_splits=5)
        for tr, te in gkf.split(frame.X, frame.y, frame.groups):
            m = HistGradientBoostingClassifier(
                max_iter=250, learning_rate=0.05, max_depth=6,
                l2_regularization=1.0, min_samples_leaf=25, random_state=42,
            )
            m.fit(frame.X[tr], frame.y[tr])
            proba = m.predict_proba(frame.X[te])
            scores.append(float(-np.mean(np.log(np.clip(
                proba[np.arange(len(te)), frame.y[te]], 1e-9, 1.0)))))
    except Exception:  # noqa: BLE001
        scores = []

    model.fit(frame.X, frame.y)
    return PriorModel(model=model, team_ids=frame.team_ids,
                      cv_logloss=float(np.mean(scores)) if scores else None,
                      n_rows=len(frame.y), ok=True)


def predict(prior: PriorModel, feats: list[DriverFeature],
            round_num: int | None = None, total_rounds: int | None = None) -> np.ndarray:
    """Return (n_drivers, 7) bucket probabilities."""
    n = len(feats)
    if not prior.ok or prior.model is None:
        return np.full((n, 7), 1.0 / 7.0)

    frac = (round_num / total_rounds) if (round_num and total_rounds) else 0.75
    rows = []
    for f in feats:
        rows.append([
            float(max(1, f.grid)),
            frac,
            f.driver_form,
            f.driver_points,
            f.team_form,
            f.circuit_affinity,
            f.dnf_rate,
            1.0,
        ])
    X = np.array(rows, dtype=float)
    proba = prior.model.predict_proba(X)

    # align classes to the 7 buckets
    classes = list(getattr(prior.model, "classes_", range(7)))
    out = np.zeros((n, 7))
    for j, cls in enumerate(classes):
        out[:, int(cls)] = proba[:, j]
    s = out.sum(axis=1, keepdims=True)
    return out / np.clip(s, 1e-9, None)


def marginalise(bucket_probs: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    win = bucket_probs[:, 0]
    podium = bucket_probs[:, 0:3].sum(axis=1)
    points = bucket_probs[:, 0:5].sum(axis=1)
    return win, podium, points
