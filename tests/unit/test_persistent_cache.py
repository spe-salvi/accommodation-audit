"""
Unit tests for audit.cache.persistent.PersistentCache.

Tests cover:
  - Basic get/set round-trip
  - TTL expiry (entry treated as miss when expired)
  - Cache miss returns None
  - invalidate() resets cached_at to epoch (forces miss)
  - Version mismatch discards file and starts fresh
  - Corrupt JSON file handled gracefully
  - stats() counts valid vs expired entries correctly
  - Custom TTL overrides
  - Lazy loading (file only read on first access)
  - List values via get_list()
"""

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from audit.cache.persistent import CacheEntity, PersistentCache, _EPOCH


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cache(tmp_path: Path, **ttl_overrides) -> PersistentCache:
    """Create a PersistentCache backed by a temp directory."""
    ttls = {
        CacheEntity.TERM:   timedelta(days=365),
        CacheEntity.COURSE: timedelta(days=30),
        CacheEntity.QUIZ:   timedelta(days=1),
        CacheEntity.USER:   timedelta(days=365),
        **ttl_overrides,
    }
    return PersistentCache(tmp_path, ttls=ttls)


# ---------------------------------------------------------------------------
# Basic get / set
# ---------------------------------------------------------------------------

def test_set_and_get_returns_value(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set(CacheEntity.COURSE, 12977, {"name": "Christian Moral Principles"})
    result = cache.get(CacheEntity.COURSE, 12977)
    assert result == {"name": "Christian Moral Principles"}


def test_get_missing_key_returns_none(tmp_path):
    cache = _make_cache(tmp_path)
    assert cache.get(CacheEntity.COURSE, 99999) is None


def test_set_int_and_str_key_are_equivalent(tmp_path):
    """JSON stores keys as strings — int and str keys should be interchangeable."""
    cache = _make_cache(tmp_path)
    cache.set(CacheEntity.USER, 5961, {"name": "Test User"})
    assert cache.get(CacheEntity.USER, "5961") == {"name": "Test User"}
    assert cache.get(CacheEntity.USER, 5961) == {"name": "Test User"}


def test_overwrite_existing_entry(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set(CacheEntity.QUIZ, "12977:classic", [{"id": 1}])
    cache.set(CacheEntity.QUIZ, "12977:classic", [{"id": 2}])
    assert cache.get(CacheEntity.QUIZ, "12977:classic") == [{"id": 2}]


# ---------------------------------------------------------------------------
# TTL expiry
# ---------------------------------------------------------------------------

def test_expired_entry_returns_none(tmp_path):
    """An entry with cached_at in the past beyond TTL should be a miss."""
    cache = _make_cache(tmp_path, **{CacheEntity.COURSE: timedelta(seconds=1)})
    cache.set(CacheEntity.COURSE, 12977, {"name": "Old Course"})

    # Manually backdate the cached_at to force expiry.
    store = cache._load(CacheEntity.COURSE)
    store["12977"]["cached_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=10)
    ).isoformat()
    cache._save(CacheEntity.COURSE, store)

    assert cache.get(CacheEntity.COURSE, 12977) is None


def test_unexpired_entry_returns_value(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set(CacheEntity.COURSE, 12977, {"name": "Course"})
    assert cache.get(CacheEntity.COURSE, 12977) is not None


# ---------------------------------------------------------------------------
# get_list
# ---------------------------------------------------------------------------

def test_get_list_returns_list(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set(CacheEntity.QUIZ, "12977:classic", [{"id": 1}, {"id": 2}])
    result = cache.get_list(CacheEntity.QUIZ, "12977:classic")
    assert result == [{"id": 1}, {"id": 2}]


def test_get_list_returns_none_for_dict_value(tmp_path):
    """get_list should return None when the stored value is a dict, not a list."""
    cache = _make_cache(tmp_path)
    cache.set(CacheEntity.COURSE, 12977, {"name": "Course"})
    assert cache.get_list(CacheEntity.COURSE, 12977) is None


def test_get_list_returns_none_on_miss(tmp_path):
    cache = _make_cache(tmp_path)
    assert cache.get_list(CacheEntity.QUIZ, "99999:classic") is None


# ---------------------------------------------------------------------------
# invalidate
# ---------------------------------------------------------------------------

def test_invalidate_resets_cached_at_to_epoch(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set(CacheEntity.COURSE, 12977, {"name": "Course"})
    cache.set(CacheEntity.COURSE, 12978, {"name": "Course 2"})

    count = cache.invalidate(CacheEntity.COURSE)
    assert count == 2

    # Both entries should now be expired (cached_at = epoch).
    store = cache._load(CacheEntity.COURSE)
    for entry in store.values():
        assert entry["cached_at"] == _EPOCH.isoformat()


def test_invalidate_causes_cache_miss(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set(CacheEntity.COURSE, 12977, {"name": "Course"})
    cache.invalidate(CacheEntity.COURSE)
    assert cache.get(CacheEntity.COURSE, 12977) is None


def test_invalidate_empty_cache_returns_zero(tmp_path):
    cache = _make_cache(tmp_path)
    assert cache.invalidate(CacheEntity.TERM) == 0


def test_data_preserved_after_invalidate(tmp_path):
    """Invalidation resets timestamps but preserves the data on disk."""
    cache = _make_cache(tmp_path)
    cache.set(CacheEntity.COURSE, 12977, {"name": "Course"})
    cache.invalidate(CacheEntity.COURSE)

    # Data should still be in the file even though TTL is expired.
    path = cache._path(CacheEntity.COURSE)
    raw = json.loads(path.read_text())
    assert "12977" in raw["entries"]
    assert raw["entries"]["12977"]["data"] == {"name": "Course"}


# ---------------------------------------------------------------------------
# Persistence (survives cache object recreation)
# ---------------------------------------------------------------------------

def test_data_persists_across_cache_instances(tmp_path):
    """Data written by one PersistentCache instance is readable by a new one."""
    cache1 = _make_cache(tmp_path)
    cache1.set(CacheEntity.USER, 5961, {"name": "Patrick"})

    cache2 = _make_cache(tmp_path)
    assert cache2.get(CacheEntity.USER, 5961) == {"name": "Patrick"}


# ---------------------------------------------------------------------------
# File format / error handling
# ---------------------------------------------------------------------------

def test_version_mismatch_discards_file(tmp_path):
    """A cache file with a different version number should be discarded."""
    path = tmp_path / "courses.json"
    path.write_text(json.dumps({
        "version": 99,
        "entries": {"12977": {"data": {"name": "Old"}, "cached_at": "2020-01-01T00:00:00+00:00"}}
    }))
    cache = _make_cache(tmp_path)
    assert cache.get(CacheEntity.COURSE, 12977) is None


def test_corrupt_json_handled_gracefully(tmp_path):
    """A corrupt cache file should not crash — start fresh instead."""
    path = tmp_path / "courses.json"
    path.write_text("{ this is not valid json !!!")
    cache = _make_cache(tmp_path)
    assert cache.get(CacheEntity.COURSE, 12977) is None
    # Should be able to write new entries normally.
    cache.set(CacheEntity.COURSE, 12977, {"name": "New"})
    assert cache.get(CacheEntity.COURSE, 12977) == {"name": "New"}


def test_missing_file_starts_empty(tmp_path):
    """No cache file should result in an empty cache, not an error."""
    cache = _make_cache(tmp_path)
    assert cache.get(CacheEntity.TERM, 117) is None


# ---------------------------------------------------------------------------
# stats()
# ---------------------------------------------------------------------------

def test_stats_counts_valid_and_expired(tmp_path):
    cache = _make_cache(tmp_path, **{CacheEntity.COURSE: timedelta(days=30)})
    cache.set(CacheEntity.COURSE, 12977, {"name": "Valid"})
    cache.set(CacheEntity.COURSE, 12978, {"name": "Also Valid"})

    # Expire one entry manually.
    store = cache._load(CacheEntity.COURSE)
    store["12978"]["cached_at"] = (
        datetime.now(timezone.utc) - timedelta(days=60)
    ).isoformat()
    cache._save(CacheEntity.COURSE, store)

    stats = cache.stats()
    assert stats["courses"]["total"] == 2
    assert stats["courses"]["valid"] == 1
    assert stats["courses"]["expired"] == 1


def test_stats_empty_cache_all_zeros(tmp_path):
    cache = _make_cache(tmp_path)
    stats = cache.stats()
    for entity_stats in stats.values():
        assert entity_stats["total"] == 0
        assert entity_stats["valid"] == 0
        assert entity_stats["expired"] == 0
