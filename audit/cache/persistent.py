"""
Persistent TTL cache for Canvas entity data.

Stores entity data in JSON files under a configurable cache directory
(default: ``.cache/`` in the project root). Each file holds all entries
for one entity type, with a per-entry timestamp used to check TTL.

Cached entity types and their TTLs
------------------------------------
- Terms:   1 year  (essentially immutable — treat as forever)
- Courses: 30 days (stable within a term)
- Quizzes: 1 day   (instructors may edit quizzes during a term)
- Users:   1 year  (legal name changes are rare; SIS IDs never change)

Cache invalidation
------------------
Entries expire when ``now - cached_at > TTL``. Expired entries are
treated as misses and overwritten on the next successful fetch.

The ``--refresh-entity`` CLI flag resets ``cached_at`` to the Unix
epoch for all entries of a given type, forcing them to be treated as
expired on the next run. This preserves the cached data (useful for
auditing what was previously cached) while forcing a re-fetch.

Hit/miss tracking
-----------------
``hits`` counts calls to ``get()`` or ``get_list()`` that returned data.
``misses`` counts calls that returned None (key absent or TTL expired).
Both counters are reset to zero on construction and are queryable at
run end via ``audit.metrics.collect_metrics()``.

File format
-----------
Each file is a JSON object::

    {
        "version": 1,
        "entries": {
            "<str_key>": {
                "data": { ...raw dict... },
                "cached_at": "2026-03-31T18:00:00Z"
            }
        }
    }

Thread safety
-------------
asyncio is single-threaded so no locking is needed. The cache is
not safe for concurrent writes from multiple processes.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Entity types and TTLs
# ---------------------------------------------------------------------------

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_CACHE_VERSION = 1


class CacheEntity(str, Enum):
    """Supported persistent cache entity types."""
    TERM   = "terms"
    COURSE = "courses"
    QUIZ   = "quizzes"
    USER   = "users"


# TTLs per entity type.
_DEFAULT_TTLS: dict[CacheEntity, timedelta] = {
    CacheEntity.TERM:   timedelta(days=365),   # ~forever — terms never change
    CacheEntity.COURSE: timedelta(days=30),    # stable within a term
    CacheEntity.QUIZ:   timedelta(days=1),     # instructors may edit quizzes
    CacheEntity.USER:   timedelta(days=365),   # name changes are rare
}


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

class PersistentCache:
    """
    File-backed TTL cache for Canvas entity data.

    Each entity type is stored in its own JSON file under ``cache_dir``.
    Files are loaded lazily on first access and written back after every
    ``set`` call.

    Parameters
    ----------
    cache_dir:
        Directory where cache files are stored. Created if absent.
        Defaults to ``.cache`` in the current working directory.
    ttls:
        Override default TTLs per entity type. Keys not present in
        this dict fall back to ``_DEFAULT_TTLS``.
    """

    def __init__(
        self,
        cache_dir: str | Path = ".cache",
        *,
        ttls: dict[CacheEntity, timedelta] | None = None,
    ) -> None:
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._ttls = {**_DEFAULT_TTLS, **(ttls or {})}
        # Lazy-loaded store: entity_type → {str_key: {data, cached_at}}
        self._store: dict[CacheEntity, dict[str, dict]] = {}
        # Hit/miss counters — reset each run, queryable via collect_metrics().
        self.hits: int = 0
        self.misses: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, entity: CacheEntity, key: int | str) -> dict | None:
        """
        Return the cached data for *key*, or None if missing or expired.

        Increments ``hits`` on a valid cache entry, ``misses`` on an
        absent key or expired TTL.
        """
        store = self._load(entity)
        entry = store.get(str(key))
        if entry is None:
            self.misses += 1
            return None

        cached_at = _parse_dt(entry["cached_at"])
        if datetime.now(timezone.utc) - cached_at > self._ttls[entity]:
            logger.debug(
                "persistent cache EXPIRED: %s/%s", entity.value, key,
            )
            self.misses += 1
            return None

        logger.debug("persistent cache HIT: %s/%s", entity.value, key)
        self.hits += 1
        return entry["data"]

    def get_list(self, entity: CacheEntity, key: int | str) -> list | None:
        """
        Return cached list data for *key*, or None if missing or expired.

        Delegates to ``get()`` — hits and misses are tracked there.
        """
        data = self.get(entity, key)
        if data is None:
            return None
        if not isinstance(data, list):
            # Technically a hit for the key, but the wrong type — treat as miss.
            self.hits -= 1
            self.misses += 1
            return None
        return data

    def set(self, entity: CacheEntity, key: int | str, data: Any) -> None:
        """Store *data* for *key* with the current timestamp."""
        store = self._load(entity)
        store[str(key)] = {
            "data": data,
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save(entity, store)
        logger.debug("persistent cache SET: %s/%s", entity.value, key)

    def invalidate(self, entity: CacheEntity) -> int:
        """
        Reset ``cached_at`` to the Unix epoch for all entries of *entity*,
        forcing them to be treated as expired on the next access.

        Returns the number of entries invalidated.
        """
        store = self._load(entity)
        count = len(store)
        for key in store:
            store[key]["cached_at"] = _EPOCH.isoformat()
        self._save(entity, store)
        logger.info(
            "persistent cache INVALIDATED: %s (%d entries)",
            entity.value, count,
        )
        return count

    def stats(self) -> dict[str, dict]:
        """Return entry totals and valid/expired counts per entity type."""
        result = {}
        for entity in CacheEntity:
            store = self._load(entity)
            now = datetime.now(timezone.utc)
            ttl = self._ttls[entity]
            valid = sum(
                1 for e in store.values()
                if now - _parse_dt(e["cached_at"]) <= ttl
            )
            result[entity.value] = {
                "total": len(store),
                "valid": valid,
                "expired": len(store) - valid,
                "ttl_hours": ttl.total_seconds() / 3600,
            }
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _path(self, entity: CacheEntity) -> Path:
        return self._dir / f"{entity.value}.json"

    def _load(self, entity: CacheEntity) -> dict[str, dict]:
        if entity in self._store:
            return self._store[entity]

        path = self._path(entity)
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if raw.get("version") == _CACHE_VERSION:
                    self._store[entity] = raw.get("entries", {})
                else:
                    logger.warning(
                        "persistent cache: version mismatch in %s, discarding", path,
                    )
                    self._store[entity] = {}
            except (json.JSONDecodeError, KeyError) as exc:
                logger.warning(
                    "persistent cache: could not read %s (%s), starting fresh",
                    path, exc,
                )
                self._store[entity] = {}
        else:
            self._store[entity] = {}

        return self._store[entity]

    def _save(self, entity: CacheEntity, store: dict[str, dict]) -> None:
        path = self._path(entity)
        try:
            path.write_text(
                json.dumps(
                    {"version": _CACHE_VERSION, "entries": store},
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error("persistent cache: failed to write %s: %s", path, exc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_dt(value: str) -> datetime:
    """Parse an ISO 8601 datetime string, always returning a UTC-aware datetime."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
