"""
In-memory request cache for Canvas API responses.

Caches raw JSON responses keyed by (url, frozen_params) so that
identical requests within a single audit run are never made twice.
This is a read-through cache — it never invalidates entries, because
Canvas data is treated as stable for the duration of one audit run.

The cache is intentionally simple:
  - In-memory only (no disk, no TTL)
  - Scoped to a single audit run (create a new instance per run)
  - Async-safe (asyncio is single-threaded; no locking needed)
  - Optional — CanvasClient works without it

Usage
-----
    cache = RequestCache()

    async with httpx.AsyncClient() as http:
        client = CanvasClient(
            base_url=settings.canvas_base_url,
            token=settings.canvas_token,
            http=http,
            cache=cache,
        )
        # Subsequent calls to the same URL return cached data.
        courses = await client.get_paginated_json("/api/v1/courses")
        courses_again = await client.get_paginated_json("/api/v1/courses")
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class RequestCache:
    """
    Async-safe in-memory cache for Canvas API JSON responses.

    Keyed by (url, params_tuple) where params_tuple is a sorted,
    hashable representation of the query parameters dict.

    Attributes
    ----------
    hits:
        Number of cache hits since construction. Useful for logging
        and diagnostics at the end of a run.
    misses:
        Number of cache misses since construction.
    """

    def __init__(self) -> None:
        self._store: dict[tuple, Any] = {}
        self.hits: int = 0
        self.misses: int = 0

    def get(self, url: str, params: dict | None) -> Any | None:
        """
        Return the cached value for (url, params), or None if not cached.

        Parameters
        ----------
        url:
            The full request URL.
        params:
            Query parameters dict, or None.
        """
        key = self._key(url, params)
        value = self._store.get(key)
        if value is not None:
            self.hits += 1
            logger.debug("cache hit: %s", url)
        else:
            self.misses += 1
        return value

    def set(self, url: str, params: dict | None, value: Any) -> None:
        """
        Store a value for (url, params).

        Parameters
        ----------
        url:
            The full request URL.
        params:
            Query parameters dict, or None.
        value:
            The JSON response to cache.
        """
        key = self._key(url, params)
        self._store[key] = value

    def clear(self) -> None:
        """Discard all cached entries and reset hit/miss counters."""
        self._store.clear()
        self.hits = 0
        self.misses = 0

    @property
    def size(self) -> int:
        """Number of cached entries."""
        return len(self._store)

    def log_stats(self) -> None:
        """Log cache hit/miss statistics at INFO level."""
        total = self.hits + self.misses
        rate = (self.hits / total * 100) if total else 0
        logger.info(
            "RequestCache: %d hits / %d misses (%.0f%% hit rate, %d entries)",
            self.hits,
            self.misses,
            rate,
            self.size,
        )

    @staticmethod
    def _key(url: str, params: dict | None) -> tuple:
        """Build a hashable cache key from url and params."""
        if not params:
            return (url,)
        return (url, tuple(sorted(params.items())))
