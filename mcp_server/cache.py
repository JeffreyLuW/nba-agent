"""Small helpers that protect NBA.com from us (and us from NBA.com).

Concept: **caching**. stats.nba.com is free, but it blocks clients that call it
too often. Most NBA data changes slowly (a 2015-16 stat line never changes), so
we keep each answer in memory for a while and reuse it.

Two tools here:

- `ttl_cache(seconds)`: remember a function's result for `seconds`.
  "TTL" means "time to live".
- `throttle()`: wait a little between two real calls to NBA.com.

The cache lives in memory, so it is empty again after a restart. That is fine
for now. Later we could move it to Postgres or Redis.
"""

import functools
import threading
import time

# Every cached function, so tests can clear them all (see tests/conftest.py).
_ALL_CACHES: list = []

# Smallest gap between two calls to stats.nba.com, in seconds.
MIN_SECONDS_BETWEEN_CALLS = 0.6
_last_call = 0.0
_lock = threading.Lock()


def ttl_cache(seconds: float):
    """Decorator: cache a function's result for `seconds`.

    Errors are NOT cached: if the call raises, the next call tries again.
    """

    def decorator(fn):
        store: dict = {}  # key -> (time_saved, result)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            hit = store.get(key)
            if hit and time.monotonic() - hit[0] < seconds:
                return hit[1]
            result = fn(*args, **kwargs)
            store[key] = (time.monotonic(), result)
            return result

        wrapper.cache_clear = store.clear
        _ALL_CACHES.append(wrapper)
        return wrapper

    return decorator


def clear_all_caches() -> None:
    for cached in _ALL_CACHES:
        cached.cache_clear()


def throttle() -> None:
    """Sleep if the last NBA.com call was less than MIN_SECONDS_BETWEEN_CALLS ago."""
    global _last_call
    with _lock:
        wait = MIN_SECONDS_BETWEEN_CALLS - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()
