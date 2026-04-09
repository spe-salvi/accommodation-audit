"""
Cache management API routes.

GET    /api/cache/stats          — persistent cache statistics
DELETE /api/cache/{entity}       — invalidate a cache entity type
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from api.models import CacheEntityStats, CacheStatsResponse, InvalidateCacheResponse

router = APIRouter(prefix="/api/cache", tags=["cache"])

_CACHE_DIR = Path(".cache")

_VALID_ENTITIES = {"terms", "courses", "quizzes", "users"}


@router.get("/stats", response_model=CacheStatsResponse)
async def get_cache_stats():
    """Return entry counts, TTL info, and expiry stats for all cache entities."""
    from audit.cache.persistent import PersistentCache
    cache = PersistentCache(_CACHE_DIR)
    raw = cache.stats()
    return CacheStatsResponse(
        stats={
            entity: CacheEntityStats(**info)
            for entity, info in raw.items()
        }
    )


@router.delete("/{entity}", response_model=InvalidateCacheResponse)
async def invalidate_cache(entity: str):
    """
    Invalidate all cached entries for the given entity type.

    Resets cached_at to the Unix epoch so entries are treated as expired
    on the next fetch, while preserving the data on disk.
    """
    if entity not in _VALID_ENTITIES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown entity {entity!r}. Valid values: {sorted(_VALID_ENTITIES)}",
        )

    from audit.cache.persistent import CacheEntity, PersistentCache
    _entity_map = {
        "terms":   CacheEntity.TERM,
        "courses": CacheEntity.COURSE,
        "quizzes": CacheEntity.QUIZ,
        "users":   CacheEntity.USER,
    }

    cache = PersistentCache(_CACHE_DIR)
    count = cache.invalidate(_entity_map[entity])

    return InvalidateCacheResponse(
        entity=entity,
        count=count,
        message=f"Invalidated {count} {entity} cache entries.",
    )
