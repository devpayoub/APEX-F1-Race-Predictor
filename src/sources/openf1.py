"""OpenF1 client — live timing, results, laps, stints, weather, race control."""
from __future__ import annotations

import time
from typing import Any

import requests

from .. import cache
from ..config import HTTP_RETRIES, HTTP_TIMEOUT, OPENF1_THROTTLE, USER_AGENT

BASE = "https://api.openf1.org/v1"
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
_last_call = [0.0]


def _raw(endpoint: str, params: dict[str, Any] | None = None) -> Any:
    url = f"{BASE}/{endpoint}"
    last_err: Exception | None = None
    for attempt in range(HTTP_RETRIES):
        gap = time.time() - _last_call[0]
        if gap < OPENF1_THROTTLE:
            time.sleep(OPENF1_THROTTLE - gap)
        try:
            r = _session.get(url, params=params, timeout=HTTP_TIMEOUT)
            _last_call[0] = time.time()
            if r.status_code == 404:          # OpenF1 404s when a session has no rows yet
                return []
            if r.status_code == 429:
                time.sleep(2.0 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            _last_call[0] = time.time()
            time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"OpenF1 {endpoint} failed: {last_err}")


def get(endpoint: str, params: dict[str, Any] | None = None, ttl: float | None = 1800) -> list:
    key = endpoint + repr(sorted((params or {}).items()))
    try:
        val, _src = cache.cached("openf1", key, ttl, lambda: _raw(endpoint, params))
    except RuntimeError:
        return []
    if isinstance(val, dict):        # error envelope
        return []
    return val or []


# ---------------------------------------------------------------- sessions ---
def sessions(year: int, ttl: float = 1800) -> list[dict]:
    return get("sessions", {"year": year}, ttl)


def meetings(year: int, ttl: float = 1800) -> list[dict]:
    return get("meetings", {"year": year}, ttl)


def drivers(session_key: int, ttl: float = 86400) -> list[dict]:
    return get("drivers", {"session_key": session_key}, ttl)


def session_result(session_key: int, ttl: float = 900) -> list[dict]:
    return get("session_result", {"session_key": session_key}, ttl)


def laps(session_key: int, ttl: float = 86400) -> list[dict]:
    return get("laps", {"session_key": session_key}, ttl)


def stints(session_key: int, ttl: float = 86400) -> list[dict]:
    return get("stints", {"session_key": session_key}, ttl)


def starting_grid(session_key: int, ttl: float = 300) -> list[dict]:
    return get("starting_grid", {"session_key": session_key}, ttl)


def pit(session_key: int, ttl: float = 86400) -> list[dict]:
    return get("pit", {"session_key": session_key}, ttl)


def weather(session_key: int, ttl: float = 600) -> list[dict]:
    return get("weather", {"session_key": session_key}, ttl)


def race_control(session_key: int, ttl: float = 600) -> list[dict]:
    return get("race_control", {"session_key": session_key}, ttl)


def intervals(session_key: int, ttl: float = 120) -> list[dict]:
    return get("intervals", {"session_key": session_key}, ttl)


def positions(session_key: int, ttl: float = 120) -> list[dict]:
    return get("position", {"session_key": session_key}, ttl)
