"""
Unit tests for audit.resolver.Resolver.

Tests cover:
  - resolve_term(): substring match, case-insensitive, multiple matches,
    no match raises ResolveError, empty terms list raises ResolveError
  - resolve_course(): delegates to search_courses, propagates results,
    no results raises ResolveError
  - resolve_quiz(): local substring filter, case-insensitive, multiple
    matches, no match raises ResolveError, empty quiz list raises ResolveError
  - resolve_user(): delegates to search_users, propagates results,
    no results raises ResolveError
  - ResolveError carries correct query and entity_type

Uses a FakeResolverRepo — no network calls, no Canvas API dependency.
"""

import pytest

from audit.models.canvas import Course, Quiz, Term, User
from audit.resolver import Resolver, ResolveError


# ---------------------------------------------------------------------------
# Fake repo
# ---------------------------------------------------------------------------

class FakeResolverRepo:
    """Minimal repo for Resolver tests. Injects controllable data."""

    def __init__(
        self,
        *,
        terms: list[Term] | None = None,
        quizzes: dict[int, list[Quiz]] | None = None,
        search_courses_result: list[Course] | None = None,
        search_users_result: list[User] | None = None,
    ):
        self._terms = terms or []
        self._quizzes = quizzes or {}
        self._search_courses_result = search_courses_result or []
        self._search_users_result = search_users_result or []
        self.search_courses_calls: list[tuple] = []
        self.search_users_calls: list[str] = []

    async def list_terms(self) -> list[Term]:
        return list(self._terms)

    async def list_quizzes(self, *, course_id: int, engine: str) -> list[Quiz]:
        return list(self._quizzes.get(course_id, []))

    async def search_courses(self, query: str, *, term_id: int) -> list[Course]:
        self.search_courses_calls.append((query, term_id))
        return list(self._search_courses_result)

    async def search_users(self, query: str) -> list[User]:
        self.search_users_calls.append(query)
        return list(self._search_users_result)


def _term(term_id: int, name: str) -> Term:
    return Term(term_id=term_id, name=name, sis_term_id=None)


def _course(course_id: int) -> Course:
    return Course(
        course_id=course_id, name=f"Course {course_id}",
        course_code=f"CRS-{course_id}", sis_course_id=None,
        enrollment_term_id=117,
    )


def _quiz(quiz_id: int, title: str, course_id: int = 12977) -> Quiz:
    return Quiz(
        quiz_id=quiz_id, course_id=course_id,
        title=title, engine="classic",
        due_at=None, lock_at=None,
    )


def _user(user_id: int, name: str) -> User:
    return User(id=user_id, sortable_name=name, sis_user_id=str(user_id))


# ---------------------------------------------------------------------------
# resolve_term
# ---------------------------------------------------------------------------

async def test_resolve_term_exact_match():
    repo = FakeResolverRepo(terms=[_term(117, "2025-2026 - Spring")])
    result = await Resolver(repo).resolve_term("2025-2026 - Spring")
    assert len(result) == 1
    assert result[0].term_id == 117


async def test_resolve_term_substring_match():
    repo = FakeResolverRepo(terms=[
        _term(116, "2025-2026 - Fall"),
        _term(117, "2025-2026 - Spring"),
    ])
    result = await Resolver(repo).resolve_term("Spring")
    assert len(result) == 1
    assert result[0].term_id == 117


async def test_resolve_term_case_insensitive():
    repo = FakeResolverRepo(terms=[_term(117, "2025-2026 - Spring")])
    result = await Resolver(repo).resolve_term("spring")
    assert len(result) == 1


async def test_resolve_term_multiple_matches():
    repo = FakeResolverRepo(terms=[
        _term(116, "2025-2026 - Fall"),
        _term(117, "2025-2026 - Spring"),
    ])
    result = await Resolver(repo).resolve_term("2025-2026")
    assert len(result) == 2


async def test_resolve_term_no_match_raises_resolve_error():
    repo = FakeResolverRepo(terms=[_term(117, "2025-2026 - Spring")])
    with pytest.raises(ResolveError) as exc_info:
        await Resolver(repo).resolve_term("Summer")
    assert exc_info.value.entity_type == "term"
    assert exc_info.value.query == "Summer"


async def test_resolve_term_empty_list_raises_resolve_error():
    repo = FakeResolverRepo(terms=[])
    with pytest.raises(ResolveError):
        await Resolver(repo).resolve_term("Spring")


async def test_resolve_term_skips_terms_with_no_name():
    """Terms with name=None should be silently skipped."""
    repo = FakeResolverRepo(terms=[
        Term(term_id=99, name=None, sis_term_id=None),
        _term(117, "Spring"),
    ])
    result = await Resolver(repo).resolve_term("Spring")
    assert len(result) == 1
    assert result[0].term_id == 117


# ---------------------------------------------------------------------------
# resolve_course
# ---------------------------------------------------------------------------

async def test_resolve_course_delegates_to_search_courses():
    courses = [_course(12977)]
    repo = FakeResolverRepo(search_courses_result=courses)
    result = await Resolver(repo).resolve_course("Moral Principles", term_id=117)

    assert len(repo.search_courses_calls) == 1
    assert repo.search_courses_calls[0] == ("Moral Principles", 117)
    assert result == courses


