"""
Unit tests for CLI helper functions in main.py.

Tests cover:
  - _parse_id_or_query(): integer strings, name strings, None, edge cases
  - _build_scope(): correct routing of each flag to ID vs query field,
    mixed ID + query, all-None inputs
  - _scope_desc(): formatting for all combinations of set fields

These are pure functions with no I/O — no fixtures or async needed.
"""

import pytest

from main import _build_scope, _parse_id_or_query, _scope_desc
from audit.planner import AuditScope
from audit.repos.base import AccommodationType


_TYPES = [AccommodationType.EXTRA_TIME]


# ---------------------------------------------------------------------------
# _parse_id_or_query
# ---------------------------------------------------------------------------

def test_parse_integer_string_returns_int():
    assert _parse_id_or_query("117") == (117, None)


def test_parse_large_integer_string_returns_int():
    assert _parse_id_or_query("99118") == (99118, None)


def test_parse_name_string_returns_query():
    assert _parse_id_or_query("Spring 2026") == (None, "Spring 2026")


def test_parse_single_word_returns_query():
    assert _parse_id_or_query("McCarthy") == (None, "McCarthy")


def test_parse_none_returns_none_none():
    assert _parse_id_or_query(None) == (None, None)


def test_parse_numeric_sis_id_returns_int():
    """SIS IDs that look like integers are parsed as IDs — Canvas accepts both."""
    assert _parse_id_or_query("2621872") == (2621872, None)


def test_parse_alphanumeric_sis_code_returns_query():
    """Course codes like CHM-115-GK are not integers — treated as queries."""
    assert _parse_id_or_query("CHM-115-GK") == (None, "CHM-115-GK")


def test_parse_zero_returns_int():
    assert _parse_id_or_query("0") == (0, None)


def test_parse_negative_number_returns_query():
    """Negative numbers are not valid Canvas IDs — treat as query string."""
    id_val, query = _parse_id_or_query("-1")
    # -1 parses as int(-1), so it returns as int
    assert id_val == -1
    assert query is None


def test_parse_float_string_returns_query():
    assert _parse_id_or_query("3.14") == (None, "3.14")


def test_parse_empty_string_returns_query():
    """Empty string is not an integer — return as query."""
    assert _parse_id_or_query("") == (None, "")


# ---------------------------------------------------------------------------
# _build_scope — ID routing
# ---------------------------------------------------------------------------

def test_build_scope_term_id_routes_to_term_id():
    scope = _build_scope(engine="classic", types=_TYPES, term="117",
                         course=None, quiz=None, user=None)
    assert scope.term_id == 117
    assert scope.term_query is None


def test_build_scope_course_id_routes_to_course_id():
    scope = _build_scope(engine="classic", types=_TYPES, term=None,
                         course="12977", quiz=None, user=None)
    assert scope.course_id == 12977
    assert scope.course_query is None


def test_build_scope_quiz_id_routes_to_quiz_id():
    scope = _build_scope(engine="classic", types=_TYPES, term=None,
                         course="12977", quiz="48379", user=None)
    assert scope.quiz_id == 48379
    assert scope.quiz_query is None


def test_build_scope_user_id_routes_to_user_id():
    scope = _build_scope(engine="classic", types=_TYPES, term=None,
                         course=None, quiz=None, user="5961")
    assert scope.user_id == 5961
    assert scope.user_query is None


# ---------------------------------------------------------------------------
# _build_scope — query routing
# ---------------------------------------------------------------------------

def test_build_scope_term_name_routes_to_term_query():
    scope = _build_scope(engine="classic", types=_TYPES, term="Spring 2026",
                         course=None, quiz=None, user=None)
    assert scope.term_query == "Spring 2026"
    assert scope.term_id is None


def test_build_scope_course_name_routes_to_course_query():
    scope = _build_scope(engine="classic", types=_TYPES, term="117",
                         course="Moral Principles", quiz=None, user=None)
    assert scope.course_query == "Moral Principles"
    assert scope.course_id is None
    # term was an ID, so term_id should be set
    assert scope.term_id == 117


def test_build_scope_quiz_title_routes_to_quiz_query():
    scope = _build_scope(engine="classic", types=_TYPES, term=None,
                         course="12977", quiz="Midterm", user=None)
    assert scope.quiz_query == "Midterm"
    assert scope.quiz_id is None


def test_build_scope_user_name_routes_to_user_query():
    scope = _build_scope(engine="new", types=_TYPES, term=None,
                         course=None, quiz=None, user="McCarthy")
    assert scope.user_query == "McCarthy"
    assert scope.user_id is None


def test_build_scope_course_code_routes_to_query():
    scope = _build_scope(engine="classic", types=_TYPES, term="117",
                         course="CHM-115-GK", quiz=None, user=None)
    assert scope.course_query == "CHM-115-GK"
    assert scope.course_id is None


# ---------------------------------------------------------------------------
# _build_scope — all None
# ---------------------------------------------------------------------------

def test_build_scope_all_none_produces_empty_scope():
    scope = _build_scope(engine="classic", types=_TYPES,
                         term=None, course=None, quiz=None, user=None)
    assert scope.term_id is None
    assert scope.term_query is None
    assert scope.course_id is None
    assert scope.course_query is None
    assert scope.quiz_id is None
    assert scope.quiz_query is None
    assert scope.user_id is None
    assert scope.user_query is None


# ---------------------------------------------------------------------------
# _build_scope — engine and types pass through
# ---------------------------------------------------------------------------

def test_build_scope_preserves_engine():
    scope = _build_scope(engine="new", types=_TYPES,
                         term="117", course=None, quiz=None, user=None)
    assert scope.engine == "new"


def test_build_scope_preserves_accommodation_types():
    types = [AccommodationType.EXTRA_TIME, AccommodationType.EXTRA_ATTEMPT]
    scope = _build_scope(engine="classic", types=types,
                         term="117", course=None, quiz=None, user=None)
    assert scope.accommodation_types == types


# ---------------------------------------------------------------------------
# _scope_desc
# ---------------------------------------------------------------------------

def test_scope_desc_term_id():
    scope = AuditScope(engine="classic", term_id=117)
    assert "term=117" in _scope_desc(scope)


def test_scope_desc_term_query():
    scope = AuditScope(engine="classic", term_query="Spring")
    assert "term='Spring'" in _scope_desc(scope)


def test_scope_desc_course_id():
    scope = AuditScope(engine="classic", course_id=12977)
    assert "course=12977" in _scope_desc(scope)


def test_scope_desc_course_query():
    scope = AuditScope(engine="classic", term_id=117, course_query="Moral")
    assert "course='Moral'" in _scope_desc(scope)


def test_scope_desc_user_id():
    scope = AuditScope(engine="classic", user_id=5961)
    assert "user=5961" in _scope_desc(scope)


def test_scope_desc_user_query():
    scope = AuditScope(engine="classic", user_query="McCarthy")
    assert "user='McCarthy'" in _scope_desc(scope)


def test_scope_desc_combined_user_and_term():
    scope = AuditScope(engine="classic", user_id=5961, term_id=117)
    desc = _scope_desc(scope)
    assert "user=5961" in desc
    assert "term=117" in desc


def test_scope_desc_empty_scope_returns_empty_string():
    scope = AuditScope(engine="classic")
    assert _scope_desc(scope) == ""
