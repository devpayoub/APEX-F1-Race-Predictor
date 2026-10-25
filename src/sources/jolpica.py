"""Jolpica-F1 client (Ergast-compatible) — standings, history, ML corpus."""
from __future__ import annotations

from typing import Any

import requests

from .. import cache
from ..config import HTTP_RETRIES, HTTP_TIMEOUT, USER_AGENT

BASE = "https://api.jolpi.ca/ergast/f1"
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})


def _raw(path: str, params: dict[str, Any] | None = None) -> Any:
    last: Exception | None = None
    for attempt in range(HTTP_RETRIES):
        try:
            r = _session.get(f"{BASE}/{path}", params=params, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            last = exc
            import time
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"Jolpica {path} failed: {last}")


def _mrdata(path: str, ttl: float = 21600, **params) -> dict:
    key = path + repr(sorted(params.items()))
    val, _ = cache.cached("jolpica", key, ttl, lambda: _raw(path, params or None))
    return (val or {}).get("MRData", {})


def _all_races(path: str, ttl: float = 21600, max_pages: int = 40) -> list[dict]:
    """Jolpica caps a page at 100 rows; walk `offset` and merge by (season, round)."""
    merged: dict[tuple[str, str], dict] = {}
    offset = 0
    for _ in range(max_pages):
        data = _mrdata(path, ttl, limit=100, offset=offset)
        races = (data.get("RaceTable") or {}).get("Races") or []
        if not races:
            break
        for r in races:
            key = (str(r.get("season")), str(r.get("round")))
            if key not in merged:
                base = dict(r)
                base["Results"] = list(r.get("Results") or [])
                merged[key] = base
            else:
                merged[key]["Results"].extend(r.get("Results") or [])
        total = int(data.get("total") or 0)
        offset += 100
        if offset >= total:
            break
    def _k(key):
        s, rd = key
        try:
            return (int(s), int(rd))
        except (TypeError, ValueError):
            return (0, 0)
    return [merged[k] for k in sorted(merged, key=_k)]


def schedule(season: int, ttl: float = 21600) -> list[dict]:
    return _mrdata(f"{season}.json", ttl, limit=100).get("RaceTable", {}).get("Races", [])


def race(season: int, rnd: int, ttl: float = 21600) -> dict:
    races = _mrdata(f"{season}/{rnd}.json", ttl).get("RaceTable", {}).get("Races", [])
    return races[0] if races else {}


def qualifying(season: int, rnd: int, ttl: float = 3600) -> list[dict]:
    races = _mrdata(f"{season}/{rnd}/qualifying.json", ttl, limit=100).get("RaceTable", {}).get("Races", [])
    return races[0].get("QualifyingResults", []) if races else []


def race_results(season: int, rnd: int, ttl: float = 21600) -> list[dict]:
    races = _mrdata(f"{season}/{rnd}/results.json", ttl, limit=100).get("RaceTable", {}).get("Races", [])
    return races[0].get("Results", []) if races else []


def driver_standings(season: int, ttl: float = 3600) -> list[dict]:
    lists = _mrdata(f"{season}/driverstandings.json", ttl, limit=100).get("StandingsTable", {}).get("StandingsLists", [])
    return lists[0].get("DriverStandings", []) if lists else []


def constructor_standings(season: int, ttl: float = 3600) -> list[dict]:
    lists = _mrdata(f"{season}/constructorstandings.json", ttl, limit=100).get("StandingsTable", {}).get("StandingsLists", [])
    return lists[0].get("ConstructorStandings", []) if lists else []


def season_results(season: int, ttl: float = 21600) -> list[dict]:
    """All race results of a season, one entry per Grand Prix."""
    return _all_races(f"{season}/results.json", ttl)


def circuit_winners(circuit_id: str, ttl: float = 86400) -> list[dict]:
    return _mrdata(f"circuits/{circuit_id}/results/1.json", ttl, limit=100).get("RaceTable", {}).get("Races", [])


def circuit_results(circuit_id: str, ttl: float = 86400) -> list[dict]:
    """Every race held at a circuit, with the full classification."""
    return _all_races(f"circuits/{circuit_id}/results.json", ttl)
