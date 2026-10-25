"""Tiny JSON snapshot cache so every run is reproducible offline."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from .config import CACHE_DIR

OFFLINE = False          # set by run.py --offline; ignores TTL and never refetches


def _key(parts: str) -> str:
    return hashlib.sha1(parts.encode("utf-8")).hexdigest()[:16]


def path_for(namespace: str, parts: str) -> Path:
    d = CACHE_DIR / namespace
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{_key(parts)}.json"


def load(namespace: str, parts: str, ttl: float | None) -> Any | None:
    p = path_for(namespace, parts)
    if not p.exists():
        return None
    if ttl is not None and not OFFLINE and (time.time() - p.stat().st_mtime) > ttl:
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save(namespace: str, parts: str, payload: Any) -> None:
    p = path_for(namespace, parts)
    try:
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def cached(namespace: str, parts: str, ttl: float | None, producer):
    """Return cached value if fresh, else call producer() and persist."""
    hit = load(namespace, parts, ttl)
    if hit is not None:
        return hit, "cache"
    value = producer()
    save(namespace, parts, value)
    return value, "live"
