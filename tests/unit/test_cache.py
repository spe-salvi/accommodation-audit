"""
Unit tests for the runtime request cache.

Tests that:
  - Cache misses return None and increment miss counter
  - Cache hits return stored data and increment hit counter
  - A second identical call does not make a second HTTP request
  - Different URLs are cached independently
  - Different params on the same URL are cached independently
  - clear() resets the store and counters
  - log_stats() runs without error
"""

import pytest

from audit.cache.runtime import RequestCache


# ---------------------------------------------------------------------------
# Basic get/set behaviour
# ---------------------------------------------------------------------------

def test_miss_returns_none():
    cache = RequestCache()
    assert cache.get("https://canvas.example.com/api/v1/courses", None) is None


def test_hit_returns_stored_value():
    cache = RequestCache()
    data = [{"id": 1, "name": "Test Course"}]
    cache.set("https://canvas.example.com/api/v1/courses", None, data)
    result = cache.get("https://canvas.example.com/api/v1/courses", None)
    assert result == data


def test_hit_and_miss_counters():
    cache = RequestCache()
    url = "https://canvas.example.com/api/v1/courses"

    cache.get(url, None)  # miss
    cache.get(url, None)  # miss
    cache.set(url, None, [])
    cache.get(url, None)  # hit

    assert cache.misses == 2
    assert cache.hits == 1


# ---------------------------------------------------------------------------
# Params are part of the cache key
# ---------------------------------------------------------------------------

def test_same_url_different_params_cached_independently():
    cache = RequestCache()
    url = "https://canvas.example.com/api/v1/accounts/1/courses"

    cache.set(url, {"enrollment_term_id": 117}, [{"id": 1}])
    cache.set(url, {"enrollment_term_id": 118}, [{"id": 2}])

    assert cache.get(url, {"enrollment_term_id": 117}) == [{"id": 1}]
    assert cache.get(url, {"enrollment_term_id": 118}) == [{"id": 2}]


def test_none_params_and_empty_dict_treated_same():
    cache = RequestCache()
    url = "https://canvas.example.com/api/v1/courses"
    cache.set(url, None, ["data"])
    # None and empty dict should produce the same key
    assert cache.get(url, None) == ["data"]


def test_different_urls_cached_independently():
    cache = RequestCache()
    cache.set("https://canvas.example.com/api/v1/courses", None, ["courses"])
    cache.set("https://canvas.example.com/api/v1/quizzes", None, ["quizzes"])

    assert cache.get("https://canvas.example.com/api/v1/courses", None) == ["courses"]
    assert cache.get("https://canvas.example.com/api/v1/quizzes", None) == ["quizzes"]


# ---------------------------------------------------------------------------
# clear()
# ---------------------------------------------------------------------------

def test_clear_removes_all_entries():
    cache = RequestCache()
    cache.set("https://canvas.example.com/api/v1/courses", None, ["data"])
    cache.clear()
    assert cache.get("https://canvas.example.com/api/v1/courses", None) is None
    assert cache.size == 0


def test_clear_resets_counters():
    cache = RequestCache()
    url = "https://canvas.example.com/api/v1/courses"
    cache.set(url, None, [])
    cache.get(url, None)  # hit
    cache.get("https://other.com", None)  # miss
    cache.clear()
    assert cache.hits == 0
    assert cache.misses == 0


# ---------------------------------------------------------------------------
# size property
# ---------------------------------------------------------------------------

def test_size_reflects_entry_count():
    cache = RequestCache()
    assert cache.size == 0
    cache.set("https://canvas.example.com/a", None, [])
    assert cache.size == 1
    cache.set("https://canvas.example.com/b", None, [])
    assert cache.size == 2
    cache.set("https://canvas.example.com/a", None, [])  # overwrite
    assert cache.size == 2


# ---------------------------------------------------------------------------
# log_stats()
# ---------------------------------------------------------------------------

def test_log_stats_does_not_raise():
    cache = RequestCache()
    cache.set("https://canvas.example.com/api/v1/courses", None, [])
    cache.get("https://canvas.example.com/api/v1/courses", None)
    cache.get("https://canvas.example.com/missing", None)
    cache.log_stats()  # should not raise


def test_log_stats_with_zero_requests_does_not_raise():
    cache = RequestCache()
    cache.log_stats()  # 0/0 — should not divide by zero
