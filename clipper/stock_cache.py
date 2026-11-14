"""24-hour on-disk stock search cache, adapted from MoneyPrinterTurbo's cache design.

No API keys are stored. Corrupt/expired entries are misses, not fatal errors.
"""
import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path

import config

TTL_SECONDS = 24 * 60 * 60
CACHE_DIR = config.DATA_DIR / "cache_stock"
_LOCKS = tuple(threading.Lock() for _ in range(32))


def _key(provider, query, filters):
    canonical = json.dumps(
        {"provider": provider.lower(), "query": " ".join(query.lower().split()),
         "filters": filters}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _path(provider, query, filters):
    return CACHE_DIR / (_key(provider, query, filters) + ".json")


def _lock(provider, query, filters):
    return _LOCKS[int(_key(provider, query, filters)[:8], 16) % len(_LOCKS)]


def load(provider, query, filters, now=None):
    path = _path(provider, query, filters)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        timestamp = payload["created_at"]
        if (payload.get("version") != 1 or
                not isinstance(timestamp, (int, float)) or
                not 0 <= (time.time() if now is None else now) - timestamp < TTL_SECONDS or
                not isinstance(payload.get("results"), list)):
            return None
        return payload["results"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save(provider, query, filters, results, now=None):
    """Never cache empty search results or failures."""
    if not results:
        return
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _path(provider, query, filters)
    temp = CACHE_DIR / ("." + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_text(json.dumps({
            "version": 1,
            "created_at": time.time() if now is None else now,
            "results": results,
        }), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def cached_search(provider, query, filters, search_fn):
    """Coalesce identical concurrent lookups within the running app."""
    try:
        results = load(provider, query, filters)
        if results is not None:
            return results, True
    except OSError:
        pass
    with _lock(provider, query, filters):
        try:
            results = load(provider, query, filters)
            if results is not None:
                return results, True
        except OSError:
            pass
        results = search_fn()
        try:
            save(provider, query, filters, results)
        except OSError:
            pass  # Cache is an optimization, never an outage.
        return results, False
