"""Optional FastF1 enrichment layer.

F1's live-timing feed now sits behind an F1 account (HTTP 401 on
``livetiming.formula1.com/static/2026/Index.json``). FastF1 3.9 therefore probes for
optional ``F1_USERNAME`` / ``F1_PASSWORD`` environment variables on every session
load and logs a warning when they are absent.

We do not want an F1 account: OpenF1 already supplies equivalent lap times, stint
boundaries and compounds. So this module

* silences FastF1's logging completely (it is chatty and its credential messages
  read like an error, which they are not),
* caches the "not available" verdict so later runs skip the probe entirely instead
  of paying a network round trip to fail again.
"""
from __future__ import annotations

import logging
import warnings

from .. import cache as _cache
from ..config import CACHE_DIR

warnings.filterwarnings("ignore")

PROBE_TTL = 6 * 3600          # re-check twice a day, in case F1 opens the feed

_ready = False
_import_ok = False


def _silence() -> None:
    """Disable every FastF1 logger, including ones created after import."""
    names = {"fastf1"}
    names.update(n for n in logging.Logger.manager.loggerDict if n.startswith("fastf1"))
    for name in names:
        lg = logging.getLogger(name)
        lg.disabled = True
        lg.setLevel(logging.CRITICAL)
        lg.propagate = False
        lg.handlers = [logging.NullHandler()]


def _init() -> bool:
    global _ready, _import_ok
    if _ready:
        return _import_ok
    _ready = True
    try:
        import fastf1
        _silence()
        cache = CACHE_DIR / "fastf1"
        cache.mkdir(parents=True, exist_ok=True)
        fastf1.Cache.enable_cache(str(cache))
        _import_ok = True
    except Exception:  # noqa: BLE001
        _import_ok = False
    return _import_ok


def available() -> bool:
    return _init()


def load_session(season: int, rnd: int, ident: str):
    """Return a loaded FastF1 session, or None if the feed is unavailable."""
    if not _init():
        return None
    try:
        import fastf1
        _silence()
        ses = fastf1.get_session(season, rnd, ident)
        ses.load(laps=True, telemetry=False, weather=False, messages=False)
        if ses.laps is None or len(ses.laps) == 0:
            return None
        return ses
    except Exception:  # noqa: BLE001
        return None


def event_name(season: int, rnd: int) -> str | None:
    if not _init():
        return None
    try:
        import fastf1
        _silence()
        return str(fastf1.get_event(season, rnd)["EventName"])
    except Exception:  # noqa: BLE001
        return None


def status(season: int, rnd: int) -> dict:
    """Report whether FastF1 contributes, caching the answer for PROBE_TTL."""
    key = f"probe:{season}:{rnd}"
    hit = _cache.load("fastf1_probe", key, PROBE_TTL)
    if hit is not None:
        return hit

    ses = load_session(season, rnd, "Q")
    if ses is None:
        out = {
            "available": available(),
            "loaded": False,
            "reason": "F1's timing feed requires an F1 account (HTTP 401); "
                      "OpenF1 supplies laps, stints and compounds instead. "
                      "No credentials needed.",
        }
    else:
        out = {"available": True, "loaded": True,
               "reason": f"loaded {len(ses.laps)} laps from FastF1"}

    _cache.save("fastf1_probe", key, out)
    return out
