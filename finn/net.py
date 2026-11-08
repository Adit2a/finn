"""Polite HTTP + a tiny on-disk cache.

Everything FINN fetches is public and fetched with plain requests at a gentle
rate. Immutable things (PDFs, XBRL, LLM answers) are cached forever; pages
that change (screener, announcement lists) get a short TTL.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache"
CACHE.mkdir(exist_ok=True)

UA = {"User-Agent": "FINN-prototype/0.1 (investor research demo)", "Accept": "*/*"}
_client = httpx.Client(headers=UA, timeout=60, follow_redirects=True)
_last_hit: dict[str, float] = {}
MIN_GAP = 0.6  # seconds between hits to the same host


def _key(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:20]


def _throttle(url: str) -> None:
    host = httpx.URL(url).host
    gap = time.time() - _last_hit.get(host, 0)
    if gap < MIN_GAP:
        time.sleep(MIN_GAP - gap)
    _last_hit[host] = time.time()


class NotFound(RuntimeError):
    pass


def get_bytes(url: str, params: dict | None = None, ttl: float | None = None) -> bytes:
    """GET with disk cache. ttl=None means cache forever."""
    path = CACHE / "http" / (_key(url, json.dumps(params or {}, sort_keys=True)))
    path.parent.mkdir(exist_ok=True)
    if path.exists() and (ttl is None or time.time() - path.stat().st_mtime < ttl):
        return path.read_bytes()
    last_err = None
    for attempt in range(3):
        try:
            _throttle(url)
            r = _client.get(url, params=params)
            if r.status_code in (400, 404, 410):  # it isn't there: no point retrying
                raise NotFound(f"{r.status_code}: {url}")
            r.raise_for_status()
            path.write_bytes(r.content)
            return r.content
        except NotFound:
            raise
        except Exception as e:  # network hiccups: back off, then fall back to stale cache
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    if path.exists():
        return path.read_bytes()
    raise RuntimeError(f"fetch failed: {url} ({last_err})")


def get_json(url: str, params: dict | None = None, ttl: float | None = 900):
    return json.loads(get_bytes(url, params, ttl))


def get_text(url: str, params: dict | None = None, ttl: float | None = 900) -> str:
    return get_bytes(url, params, ttl).decode("utf-8", errors="replace")


def memo_json(namespace: str, key: str, fn, ttl: float | None = None):
    """Cache the JSON-serialisable result of fn() under namespace/key."""
    path = CACHE / namespace / f"{_key(key)}.json"
    path.parent.mkdir(exist_ok=True)
    if path.exists() and (ttl is None or time.time() - path.stat().st_mtime < ttl):
        return json.loads(path.read_text(encoding="utf-8"))
    val = fn()
    path.write_text(json.dumps(val, ensure_ascii=False), encoding="utf-8")
    return val