async def test_resolve_course_returns_all_matches():
    courses = [_course(12977), _course(12978)]
    repo = FakeResolverRepo(search_courses_result=courses)
    result = await Resolver(repo).resolve_course("CHM", term_id=117)
    assert len(result) == 2


async def test_resolve_course_no_results_raises_resolve_error():
    repo = FakeResolverRepo(search_courses_result=[])
    with pytest.raises(ResolveError) as exc_info:
        await Resolver(repo).resolve_course("Nonexistent Course", term_id=117)
    assert exc_info.value.entity_type == "course"
    assert exc_info.value.query == "Nonexistent Course"


async def test_resolve_course_passes_term_id_correctly():
    repo = FakeResolverRepo(search_courses_result=[_course(12977)])
    await Resolver(repo).resolve_course("Test", term_id=999)
    assert repo.search_courses_calls[0][1] == 999


# ---------------------------------------------------------------------------
# resolve_quiz
# ---------------------------------------------------------------------------

async def test_resolve_quiz_substring_match():
    quizzes = [
        _quiz(48379, "Midterm Exam"),
        _quiz(48380, "Final Exam"),
    ]
    repo = FakeResolverRepo(quizzes={12977: quizzes})
    result = await Resolver(repo).resolve_quiz("Midterm", course_id=12977, engine="classic")
    assert len(result) == 1
    assert result[0].quiz_id == 48379


async def test_resolve_quiz_case_insensitive():
    quizzes = [_quiz(48379, "Midterm Exam")]
    repo = FakeResolverRepo(quizzes={12977: quizzes})
    result = await Resolver(repo).resolve_quiz("midterm", course_id=12977, engine="classic")
    assert len(result) == 1


async def test_resolve_quiz_multiple_matches():
    quizzes = [
        _quiz(48379, "Chapter 1 Quiz"),
        _quiz(48380, "Chapter 2 Quiz"),
        _quiz(48381, "Midterm"),
    ]
    repo = FakeResolverRepo(quizzes={12977: quizzes})
    result = await Resolver(repo).resolve_quiz("Quiz", course_id=12977, engine="classic")
    assert len(result) == 2
    assert {r.quiz_id for r in result} == {48379, 48380}


async def test_resolve_quiz_no_match_raises_resolve_error():
    quizzes = [_quiz(48379, "Midterm")]
    repo = FakeResolverRepo(quizzes={12977: quizzes})
    with pytest.raises(ResolveError) as exc_info:
        await Resolver(repo).resolve_quiz("Final", course_id=12977, engine="classic")
    assert exc_info.value.entity_type == "quiz"
    assert exc_info.value.query == "Final"


async def test_resolve_quiz_empty_course_raises_resolve_error():
    repo = FakeResolverRepo(quizzes={})
    with pytest.raises(ResolveError):
        await Resolver(repo).resolve_quiz("Midterm", course_id=12977, engine="classic")


async def test_resolve_quiz_skips_quizzes_with_no_title():
    quizzes = [
        Quiz(quiz_id=1, course_id=12977, title=None, engine="classic", due_at=None, lock_at=None),
        _quiz(2, "Midterm"),
    ]
    repo = FakeResolverRepo(quizzes={12977: quizzes})
    result = await Resolver(repo).resolve_quiz("Midterm", course_id=12977, engine="classic")
    assert len(result) == 1
    assert result[0].quiz_id == 2


# ---------------------------------------------------------------------------
# resolve_user
# ---------------------------------------------------------------------------

async def test_resolve_user_delegates_to_search_users():
    users = [_user(5961, "McCarthy, Patrick")]
    repo = FakeResolverRepo(search_users_result=users)
    result = await Resolver(repo).resolve_user("McCarthy")

    assert len(repo.search_users_calls) == 1
    assert repo.search_users_calls[0] == "McCarthy"
    assert result == users


async def test_resolve_user_returns_all_matches():
    users = [
        _user(5961, "McCarthy, Patrick"),
        _user(5962, "McCarthy, Jane"),
    ]
    repo = FakeResolverRepo(search_users_result=users)
    result = await Resolver(repo).resolve_user("McCarthy")
    assert len(result) == 2


async def test_resolve_user_no_results_raises_resolve_error():
    repo = FakeResolverRepo(search_users_result=[])
    with pytest.raises(ResolveError) as exc_info:
        await Resolver(repo).resolve_user("Zznotarealname")
    assert exc_info.value.entity_type == "user"
    assert exc_info.value.query == "Zznotarealname"


async def test_resolve_user_sis_id_query_delegates_to_canvas():
    """SIS user ID queries are passed to search_users — Canvas handles matching."""
    users = [_user(5961, "McCarthy, Patrick")]
    repo = FakeResolverRepo(search_users_result=users)
    result = await Resolver(repo).resolve_user("2621872")

    assert repo.search_users_calls[0] == "2621872"
    assert len(result) == 1


# ---------------------------------------------------------------------------
# ResolveError attributes
# ---------------------------------------------------------------------------

def test_resolve_error_carries_query_and_entity_type():
    err = ResolveError("Not found", query="Spring", entity_type="term")
    assert err.query == "Spring"
    assert err.entity_type == "term"
    assert "Not found" in str(err)


def test_resolve_error_is_exception():
    err = ResolveError("msg", query="x", entity_type="course")
    assert isinstance(err, Exception)
