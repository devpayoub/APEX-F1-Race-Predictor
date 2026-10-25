"""Ensemble blend, calibration and per-driver explainability."""
from __future__ import annotations

import numpy as np

from .config import (BLEND_MC, BLEND_ML, CALIBRATION_T, ML_SOFTEN_T,
                     POINTS_POSITIONS, WIN_SHRINK)
from .features import DriverFeature
from .ml_model import marginalise


def _calibrate(p: np.ndarray, temperature: float) -> np.ndarray:
    q = np.power(np.clip(p, 1e-9, 1.0), 1.0 / max(0.05, temperature))
    s = q.sum()
    return q / s if s > 0 else np.full_like(q, 1.0 / len(q))


def _scale_to(p: np.ndarray, target: float) -> np.ndarray:
    """Find the largest lambda with sum(min(1, p*lambda)) == target.

    Simple rescaling fails here because probabilities saturate at 1.0; this is
    the exact water-filling solution, solved by bisection.
    """
    p = np.clip(p, 0.0, 1.0)
    if p.sum() <= 1e-12:
        return p
    if p.sum() >= target:                      # scale down, no saturation
        return p * (target / p.sum())
    lo, hi = 1.0, 1e7
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        s = float(np.minimum(1.0, p * mid).sum())
        if s < target:
            lo = mid
        else:
            hi = mid
    return np.minimum(1.0, p * (0.5 * (lo + hi)))


def _contributions(feats: list[DriverFeature]) -> None:
    """Relative share of each factor in a driver's projected performance."""
    keys = ["quali_pace", "race_pace", "team_form", "circuit_affinity", "reliability"]
    raw = np.zeros((len(feats), len(keys)))
    for i, f in enumerate(feats):
        raw[i, 0] = np.exp(-f.q_gap / 0.55)
        raw[i, 1] = np.exp(-f.long_run_gap / 0.55)
        raw[i, 2] = np.exp(f.team_form / 14.0)
        raw[i, 3] = np.exp(-(f.circuit_affinity - 8.0) / 6.0)
        raw[i, 4] = np.exp(-f.dnf_rate / 0.16)

    # convert to share-of-field, then to share-within-driver
    field = raw.sum(axis=0, keepdims=True)
    share = raw / np.clip(field, 1e-9, None)
    for i, f in enumerate(feats):
        v = share[i]
        tot = v.sum()
        f.contributions = {k: round(float(x / tot), 3) if tot else 0.2
                           for k, x in zip(keys, v)}


def _geometric_blend(p_mc: np.ndarray, p_ml: np.ndarray, n_sims: int) -> np.ndarray:
    """Log-opinion pool of the two forecasters.

    Arithmetic blending lets the prior hand a backmarker a win chance the
    simulation cannot support, which then needs a hard cap — and a cap applied
    across 22 drivers flattens the whole tail to one number. Multiplying the two
    distributions instead lets a near-zero simulation probability suppress the
    prior naturally: no cap required, and the field keeps a meaningful spread.
    """
    n = max(1, int(n_sims))
    # Laplace smoothing so a driver with zero wins is not exactly zero
    p_mc = (p_mc * n + 0.5) / (n + 1.0)
    logp = BLEND_MC * np.log(np.clip(p_mc, 1e-12, 1.0)) + \
        BLEND_ML * np.log(np.clip(p_ml, 1e-12, 1.0))
    logp -= logp.max()
    w = np.exp(logp)
    s = w.sum()
    return w / s if s > 0 else np.full(len(p_mc), 1.0 / len(p_mc))


def blend(feats: list[DriverFeature], sim, bucket_probs: np.ndarray) -> dict:
    win_ml, pod_ml, pts_ml = marginalise(bucket_probs)

    # soften the tree prior: it is over-confident on single rows
    win_ml = _calibrate(win_ml, ML_SOFTEN_T)
    pod_ml = _scale_to(pod_ml, 3.0)
    pts_ml = _scale_to(pts_ml, float(POINTS_POSITIONS))

    win = _geometric_blend(sim.win, win_ml, getattr(sim, "n_sims", 0))
    win = _calibrate(win, CALIBRATION_T)
    if WIN_SHRINK > 0:
        n = len(win)
        win = (1.0 - WIN_SHRINK) * win + WIN_SHRINK / n
        win = win / win.sum()

    pod = BLEND_MC * sim.podium + BLEND_ML * pod_ml
    pod = _scale_to(pod, 3.0)

    pts = BLEND_MC * sim.points + BLEND_ML * pts_ml
    pts = _scale_to(pts, float(POINTS_POSITIONS))

    _contributions(feats)

    for i, f in enumerate(feats):
        f.win_prob = float(win[i])
        f.win_prob_mc = float(sim.win[i])
        f.win_prob_ml = float(win_ml[i])
        f.podium_prob = float(pod[i])
        f.points_prob = float(pts[i])
        f.expected_finish = float(sim.expected_finish[i])
        f.finish_band = (int(sim.band_low[i]), int(sim.band_high[i]))
        f.dnf_prob = float(sim.dnf_prob[i])
        gap = sim.expected_gap[i]
        f.expected_gap = None if not np.isfinite(gap) else round(float(gap), 2)
        two = float(sim.two_stop_share[i])
        f.strategy = "2-stop M-M-S" if two > 0.5 else "1-stop M-H"
        # model confidence blends data completeness with simulation spread
        spread = float(sim.band_high[i] - sim.band_low[i])
        f.model_confidence = round(float(np.clip(
            f.confidence * (1.0 - min(0.45, spread / 30.0)), 0.15, 0.97)), 3)

    # the published order is the predicted classification: lowest expected
    # finishing position first
    order = np.argsort(sim.expected_finish)
    contenders = list(np.argsort(-win)[:3])
    return {
        "win": win, "podium": pod, "points": pts,
        "order": order,
        "contenders": contenders,
        "sanity": {
            "sum_win": round(float(win.sum()), 6),
            "sum_podium": round(float(pod.sum()), 6),
            "sum_points": round(float(pts.sum()), 6),
        },
    }
