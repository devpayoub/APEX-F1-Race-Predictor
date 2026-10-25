#!/usr/bin/env python
"""F1 race predictor — CLI entrypoint.

    python run.py --date 2026-10-25
    python run.py --season 2026 --round 18 --sims 20000
    python run.py --offline
    python run.py --backtest
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

try:
    from src import blend, config, features, ml_model, report, simulate  # noqa: E402
    from src.sources import fastf1_source, jolpica, openf1  # noqa: E402
except ModuleNotFoundError as exc:
    missing = getattr(exc, "name", "a dependency") or "a dependency"
    sys.stderr.write(
        "\n"
        f"  Missing dependency: {missing}\n\n"
        "  This project needs numpy, pandas, scikit-learn and requests, which are\n"
        "  installed into a project-local virtual environment -- not your system Python.\n"
        "\n"
        "  Easiest fix on Windows:\n"
        "      run.bat\n"
        "\n"
        "  Or by hand:\n"
        "      python -m venv .venv\n"
        "      .venv\\Scripts\\activate\n"
        "      pip install -r requirements.txt\n"
        "      python run.py --date YYYY-MM-DD\n"
        "\n"
    )
    raise SystemExit(1)

# --------------------------------------------------------------------- utils --
def _today() -> dt.date:
    return dt.date.today()


def _find_race_round(season: int, target: dt.date | None) -> dict:
    schedule = jolpica.schedule(season)
    if not schedule:
        raise RuntimeError(f"no schedule returned for {season}")
    if target is None:
        return schedule[-1]
    ds = target.isoformat()
    exact = [r for r in schedule if r.get("date") == ds]
    if exact:
        return exact[0]
    upcoming = [r for r in schedule if (r.get("date") or "") >= ds]
    if upcoming:
        return upcoming[0]
    past = [r for r in schedule if (r.get("date") or "") <= ds]
    return past[-1] if past else schedule[-1]


def _sessions_for(season: int, meeting_key: int) -> dict:
    ss = [s for s in openf1.sessions(season) if s.get("meeting_key") == meeting_key]
    out: dict[str, int] = {}
    for s in ss:
        name = (s.get("session_name") or "").strip()
        stype = (s.get("session_type") or "").strip()
        if stype == "Race" and "race" not in out:
            out["race"] = s["session_key"]
        elif stype == "Qualifying" and "quali" not in out:
            out["quali"] = s["session_key"]
        elif stype == "Sprint" and "sprint" not in out:
            out["sprint"] = s["session_key"]
        if name == "Practice 2":
            out["fp2"] = s["session_key"]
        elif name == "Practice 3":
            out["fp3"] = s["session_key"]
        elif name == "Practice 1":
            out["fp1"] = s["session_key"]
    return out


def _meeting_for_round(season: int, race_meta: dict) -> int | None:
    """Map a Jolpica race to an OpenF1 meeting_key.

    OpenF1 meetings start on Friday, so the race date falls *inside* the
    [date_start, date_end] window rather than matching date_start exactly.
    """
    date = race_meta.get("date")
    if not date:
        return None
    day = dt.date.fromisoformat(date)
    loc = ((race_meta.get("Circuit") or {}).get("Location") or {})
    locality = (loc.get("locality") or "").lower()

    fallback = None
    for m in openf1.meetings(season):
        try:
            start = dt.date.fromisoformat((m.get("date_start") or "")[:10])
            end = dt.date.fromisoformat((m.get("date_end") or "")[:10])
        except ValueError:
            continue
        if start <= day <= end:
            return m.get("meeting_key")
        if locality and locality == (m.get("location") or "").lower():
            fallback = fallback or m.get("meeting_key")
    return fallback


def _forecast(lat: float, lon: float, date: str) -> dict:
    try:
        import requests
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": lat, "longitude": lon,
                    "hourly": "temperature_2m,precipitation_probability,wind_speed_10m,relative_humidity_2m",
                    "timezone": "UTC", "start_date": date, "end_date": date},
            timeout=30,
        )
        h = r.json()["hourly"]
        idx = [i for i, t in enumerate(h["time"]) if t.endswith("20:00")] or [0]
        i = idx[0]
        return {"air_temp_c": h["temperature_2m"][i],
                "rain_prob": round((h["precipitation_probability"][i] or 0) / 100.0, 3),
                "wind_kmh": h["wind_speed_10m"][i],
                "humidity": h["relative_humidity_2m"][i],
                "source": "open-meteo"}
    except Exception:  # noqa: BLE001
        return {"source": "unavailable"}


def _merge_practice(*dicts: dict[int, dict]) -> dict[int, dict]:
    merged: dict[int, dict] = {}
    for d in dicts:
        for drv, v in d.items():
            cur = merged.get(drv)
            if cur is None:
                merged[drv] = dict(v)
                continue
            if v.get("pace") and (cur.get("pace") is None or v["pace"] < cur["pace"]):
                cur["pace"] = v["pace"]
                cur["n"] = v.get("n")
                cur["compound"] = v.get("compound")
            if v.get("deg") is not None:
                cur["deg"] = v["deg"] if cur.get("deg") is None else (cur["deg"] + v["deg"]) / 2.0
            if v.get("sigma"):
                cur["sigma"] = v["sigma"]
    return merged


# ---------------------------------------------------------------- core round --
def run_round(season: int, rnd: int, sims: int, target_date: dt.date | None = None,
              quiet: bool = False, prior=None) -> tuple[dict, dict]:
    t0 = time.time()
    race_meta = jolpica.race(season, rnd)
    if not race_meta:
        raise RuntimeError(f"Jolpica has no race for {season} round {rnd}")

    circuit_id = (race_meta.get("Circuit") or {}).get("circuitId") or "unknown"
    circuit = dict(config.CIRCUITS.get(circuit_id, config.DEFAULT_CIRCUIT))
    loc = (race_meta.get("Circuit") or {}).get("Location") or {}

    meeting_key = _meeting_for_round(season, race_meta)
    if meeting_key is None:
        raise RuntimeError("could not map race to an OpenF1 meeting")

    keys = _sessions_for(season, meeting_key)
    if "race" not in keys:
        raise RuntimeError("no race session in OpenF1 for this meeting")

    drivers = openf1.drivers(keys["race"])
    if not drivers:
        raise RuntimeError("no driver list available")

    # ---- qualifying ----
    quali_rows = openf1.session_result(keys["quali"]) if "quali" in keys else []
    quali = features.extract_quali(quali_rows, drivers)

    # ---- official grid (published shortly before lights out) ----
    grid_map: dict[int, int] = {}
    for g in openf1.starting_grid(keys["race"]):
        num, pos = g.get("driver_number"), g.get("position")
        if num is not None and pos:
            grid_map[int(num)] = int(pos)

    # ---- practice long runs ----
    practice: dict[int, dict] = {}
    for tag in ("fp2", "fp3", "fp1"):
        if tag not in keys:
            continue
        practice = _merge_practice(
            practice,
            features.extract_practice_pace(openf1.laps(keys[tag]), openf1.stints(keys[tag])),
        )
        if tag == "fp2":
            continue

    # ---- priors ----
    form = features.rolling_form(jolpica.season_results(season))
    circ_drv, circ_team = features.circuit_history(jolpica.circuit_results(circuit_id))

    feats = features.build_features(drivers, quali, practice, grid_map, form,
                                    circ_drv, circ_team,
                                    deg_prior=circuit.get("deg_prior", config.DEG_PRIOR))

    # ---- conditions ----
    wx = openf1.weather(keys["race"])
    if wx:
        last = wx[-1]
        conditions = {
            "air_temp_c": last.get("air_temperature"),
            "track_temp_c": last.get("track_temperature"),
            "rain_prob": 0.0 if not last.get("rainfall") else 1.0,
            "wind_kmh": round((last.get("wind_speed") or 0) * 3.6, 1),
            "humidity": last.get("humidity"),
            "source": "openf1",
        }
    else:
        conditions = _forecast(float(loc.get("lat") or 0), float(loc.get("long") or 0),
                               race_meta.get("date") or _today().isoformat())
        conditions["track_temp_c"] = (conditions.get("air_temp_c") or 25) + 13.0
    conditions["sc_probability"] = circuit["sc_prob"]
    conditions["expected_strategy"] = "1-stop M-H"

    # ---- simulate ----
    sim = simulate.simulate(feats, circuit, n_sims=sims)

    # ---- ML prior ----
    if prior is None:
        prior = ml_model.train([2022, 2023, 2024, 2025, 2026])
    total_rounds = len(jolpica.schedule(season)) or 24
    buckets = ml_model.predict(prior, feats, int(race_meta.get("round") or rnd), total_rounds)

    blended = blend.blend(feats, sim, buckets)

    avg_conf = sum(f.confidence for f in feats) / len(feats)
    model_confidence = "high" if avg_conf > 0.78 else ("medium" if avg_conf > 0.6 else "low")

    f1_status = fastf1_source.status(season, rnd)

    race_payload = {
        "name": race_meta.get("raceName"),
        "season": int(race_meta.get("season") or season),
        "round": int(race_meta.get("round") or rnd),
        "circuit": circuit["name"],
        "circuit_id": circuit_id,
        "location": f"{loc.get('locality', circuit['locality'])}, {loc.get('country', circuit['country'])}",
        "laps": circuit["laps"],
        "circuit_length_km": circuit["length_km"],
        "start_utc": f"{race_meta.get('date')}T{race_meta.get('time') or '00:00:00Z'}",
        "session_key": keys["race"],
        "meeting_key": meeting_key,
    }

    notes = [
        f"Race pace from {len([f for f in feats if f.long_run_pace])}/{len(feats)} drivers "
        f"with clean long-run data (OpenF1 practice laps + stint compounds).",
        "Monte Carlo: lap-by-lap, dirty-air blocking, 1/2-stop strategy variance, "
        "safety-car bunching and per-driver DNF hazard.",
        "ML prior: HistGradientBoosting on 2022-2026 race results (grid, form, circuit affinity).",
        "Fuel correction 0.030 s/kg; race offset +2.6 s over qualifying pace.",
    ]
    if not f1_status["loaded"]:
        notes.append(f"FastF1 unavailable: {f1_status['reason']}")
    if not grid_map:
        notes.append("Official starting grid not yet published — grid derived from qualifying classification.")

    payload = report.build(
        feats=feats, blended=blended, sim=sim, race=race_payload, circuit=circuit,
        conditions=conditions,
        model_info={
            "simulations": sims,
            "confidence": model_confidence,
            "ml_rows": prior.n_rows,
            "ml_cv_logloss": round(prior.cv_logloss, 4) if prior.cv_logloss else None,
            "fastf1": f1_status,
        },
        sources=["OpenF1", "Jolpica F1", "Open-Meteo"] + (["FastF1"] if f1_status["loaded"] else []),
        notes=notes,
    )

    diag = {
        "elapsed": round(time.time() - t0, 1),
        "drivers": len(feats),
        "quali_rows": len(quali_rows),
        "practice_drivers": len([f for f in feats if f.long_run_pace]),
        "grid_published": bool(grid_map),
        "ml_ok": prior.ok,
        "ml_rows": prior.n_rows,
        "ml_cv_logloss": prior.cv_logloss,
        "fastf1": f1_status,
        "sanity": blended["sanity"],
    }
    return payload, diag


# ------------------------------------------------------------------- display --
def print_table(payload: dict) -> None:
    print()
    race = payload["meta"]["race"]
    print(f"  {race['name']} {race['season']}  ·  Round {race['round']}")
    print(f"  {race['circuit']} — {race['location']}  ·  {race['laps']} laps")
    print(f"  Model confidence: {payload['meta']['model']['confidence'].upper()}"
          f"   ·   {payload['meta']['model']['simulations']:,} simulations")
    cond = payload["meta"]["conditions"]
    print(f"  Air {cond.get('air_temp_c')}°C  ·  rain {int((cond.get('rain_prob') or 0) * 100)}%"
          f"  ·  SC {int(cond.get('sc_probability', 0) * 100)}%")
    print()
    print(f"  {'P':>2}  {'DRV':<4}{'TEAM':<20}{'GRID':>5}{'WIN%':>7}{'POD%':>7}"
          f"{'PTS%':>7}{'EXP':>7}{'GAP':>8}  STRATEGY")
    print("  " + "-" * 96)
    for d in payload["drivers"]:
        gap = "-" if d["expected_gap_to_winner"] is None else f"+{d['expected_gap_to_winner']:.1f}"
        print(f"  {d['position']:>2}  {d['code']:<4}{d['team'][:19]:<20}{d['grid']:>5}"
              f"{d['win_prob'] * 100:>6.1f}{d['podium_prob'] * 100:>7.1f}"
              f"{d['points_prob'] * 100:>7.1f}{d['expected_finish']:>7.2f}{gap:>8}  {d['strategy']}")
    print()


# ------------------------------------------------------------------ backtest --
def backtest(season: int, sims: int = 2500) -> dict:
    schedule = jolpica.schedule(season)
    today = _today().isoformat()

    # Train the prior on seasons strictly before the one being replayed, so every
    # round is genuinely out-of-sample for the prior component.
    prior_seasons = [y for y in (2022, 2023, 2024, 2025) if y < season]
    print(f"  training prior on {prior_seasons} ...")
    prior = ml_model.train(prior_seasons) if prior_seasons else None
    if prior is not None and not prior.ok:
        prior = None

    results = []
    for r in schedule:
        if (r.get("date") or "") >= today:
            continue
        rnd = int(r["round"])
        try:
            payload, _ = run_round(season, rnd, sims, quiet=True, prior=prior)
        except Exception as exc:  # noqa: BLE001
            print(f"  skip R{rnd}: {exc}")
            continue
        actual_rows = jolpica.race_results(season, rnd)
        actual = {}
        for res in actual_rows:
            pos = res.get("position")
            code = (res.get("Driver") or {}).get("code")
            if code and str(pos).isdigit():
                actual[code] = int(pos)
        if not actual:
            continue
        errs, brier = [], 0.0
        probs = {}
        for d in payload["drivers"]:
            if d["code"] in actual:
                errs.append(abs(d["expected_finish"] - actual[d["code"]]))
                brier += (d["win_prob"] - (1.0 if actual[d["code"]] == 1 else 0.0)) ** 2
            probs[d["code"]] = d["win_prob"]
        top3 = [d["code"] for d in payload["drivers"][:3]]
        winner = min(actual, key=actual.get)
        results.append({
            "round": rnd, "race": r.get("raceName"),
            "mae": sum(errs) / len(errs) if errs else None,
            "brier": brier,
            "winner": winner,
            "winner_in_top3": winner in top3,
            "predicted_top3": top3,
            "probs": probs,
        })
        print(f"  R{rnd:<3} {str(r.get('raceName'))[:34]:<36} MAE={results[-1]['mae']:.2f}  "
              f"winner={winner}  top3hit={results[-1]['winner_in_top3']}")

    if not results:
        return {"rounds": 0}

    # ---- calibration sweep ---------------------------------------------------
    # Temperature scaling fixes the favourite but distorts the tail (3% -> 10%).
    # Shrinking toward the uniform distribution softens the leader while keeping
    # the relative shape of the field, so that is what we sweep.
    import numpy as np
    sweep = []
    for a in (0.0, 0.08, 0.16, 0.24, 0.32, 0.40, 0.50):
        b = 0.0
        for res in results:
            codes = list(res["probs"].keys())
            p = np.array([res["probs"][c] for c in codes], dtype=float)
            q = (1.0 - a) * p + a / len(p)
            q = q / q.sum()
            y = np.array([1.0 if c == res["winner"] else 0.0 for c in codes])
            b += float(((q - y) ** 2).sum())
        sweep.append({"shrink_to_uniform": a, "brier": round(b / len(results), 4)})

    best = min(sweep, key=lambda s: s["brier"])
    print("\n  calibration sweep (shrinkage toward uniform):")
    for s in sweep:
        mark = "  <-- best" if s is best else ""
        print(f"    a={s['shrink_to_uniform']:<5} Brier={s['brier']}{mark}")

    out = {
        "season": season,
        "rounds": len(results),
        "prior_seasons": prior_seasons,
        "mae": round(sum(x["mae"] for x in results if x["mae"]) /
                     max(1, len([x for x in results if x["mae"]])), 3),
        "brier": round(sum(x["brier"] for x in results) / len(results), 4),
        "top3_hit_rate": round(sum(1 for x in results if x["winner_in_top3"]) / len(results), 3),
        "calibration_sweep": sweep,
        "suggested_shrinkage": best["shrink_to_uniform"],
        "detail": [{k: v for k, v in r.items() if k != "probs"} for r in results],
    }
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (config.OUTPUT_DIR / "backtest.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\n  season MAE={out['mae']}  Brier={out['brier']}  "
          f"winner-in-top3={out['top3_hit_rate'] * 100:.0f}%  ({out['rounds']} rounds)")
    return out


# ---------------------------------------------------------------------- main --
def main() -> int:
    ap = argparse.ArgumentParser(description="F1 race predictor")
    ap.add_argument("--date", help="race date YYYY-MM-DD (default: today)")
    ap.add_argument("--season", type=int, help="season year")
    ap.add_argument("--round", type=int, help="round number")
    ap.add_argument("--sims", type=int, default=config.DEFAULT_SIMS)
    ap.add_argument("--offline", action="store_true", help="use cache only")
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--emit-sample", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if args.backtest:
        season = args.season or _today().year
        print(f"  backtesting {season} ...")
        backtest(season, args.sims)
        return 0

    if args.offline:
        from src import cache
        cache.OFFLINE = True
        print("  offline mode: cache only, TTL ignored")

    target = dt.date.fromisoformat(args.date) if args.date else _today()
    season = args.season or target.year

    if args.round:
        rnd = args.round
    else:
        rnd = int(_find_race_round(season, target)["round"])

    print(f"  resolving {season} round {rnd} ...")
    payload, diag = run_round(season, rnd, args.sims, target)

    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else config.OUTPUT_DIR / "prediction.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    # the dashboard loads this with a plain <script> tag so it also works from
    # file:// without a server or CORS workarounds
    config.DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    (config.DASHBOARD_DIR / "prediction.js").write_text(
        "window.PREDICTION = " + json.dumps(payload, ensure_ascii=False) + ";\n",
        encoding="utf-8")

    print_table(payload)
    print(f"  sanity: {diag['sanity']}")
    print(f"  sources: {', '.join(payload['meta']['sources'])}")
    f1 = diag.get("fastf1") or {}
    if f1.get("loaded"):
        print("  FastF1 : contributing telemetry")
    else:
        print("  FastF1 : skipped — F1's timing feed needs an account, so OpenF1 is used "
              "instead (no credentials required)")
    print(f"  elapsed {diag['elapsed']}s  ->  {out_path}")

    if args.emit_sample:
        sample = config.OUTPUT_DIR / "prediction.sample.json"
        sample.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  sample  ->  {sample}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
