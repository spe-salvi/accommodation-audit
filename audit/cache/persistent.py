"""
Persistent TTL cache for Canvas entity data.

Stores entity data in JSON files under a configurable cache directory
(default: ``.cache/`` in the project root). Each file holds all entries
for one entity type, with a per-entry timestamp used to check TTL.

Cached entity types and their default TTLs
------------------------------------------
- Terms:   30 days  (almost never change)
- Courses: 30 days  (stable within a term)
- Quizzes:  1 day   (instructors do edit quizzes)
- Users:    7 days  (name changes are rare)

Cache invalidation
------------------
Entries expire when ``now - cached_at > TTL``. Expired entries are
treated as misses and overwritten on the next successful fetch.

The ``--refresh-entity`` CLI flag resets ``cached_at`` to the Unix
epoch for all entries of a given type, forcing them to be treated as
expired on the next run. This preserves the cached data (useful for
auditing what was previously cached) while forcing a re-fetch.

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
not safe for concurrent writes from multiple processes — use a single
process per cache directory.
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


# Default TTLs per entity type.
_DEFAULT_TTLS: dict[CacheEntity, timedelta] = {
    CacheEntity.TERM:   timedelta(days=30),
    CacheEntity.COURSE: timedelta(days=30),
    CacheEntity.QUIZ:   timedelta(days=1),
    CacheEntity.USER:   timedelta(days=7),
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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, entity: CacheEntity, key: int | str) -> dict | None:
        """
        Return the cached data for *key*, or None if missing or expired.

        Parameters
        ----------
        entity:
            The entity type to look up.
        key:
            The entity's primary ID (term_id, course_id, etc.).
        """
        store = self._load(entity)
        entry = store.get(str(key))
        if entry is None:
            return None

        cached_at = _parse_dt(entry["cached_at"])
        if datetime.now(timezone.utc) - cached_at > self._ttls[entity]:
            logger.debug(
                "persistent cache EXPIRED: %s/%s (age=%s ttl=%s)",
                entity.value, key,
                datetime.now(timezone.utc) - cached_at,
                self._ttls[entity],
            )
            return None

        logger.debug("persistent cache HIT: %s/%s", entity.value, key)
        return entry["data"]

    def get_list(self, entity: CacheEntity, key: int | str) -> list | None:
        """
        Return cached list data for *key*, or None if missing or expired.

        Convenience wrapper for list-valued entries (e.g. quizzes for a
        course). The key convention for list entries is the same as for
        single entries — the caller is responsible for choosing a
        meaningful composite key (e.g. ``f"{course_id}:{engine}"``).
        """
        data = self.get(entity, key)
        if data is None:
            return None
        if not isinstance(data, list):
            return None
        return data

    def set(self, entity: CacheEntity, key: int | str, data: Any) -> None:
        """
        Store *data* for *key* with the current timestamp.

        Parameters
        ----------
        entity:
            The entity type to store.
        key:
            The entity's primary ID.
        data:
            The data to cache. Must be JSON-serialisable.
        """
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

        This preserves the cached data for inspection while ensuring
        every entry is re-fetched on the next run.

        Returns
        -------
        int
            Number of entries invalidated.
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
        """
        Return hit/miss/expired counts and entry totals per entity type.

        Loads all cache files to count valid vs. expired entries.
        """
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
        """Load and return the in-memory store for *entity*, reading from disk if needed."""
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
                        "persistent cache: version mismatch in %s, discarding",
                        path,
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
        """Write the in-memory store for *entity* to disk."""
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
