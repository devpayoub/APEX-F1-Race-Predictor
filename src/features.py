"""Feature engineering: turns raw API payloads into one row per driver."""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field, asdict
from typing import Any

from .config import (COMPOUND_DEG_MULT, COMPOUND_PACE_OFFSET, DEG_CLAMP,
                     DEG_PRIOR, DEG_SHRINK, FUEL_BURN_PER_LAP, FUEL_EFFECT,
                     LR_CLAMP, LR_SHRINK, PACE_W_LONG_RUN, PACE_W_QUALI,
                     PRACTICE_FUEL_KG, RACE_PACE_OFFSET, START_FUEL_KG)

LONG_RUN_COMPOUNDS = {"SOFT", "MEDIUM", "HARD"}


@dataclass
class DriverFeature:
    number: int
    code: str
    name: str
    team: str
    team_colour: str
    grid: int = 20
    q_best: float | None = None          # seconds
    q_gap: float = 0.0                   # seconds to pole
    q_segment: str = "Q1"
    long_run_pace: float | None = None   # raw practice median, seconds
    race_pace_est: float | None = None   # corrected to race fuel
    long_run_gap: float = 0.0
    pace_gap: float = 0.0
    deg_rate: float = 0.075
    deg_raw: float | None = None
    deg_source: str = "default"
    lap_sigma: float = 0.18
    top_speed: int | None = None
    dnf_rate: float = 0.08
    team_form: float = 0.0               # points per race, last 5
    driver_form: float = 11.0            # mean finish, last 5
    driver_points: float = 0.0
    circuit_affinity: float = 10.0       # smoothed mean finish at this circuit
    circuit_starts: int = 0
    confidence: float = 0.7
    sources: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    # filled by the simulator / blend
    base_lap: float = 0.0
    win_prob: float = 0.0
    podium_prob: float = 0.0
    points_prob: float = 0.0
    expected_finish: float = 11.0
    finish_band: tuple[int, int] = (1, 20)
    expected_gap: float | None = None
    dnf_prob: float = 0.0
    strategy: str = ""
    contributions: dict[str, float] = field(default_factory=dict)
    model_confidence: float = 0.0
    win_prob_mc: float = 0.0
    win_prob_ml: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ------------------------------------------------------------------ helpers --
