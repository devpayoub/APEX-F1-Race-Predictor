"""Assemble the JSON contract consumed by the dashboard."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np

from . import config
from .config import (BLEND_MC, BLEND_ML, CALIBRATION_T, MODEL_VERSION,
                     COMPOUND_COLOUR)
from .features import DriverFeature


def _fmt_lap(seconds: float | None) -> str | None:
    if not seconds or not np.isfinite(seconds):
        return None
    m = int(seconds // 60)
    s = seconds - m * 60
    return f"{m}:{s:06.3f}"


def _narrative(feats: list[DriverFeature], ranked: list[DriverFeature],
               blended: dict, circuit: dict, conditions: dict) -> list[str]:
    """Three plain-language sentences that read the model back to the user."""
    out: list[str] = []
    if len(ranked) < 3:
        return out
    p1, p2, p3 = ranked[0], ranked[1], ranked[2]

    edge = p2.pace_gap - p1.pace_gap
    if p1.expected_gap is not None and p2.expected_gap is not None:
        margin = f"{max(0.0, p2.expected_gap - p1.expected_gap):.1f} s"
    else:
        margin = "—"
    out.append(
        f"{p1.code} starts from P{p1.grid} and holds the model's best race-trim pace, "
        f"{edge:+.2f} s/lap versus {p2.code}. Projected margin over {p2.code} at the flag: {margin}."
    )

    gap2 = p2.pace_gap - p1.pace_gap
    trend = "quicker" if gap2 < 0 else "slower"
    out.append(
        f"{p2.code} is the closest threat at {p2.win_prob * 100:.1f}% to win — "
        f"{trend} than the leader by {abs(gap2):.2f} s/lap in race trim from P{p2.grid}. "
        f"Track position is worth about {config.GRID_GAP:.1f} s per grid slot here, "
        f"so the run to turn one matters as much as raw pace."
    )

    sc = conditions.get("sc_probability", 0)
    deg_spread = max(f.deg_rate for f in feats) - min(f.deg_rate for f in feats)
    low_deg = min(feats, key=lambda f: f.deg_rate)
    out.append(
        f"Swing factors: a {sc * 100:.0f}% safety-car chance that bunches the field and wipes out "
        f"track position, and a {deg_spread:.3f} s/lap spread in tyre degradation that favours "
        f"{low_deg.team} over a long final stint. {p3.code} completes the projected podium at "
        f"{p3.podium_prob * 100:.1f}%."
    )
    return out


def build(
    feats: list[DriverFeature],
    blended: dict,
    sim,
    race: dict,
    circuit: dict,
    conditions: dict,
    model_info: dict,
    sources: list[str],
    notes: list[str],
    generated_at: str | None = None,
) -> dict[str, Any]:
    order = list(blended["order"])
    ranked = [feats[i] for i in order]

    drivers = []
    for pos, f in enumerate(ranked, start=1):
        drivers.append({
            "position": pos,
            "code": f.code,
            "number": f.number,
            "name": f.name,
            "team": f.team,
            "team_colour": f.team_colour,
            "grid": f.grid,
            "grid_delta": f.grid - pos,
            "qualifying_time": _fmt_lap(f.q_best),
            "qualifying_gap": round(float(f.q_gap), 3),
            "qualifying_segment": f.q_segment,
            "win_prob": round(f.win_prob, 4),
            "podium_prob": round(f.podium_prob, 4),
            "points_prob": round(f.points_prob, 4),
            "expected_finish": round(f.expected_finish, 2),
            "finish_band": list(f.finish_band),
            "expected_gap_to_winner": f.expected_gap,
            "race_pace_gap": round(float(f.long_run_gap), 3),
            "race_pace_est": _fmt_lap(f.race_pace_est),
            "base_lap": round(float(f.base_lap), 3),
            "deg_rate": round(float(f.deg_rate), 4),
            "strategy": f.strategy,
            "dnf_prob": round(f.dnf_prob, 4),
            "contributions": f.contributions,
            "confidence": f.model_confidence,
            "win_prob_mc": round(f.win_prob_mc, 4),
            "win_prob_ml": round(f.win_prob_ml, 4),
            "sources": f.sources,
            "notes": f.notes,
        })

    # position probability matrix (rows = driver, cols = finishing position)
    pos_matrix = []
    for i in order:
        row = sim.pos_dist[i]
        pos_matrix.append({
            "code": feats[i].code,
            "probabilities": [round(float(x), 4) for x in row],
        })

    pace_ranking = sorted(
        ({"code": f.code, "team": f.team, "team_colour": f.team_colour,
          "long_run": round(float(f.race_pace_est), 3) if f.race_pace_est else None,
          "quali": round(float(f.q_best), 3) if f.q_best else None,
          "long_run_gap": round(float(f.long_run_gap), 3),
          "quali_gap": round(float(f.q_gap), 3),
          "blended_gap": round(float(f.pace_gap), 3),
          "deg_rate": round(float(f.deg_rate), 4)} for f in feats),
        key=lambda x: x["blended_gap"],
    )

    strategy_matrix = [
        {"code": f.code, "one_stop": "1-stop M-H", "two_stop": "2-stop M-M-S",
         "optimal": f.strategy,
         "two_stop_probability": round(float(sim.two_stop_share[i]), 3)}
        for i, f in enumerate(feats)
    ]

    top3 = [feats[i].code for i in blended.get("contenders", order[:3])]
    fav_i = int(blended.get("contenders", order[:1])[0])
    return {
        "meta": {
            "generated_at": generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "race": race,
            "model": {
                "version": MODEL_VERSION,
                "simulations": int(model_info.get("simulations", 0)),
                "blend": {"monte_carlo": BLEND_MC, "ml": BLEND_ML},
                "calibration_temperature": CALIBRATION_T,
                "confidence": model_info.get("confidence", "medium"),
                "ml_rows": model_info.get("ml_rows"),
                "ml_cv_logloss": model_info.get("ml_cv_logloss"),
                "sanity": blended["sanity"],
                **{k: v for k, v in model_info.items() if k in ("fastf1", "backtest_mae")},
            },
            "conditions": conditions,
            "sources": sources,
        },
        "favourite": {"code": feats[fav_i].code, "win_prob": round(feats[fav_i].win_prob, 4)},
        "podium_contenders": top3,
        "analysis": _narrative(feats, ranked, blended, circuit, conditions),
        "drivers": drivers,
        "pace_ranking": pace_ranking,
        "strategy_matrix": strategy_matrix,
        "position_distribution": pos_matrix,
        "compounds": COMPOUND_COLOUR,
        "methodology_notes": notes,
    }