def _ols_slope(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den <= 1e-9:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den


def _median(vals: list[float]) -> float | None:
    clean = [v for v in vals if v is not None and math.isfinite(v)]
    return statistics.median(clean) if clean else None


def _laplace(hist: list[float], prior: float = 10.0) -> float:
    if not hist:
        return prior
    return (sum(hist) + prior) / (len(hist) + 1.0)


# -------------------------------------------------------------------- quali --
def extract_quali(result_rows: list[dict], drivers: list[dict]) -> dict[int, dict]:
    """driver_number -> {position, best, gap, segment}"""
    out: dict[int, dict] = {}
    for row in result_rows:
        num = row.get("driver_number")
        if num is None:
            continue
        durs = [d for d in (row.get("duration") or []) if isinstance(d, (int, float)) and d > 0]
        gaps = [g for g in (row.get("gap_to_leader") or []) if isinstance(g, (int, float))]
        seg = "Q1"
        if len(durs) >= 3:
            seg = "Q3"
        elif len(durs) == 2:
            seg = "Q2"
        out[num] = {
            "position": row.get("position") or 99,
            "best": min(durs) if durs else None,
            "gap": gaps[-1] if gaps else None,
            "segment": seg,
            "laps": row.get("number_of_laps"),
        }
    # fill missing gaps from best-lap differences
    bests = [v["best"] for v in out.values() if v["best"]]
    pole = min(bests) if bests else None
    for v in out.values():
        if v["best"] and pole:
            v["gap"] = v["best"] - pole
    return out


# ------------------------------------------------------------ practice pace --
def _stint_stats(lap_map: dict[int, dict], stint: dict) -> dict | None:
    """Robust median + degradation slope for one stint.

    In-laps, traffic and yellow-flag laps are rejected by trimming anything more
    than 3% off the stint median, which is itself resistant to a couple of bad
    laps.
    """
    start, end = stint.get("lap_start"), stint.get("lap_end")
    if start is None or end is None:
        return None
    rows: list[tuple[int, float]] = []
    for ln in range(int(start), int(end) + 1):
        lap = lap_map.get(ln)
        if not lap:
            continue
        dur = lap.get("lap_duration")
        if dur is None or lap.get("is_pit_out_lap"):
            continue
        rows.append((ln, float(dur)))
    if len(rows) < 4:
        return None
    med = statistics.median([t for _, t in rows])
    keep = [(ln, t) for ln, t in rows if t <= med * 1.03]
    if len(keep) < 4:
        return None
    xs = [ln - int(start) for ln, _ in keep]
    ys = [t for _, t in keep]
    return {
        "median": statistics.median(ys),
        "slope": _ols_slope(xs, ys),
        "n": len(keep),
        "compound": (stint.get("compound") or "MEDIUM").upper(),
    }


def extract_practice_pace(lap_rows: list[dict], stint_rows: list[dict]) -> dict[int, dict]:
    """driver_number -> best sustained race-trim pace and raw degradation slope.

    Practice mixes qualifying simulations (3-5 laps, low fuel) with race
    simulations (8-18 laps, high fuel), and some sessions are partly wet. We
    first discard every stint slower than 6% off the session's fastest sustained
    run, then require >= 5 laps before calling a run representative.
    """
    laps_by_drv: dict[int, dict[int, dict]] = {}
    for lap in lap_rows:
        drv = lap.get("driver_number")
        if drv is None or lap.get("lap_number") is None:
            continue
        laps_by_drv.setdefault(drv, {})[int(lap["lap_number"])] = lap

    all_stats: list[tuple[int, dict]] = []
    for s in stint_rows:
        drv = s.get("driver_number")
        if drv is None:
            continue
        if (s.get("compound") or "").upper() not in LONG_RUN_COMPOUNDS:
            continue
        st = _stint_stats(laps_by_drv.get(drv, {}), s)
        if st:
            all_stats.append((drv, st))
    if not all_stats:
        return {}

    ref = min(st["median"] for _, st in all_stats)
    clean = [(d, st) for d, st in all_stats if st["median"] <= ref * 1.06]
    if not clean:
        return {}

    by_drv: dict[int, list[dict]] = {}
    for d, st in clean:
        by_drv.setdefault(d, []).append(st)

    out: dict[int, dict] = {}
    for drv, stats in by_drv.items():
        sustained = [s for s in stats if s["n"] >= 5] or stats
        for s in sustained:
            s["equiv"] = s["median"] + COMPOUND_PACE_OFFSET.get(s["compound"], 0.0)
        chosen = min(sustained, key=lambda s: s["equiv"])
        slopes = [s["slope"] for s in stats if s["slope"] is not None and s["n"] >= 6]
        meds = [s["equiv"] for s in sustained]
        out[drv] = {
            "pace": chosen["equiv"],
            "deg_raw": _median(slopes),
            "deg_n": sum(1 for s in stats if s["slope"] is not None and s["n"] >= 6),
            "n": chosen["n"],
            "compound": chosen["compound"],
            "sigma": max(0.10, min(0.28, statistics.pstdev(meds))) if len(meds) >= 2 else None,
        }
    return out


# ------------------------------------------------------------- form & prior --
def rolling_form(season_results: list[dict]) -> dict[str, dict]:
    """code -> {form, points, team_form, dnf_rate} from the last 5 rounds."""
    per_driver: dict[str, list[dict]] = {}
    per_team: dict[str, list[dict]] = {}
    for race in season_results:
        for res in race.get("Results", []):
            code = res.get("Driver", {}).get("code")
            team = res.get("Constructor", {}).get("name")
            if not code:
                continue
            entry = {
                "finish": int(res["position"]) if str(res.get("position", "")).isdigit() else 20,
                "points": float(res.get("points") or 0),
                "dnf": not str(res.get("status", "")).startswith("Finished")
                        and "+" not in str(res.get("status", ""))
                        and "Lap" not in str(res.get("status", "")),
            }
            per_driver.setdefault(code, []).append(entry)
            if team:
                per_team.setdefault(team, []).append(entry)

    out: dict[str, dict] = {}
    for code, hist in per_driver.items():
        last = hist[-5:]
        dnfs = sum(1 for h in hist if h["dnf"])
        starts = max(1, len(hist))
        # shrink toward an 8% baseline so a 17-race sample does not produce
        # implausible single-race retirement odds
        out[code] = {
            "driver_form": sum(h["finish"] for h in last) / len(last),
            "driver_points": sum(h["points"] for h in last) / len(last),
            "dnf_rate": min(0.40, (dnfs + 0.08 * 8) / (starts + 8)),
            "starts": starts,
        }

    # team form = points per car per race over the last 5 rounds (two cars -> /2)
    team_form: dict[str, float] = {}
    for team, hist in per_team.items():
        last = hist[-10:]
        if last:
            team_form[team] = sum(h["points"] for h in last) / (len(last) / 2.0)
    out["_team_form"] = team_form  # type: ignore[assignment]
    return out


def circuit_history(races: list[dict]) -> tuple[dict[str, list[float]], dict[str, list[float]]]:
    """Return (driver_code -> finishes, team_name -> finishes) at one circuit."""
    drv: dict[str, list[float]] = {}
    team: dict[str, list[float]] = {}
    for race in races:
        for res in race.get("Results", []):
            pos = res.get("position")
            if not str(pos).isdigit():
                continue
            p = float(pos)
            code = res.get("Driver", {}).get("code")
            tname = res.get("Constructor", {}).get("name")
            if code:
                drv.setdefault(code, []).append(p)
            if tname:
                team.setdefault(tname, []).append(p)
    return drv, team


# -------------------------------------------------------------------- build --
def build_features(
    drivers: list[dict],
    quali: dict[int, dict],
    practice: dict[int, dict],
    grid_map: dict[int, int],
    form: dict,
    circuit_drv: dict[str, list[float]],
    circuit_team: dict[str, list[float]],
    has_fastf1: bool = False,
    deg_prior: float = DEG_PRIOR,
) -> list[DriverFeature]:
    feats: list[DriverFeature] = []
    team_form = form.get("_team_form", {})

    for d in drivers:
        num = d.get("driver_number")
        code = (d.get("name_acronym") or "???").upper()
        q = quali.get(num, {})
        p = practice.get(num, {})

        f = DriverFeature(
            number=int(num),
            code=code,
            name=(d.get("full_name") or code).title(),
            team=d.get("team_name") or "Unknown",
            team_colour=f"#{d.get('team_colour') or '888888'}",
            q_best=q.get("best"),
            q_gap=q.get("gap") or 0.0,
            q_segment=q.get("segment", "Q1"),
        )
        f.sources = ["openf1"]
        if p.get("pace"):
            f.long_run_pace = p["pace"]
            f.race_pace_est = p["pace"] + FUEL_EFFECT * (START_FUEL_KG - PRACTICE_FUEL_KG)
            f.sources.append("openf1/practice")
        if p.get("deg_raw") is not None:
            f.deg_raw = float(p["deg_raw"])
            f.deg_source = "measured"
        if p.get("sigma"):
            f.lap_sigma = float(p["sigma"])

        info = form.get(code, {})
        f.driver_form = float(info.get("driver_form", 11.0))
        f.driver_points = float(info.get("driver_points", 0.0))
        f.dnf_rate = float(info.get("dnf_rate", 0.08))
        f.team_form = float(team_form.get(f.team, 0.0))

        ch = circuit_drv.get(code, [])
        f.circuit_affinity = _laplace(ch, prior=10.0)
        f.circuit_starts = len(ch)

        f.grid = int(grid_map.get(num, q.get("position", 20)))
        feats.append(f)

    _impute_pace(feats, deg_prior)
    _normalise(feats, has_fastf1)
    _score_confidence(feats)
    return feats


def _impute_pace(feats: list[DriverFeature], deg_prior: float = DEG_PRIOR) -> None:
    """Fill a missing long run from the team mate; never invent a faster one."""
    by_team: dict[str, list[DriverFeature]] = {}
    for f in feats:
        by_team.setdefault(f.team, []).append(f)

    for team, group in by_team.items():
        vals = [g.long_run_pace for g in group if g.long_run_pace]
        team_ref = min(vals) if vals else None
        for f in group:
            if f.long_run_pace is None and team_ref is not None:
                f.long_run_pace = team_ref + 0.25
                f.race_pace_est = f.long_run_pace + FUEL_EFFECT * (START_FUEL_KG - PRACTICE_FUEL_KG)
                f.notes.append("long-run pace imputed from team mate")
                f.sources.append("imputed/teammate")

    # ---- degradation: a car property, so pool by team -----------------------
    # Absolute measured slopes are unreliable (practice fuel loads, track state),
    # but the *ratio* between teams is meaningful. Anchor the field level to a
    # circuit prior and keep only the relative ordering.
    fuel_corr = FUEL_EFFECT * FUEL_BURN_PER_LAP   # slope is de-trended for fuel burn
    measured: dict[str, list[float]] = {}
    for f in feats:
        if f.deg_raw is not None:
            measured.setdefault(f.team, []).append(max(0.01, f.deg_raw + fuel_corr))

    team_med = {t: _median(v) for t, v in measured.items()}
    field_deg = _median([v for v in team_med.values() if v])

    for f in feats:
        tm = team_med.get(f.team)
        if tm and field_deg:
            ratio = max(0.65, min(1.45, tm / field_deg))
            f.deg_rate = round(max(DEG_CLAMP[0], min(DEG_CLAMP[1], deg_prior * ratio)), 4)
            f.deg_source = "measured"
        else:
            f.deg_rate = deg_prior
            f.deg_source = "prior"


def _normalise(feats: list[DriverFeature], has_fastf1: bool = False) -> None:
    """Build relative pace gaps.

    Raw long-run medians are not comparable across drivers because teams run
    different fuel loads, compounds and track states. We therefore compare each
    driver's `long-run median minus own qualifying lap` (the programme offset).
    Fuel and compound effects are common to the field and cancel out; what
    remains is genuine race-trim advantage.
    """
    qs = [f.q_best for f in feats if f.q_best]
    pole = min(qs) if qs else None
    for f in feats:
        if pole and f.q_best:
            f.q_gap = f.q_best - pole
        if f.long_run_pace and not f.race_pace_est:
            f.race_pace_est = f.long_run_pace + FUEL_EFFECT * (START_FUEL_KG - PRACTICE_FUEL_KG)

    offsets: dict[int, float] = {}
    for f in feats:
        if f.long_run_pace and f.q_best:
            offsets[f.number] = f.long_run_pace - f.q_best

    deltas: dict[int, float] = {}
    if offsets:
        med = statistics.median(offsets.values())
        # reject implausible programmes (wet running, traffic, very low fuel)
        clean = {k: v for k, v in offsets.items() if med - 2.0 <= v <= med + 4.0}
        clean = clean or offsets
        med = statistics.median(clean.values())
        for f in feats:
            if f.number in clean:
                d = clean[f.number] - med
                f.notes.append(f"long-run offset {d:+.2f}s vs field median")
            else:
                d = f.q_gap * 0.8
                f.notes.append("long-run offset inferred from qualifying")
            deltas[f.number] = max(LR_CLAMP[0], min(LR_CLAMP[1], d)) * LR_SHRINK

    if deltas:
        lo = min(deltas.values())
        for f in feats:
            f.long_run_gap = max(0.0, deltas.get(f.number, 0.8) - lo)
    else:
        for f in feats:
            f.long_run_gap = 0.6

    for f in feats:
        f.pace_gap = PACE_W_QUALI * f.q_gap + PACE_W_LONG_RUN * f.long_run_gap
        form_adj = max(-0.12, min(0.12, (11.0 - f.driver_form) * 0.010))
        f.pace_gap = max(0.0, f.pace_gap - form_adj)
        base = (pole or 94.0) + RACE_PACE_OFFSET
        f.base_lap = base + f.pace_gap


def _score_confidence(feats: list[DriverFeature]) -> None:
    for f in feats:
        c = 0.55
        if f.q_best:
            c += 0.15
        if f.long_run_pace:
            c += 0.18
        if f.deg_source == "measured":
            c += 0.07
        if f.circuit_starts:
            c += 0.05
        if "imputed" in " ".join(f.sources):
            c -= 0.10
        f.confidence = round(max(0.25, min(0.97, c)), 3)
