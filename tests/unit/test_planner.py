"""
Unit tests for audit.planner.

Tests cover:
  - AuditScope construction (ID fields, query fields, mixed)
  - _has_queries() / _replace_scope() helpers
  - AuditPlanner.build() for all ID-based scope combinations:
      term, course, quiz, user, user+term, user+course, user+course+quiz
  - AuditPlanner.build() for query-based scopes:
      term_query, course_query, quiz_query, user_query (single + multiple)
  - Multi-user deduplication (_build_multi_user_plan):
      courses audited once per unique course, user_ids sets correct
  - Error cases: missing course_id with quiz_id, missing term_id with
      course_query, NotImplementedError on JsonRepo enrollment scope,
      ResolveError propagated from resolver
  - AuditPlan.course_count property
  - StepKind routing (COURSE vs USER vs QUIZ)

Uses a FakePlannerRepo that records calls and returns controllable data,
avoiding any network calls or real Canvas API dependency.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

import pytest

from audit.models.canvas import Course, Enrollment, Quiz, Term, User
from audit.planner import (
    AuditPlan,
    AuditPlanner,
    AuditScope,
    AuditStep,
    StepKind,
    _has_queries,
    _replace_scope,
)
from audit.repos.base import AccommodationType
from audit.resolver import ResolveError


# ---------------------------------------------------------------------------
# Fake repo
# ---------------------------------------------------------------------------

def _course(course_id: int, term_id: int = 117) -> Course:
    return Course(
        course_id=course_id,
        name=f"Course {course_id}",
        course_code=f"CRS-{course_id}",
        sis_course_id=None,
        enrollment_term_id=term_id,
    )


def _quiz(quiz_id: int, course_id: int = 12977) -> Quiz:
    return Quiz(
        quiz_id=quiz_id,
        course_id=course_id,
        title=f"Quiz {quiz_id}",
        engine="classic",
        due_at=None,
        lock_at=None,
    )


def _user(user_id: int, name: str = "Test User") -> User:
    return User(id=user_id, sortable_name=name, sis_user_id=str(user_id))


def _term(term_id: int, name: str = "Spring") -> Term:
    return Term(term_id=term_id, name=name, sis_term_id=None)


def _enrollment(user_id: int, course_id: int) -> Enrollment:
    return Enrollment(user_id=user_id, course_id=course_id)


class FakePlannerRepo:
    """
    Minimal repo for planner tests. Supports all methods the planner calls:
    list_courses, list_quizzes, list_terms, list_enrollments, get_course_by_id,
    search_courses, search_users.

    All data is injected at construction. Methods record their call counts.
    """

    def __init__(
        self,
        *,
        courses: dict[int, list[Course]] | None = None,   # term_id → courses
        quizzes: dict[int, list[Quiz]] | None = None,      # course_id → quizzes
        terms: list[Term] | None = None,
        enrollments: dict[int, list[Enrollment]] | None = None,  # user_id → enrollments
        courses_by_id: dict[int, Course] | None = None,
        search_courses_result: list[Course] | None = None,
        search_users_result: list[User] | None = None,
    ):
        self._courses = courses or {}
        self._quizzes = quizzes or {}
        self._terms = terms or []
        self._enrollments = enrollments or {}
        self._courses_by_id = courses_by_id or {}
        self._search_courses_result = search_courses_result or []
        self._search_users_result = search_users_result or []
        # Call counters
        self.list_courses_calls: list[tuple] = []
        self.list_quizzes_calls: list[tuple] = []
        self.list_enrollments_calls: list[tuple] = []
        self.get_course_by_id_calls: list[int] = []
        self.search_courses_calls: list[tuple] = []
        self.search_users_calls: list[str] = []

    async def list_courses(self, *, term_id: int, engine: str) -> list[Course]:
        self.list_courses_calls.append((term_id, engine))
        return list(self._courses.get(term_id, []))

    async def list_quizzes(self, *, course_id: int, engine: str) -> list[Quiz]:
        self.list_quizzes_calls.append((course_id, engine))
        return list(self._quizzes.get(course_id, []))

    async def list_terms(self) -> list[Term]:
        return list(self._terms)

    async def list_enrollments(
        self, user_id: int, *, term_id: int | None = None
    ) -> list[Enrollment]:
        self.list_enrollments_calls.append((user_id, term_id))
        enrollments = list(self._enrollments.get(user_id, []))
        if term_id is not None:
            # Simple simulation: filter by checking if any course exists in term
            term_course_ids = {c.course_id for c in self._courses.get(term_id, [])}
            enrollments = [e for e in enrollments if e.course_id in term_course_ids]
        return enrollments

    async def get_course_by_id(self, course_id: int) -> Optional[Course]:
        self.get_course_by_id_calls.append(course_id)
        return self._courses_by_id.get(course_id)

    async def search_courses(self, query: str, *, term_id: int) -> list[Course]:
        self.search_courses_calls.append((query, term_id))
        return list(self._search_courses_result)

    async def search_users(self, query: str) -> list[User]:
        self.search_users_calls.append(query)
        return list(self._search_users_result)

    # AccommodationRepo protocol stubs (not used by planner)
    async def list_participants(self, **kw): return []
    async def get_participant(self, **kw): return None
    async def list_submissions(self, **kw): return []
    async def get_submission(self, **kw): return None
    async def list_items(self, **kw): return []
    async def get_quiz(self, **kw): return None
    async def get_course(self, **kw): return None


# ---------------------------------------------------------------------------
# AuditScope helpers
# ---------------------------------------------------------------------------

def test_has_queries_false_for_id_only_scope():
    scope = AuditScope(engine="classic", term_id=117)
    assert _has_queries(scope) is False


def test_has_queries_true_for_term_query():
    scope = AuditScope(engine="classic", term_query="Spring")
    assert _has_queries(scope) is True


def test_has_queries_false_when_query_has_corresponding_id():
    """If both term_id and term_query are set, query is already resolved."""
    scope = AuditScope(engine="classic", term_id=117, term_query="Spring")
    assert _has_queries(scope) is False


def test_has_queries_true_for_user_query():
    scope = AuditScope(engine="new", user_query="McCarthy")
    assert _has_queries(scope) is True


def test_replace_scope_updates_fields():
    scope = AuditScope(engine="classic", term_query="Spring")
    new_scope = _replace_scope(scope, term_id=117, term_query=None)
    assert new_scope.term_id == 117
    assert new_scope.term_query is None
    assert new_scope.engine == "classic"


def test_replace_scope_does_not_mutate_original():
    scope = AuditScope(engine="classic", term_query="Spring")
    _replace_scope(scope, term_id=117, term_query=None)
    assert scope.term_id is None
    assert scope.term_query == "Spring"


# ---------------------------------------------------------------------------
# AuditPlan.course_count
# ---------------------------------------------------------------------------

def test_course_count_counts_course_and_user_steps():
    steps = [
        AuditStep(kind=StepKind.COURSE, engine="classic", accommodation_types=None, course_id=1),
        AuditStep(kind=StepKind.USER,   engine="classic", accommodation_types=None, course_id=2, user_id=5),
        AuditStep(kind=StepKind.QUIZ,   engine="classic", accommodation_types=None, course_id=3, quiz_id=9),
    ]
    scope = AuditScope(engine="classic", term_id=117)
    plan = AuditPlan(steps=steps, scope=scope)
    assert plan.course_count == 2


def test_course_count_zero_for_empty_plan():
    scope = AuditScope(engine="classic", term_id=117)
    assert AuditPlan(steps=[], scope=scope).course_count == 0


# ---------------------------------------------------------------------------
# build() — term scope
# ---------------------------------------------------------------------------

async def test_build_term_scope_produces_course_steps():
    courses = [_course(12977), _course(12978), _course(12979)]
    repo = FakePlannerRepo(courses={117: courses})
    scope = AuditScope(engine="classic", term_id=117)
    plan = await AuditPlanner(repo).build(scope)

    assert len(plan.steps) == 3
    assert all(s.kind == StepKind.COURSE for s in plan.steps)
    assert {s.course_id for s in plan.steps} == {12977, 12978, 12979}


async def test_build_term_scope_passes_course_objects():
    """Course objects pre-fetched from list_courses are passed into steps."""
    courses = [_course(12977)]
    repo = FakePlannerRepo(courses={117: courses})
    scope = AuditScope(engine="classic", term_id=117)
    plan = await AuditPlanner(repo).build(scope)

    assert plan.steps[0].course is not None
    assert plan.steps[0].course.course_id == 12977


async def test_build_term_scope_empty_term_returns_no_steps():
    repo = FakePlannerRepo(courses={})
    scope = AuditScope(engine="classic", term_id=999)
    plan = await AuditPlanner(repo).build(scope)
    assert plan.steps == []


# ---------------------------------------------------------------------------
# build() — course scope
# ---------------------------------------------------------------------------

async def test_build_course_scope_produces_single_course_step():
    repo = FakePlannerRepo(courses_by_id={12977: _course(12977)})
    scope = AuditScope(engine="classic", course_id=12977)
    plan = await AuditPlanner(repo).build(scope)

    assert len(plan.steps) == 1
    assert plan.steps[0].kind == StepKind.COURSE
    assert plan.steps[0].course_id == 12977


async def test_build_course_scope_with_user_produces_user_step():
    repo = FakePlannerRepo(courses_by_id={12977: _course(12977)})
    scope = AuditScope(engine="classic", course_id=12977, user_id=5961)
    plan = await AuditPlanner(repo).build(scope)

    assert plan.steps[0].kind == StepKind.USER
    assert plan.steps[0].user_id == 5961


# ---------------------------------------------------------------------------
# build() — quiz scope
# ---------------------------------------------------------------------------

async def test_build_quiz_scope_produces_quiz_step():
    repo = FakePlannerRepo()
    scope = AuditScope(engine="classic", course_id=12977, quiz_id=48379)
    plan = await AuditPlanner(repo).build(scope)

    assert len(plan.steps) == 1
    assert plan.steps[0].kind == StepKind.QUIZ
    assert plan.steps[0].quiz_id == 48379
    assert plan.steps[0].course_id == 12977


async def test_build_quiz_scope_with_user_produces_user_step():
    repo = FakePlannerRepo()
    scope = AuditScope(engine="classic", course_id=12977, quiz_id=48379, user_id=5961)
    plan = await AuditPlanner(repo).build(scope)

    assert plan.steps[0].kind == StepKind.USER
    assert plan.steps[0].quiz_id == 48379
    assert plan.steps[0].user_id == 5961


async def test_build_quiz_without_course_raises_value_error():
    repo = FakePlannerRepo()
    scope = AuditScope(engine="classic", quiz_id=48379)
    with pytest.raises(ValueError, match="course_id"):
        await AuditPlanner(repo).build(scope)


# ---------------------------------------------------------------------------
# build() — user scope (enrollment traversal)
# ---------------------------------------------------------------------------

async def test_build_user_scope_resolves_enrollments():
    enrollments = [_enrollment(5961, 12977), _enrollment(5961, 12978)]
    repo = FakePlannerRepo(enrollments={5961: enrollments})
    scope = AuditScope(engine="classic", user_id=5961)
    plan = await AuditPlanner(repo).build(scope)

    assert len(plan.steps) == 2
    assert all(s.kind == StepKind.USER for s in plan.steps)
    assert all(s.user_id == 5961 for s in plan.steps)
    assert {s.course_id for s in plan.steps} == {12977, 12978}


async def test_build_user_scope_deduplicates_same_course():
    """A user enrolled in the same course twice (different sections) → one step."""
    enrollments = [_enrollment(5961, 12977), _enrollment(5961, 12977)]
    repo = FakePlannerRepo(enrollments={5961: enrollments})
    scope = AuditScope(engine="classic", user_id=5961)
    plan = await AuditPlanner(repo).build(scope)

    assert len(plan.steps) == 1
    assert plan.steps[0].course_id == 12977


async def test_build_user_scope_no_enrollments_returns_empty_plan():
    repo = FakePlannerRepo(enrollments={})
    scope = AuditScope(engine="classic", user_id=99999)
    plan = await AuditPlanner(repo).build(scope)
    assert plan.steps == []


async def test_build_user_scope_raises_for_repo_without_list_enrollments():
    """JsonRepo doesn't have list_enrollments — should raise NotImplementedError."""
    from audit.repos.json_repo import JsonRepo
    from pathlib import Path
    DUMPS = Path(__file__).resolve().parent.parent.parent / "dumps"
    repo = JsonRepo(
        submission_path=DUMPS / "classic_submissions.json",
        quizzes_path=DUMPS / "classic_quizzes.json",
        courses_path=DUMPS / "courses.json",
    )
    scope = AuditScope(engine="classic", user_id=9448)
    with pytest.raises(NotImplementedError):
        await AuditPlanner(repo).build(scope)


async def test_build_user_term_scope_filters_enrollments_by_term():
    """user_id + term_id should only audit courses in that term."""
    term_courses = [_course(12977, term_id=117)]
    other_courses = [_course(12978, term_id=116)]
    enrollments_5961 = [_enrollment(5961, 12977), _enrollment(5961, 12978)]
    repo = FakePlannerRepo(
        courses={117: term_courses, 116: other_courses},
        enrollments={5961: enrollments_5961},
    )
    scope = AuditScope(engine="classic", user_id=5961, term_id=117)
    plan = await AuditPlanner(repo).build(scope)

    # Only course 12977 is in term 117
    assert len(plan.steps) == 1
    assert plan.steps[0].course_id == 12977


# ---------------------------------------------------------------------------
# build() — query-based scopes (fuzzy search)
# ---------------------------------------------------------------------------

async def test_build_term_query_resolves_and_fans_out():
    """term_query matching two terms → steps for courses in both terms."""
    terms = [_term(116, "2025-2026 - Fall"), _term(117, "2025-2026 - Spring")]
    courses_116 = [_course(12900, term_id=116)]
    courses_117 = [_course(12977, term_id=117)]
    repo = FakePlannerRepo(
        terms=terms,
        courses={116: courses_116, 117: courses_117},
    )
    scope = AuditScope(engine="classic", term_query="2025-2026")
    plan = await AuditPlanner(repo).build(scope)

    assert len(plan.steps) == 2
    assert {s.course_id for s in plan.steps} == {12900, 12977}


async def test_build_course_query_delegates_to_search_courses():
    courses = [_course(12977)]
    repo = FakePlannerRepo(
        search_courses_result=courses,
        courses_by_id={12977: _course(12977)},
    )
    scope = AuditScope(engine="classic", term_id=117, course_query="Moral")
    plan = await AuditPlanner(repo).build(scope)

    assert len(repo.search_courses_calls) == 1
    assert repo.search_courses_calls[0] == ("Moral", 117)
    assert len(plan.steps) == 1
    assert plan.steps[0].course_id == 12977


async def test_build_course_query_without_term_raises_value_error():
    repo = FakePlannerRepo()
    scope = AuditScope(engine="classic", course_query="Moral")
    with pytest.raises(ValueError, match="term_id"):
        await AuditPlanner(repo).build(scope)


async def test_build_quiz_query_resolves_locally():
    quizzes = [_quiz(48379), _quiz(48380)]
    repo = FakePlannerRepo(quizzes={12977: quizzes})
    scope = AuditScope(engine="classic", course_id=12977, quiz_query="Quiz 48")
    plan = await AuditPlanner(repo).build(scope)

    # Both quizzes match "Quiz 48" — two QUIZ steps
    assert len(plan.steps) == 2
    assert all(s.kind == StepKind.QUIZ for s in plan.steps)
    assert {s.quiz_id for s in plan.steps} == {48379, 48380}


async def test_build_user_query_single_match_builds_user_plan():
    """Single user match → normal single-user enrollment traversal."""
    user = _user(5961, "McCarthy, Patrick")
    enrollments = [_enrollment(5961, 12977)]
    repo = FakePlannerRepo(
        search_users_result=[user],
        enrollments={5961: enrollments},
    )
    scope = AuditScope(engine="classic", user_query="McCarthy")
    plan = await AuditPlanner(repo).build(scope)

    assert len(plan.steps) == 1
    assert plan.steps[0].kind == StepKind.USER
    assert plan.steps[0].user_id == 5961


async def test_build_user_query_no_matches_raises_resolve_error():
    repo = FakePlannerRepo(search_users_result=[])
    scope = AuditScope(engine="classic", user_query="Zzzznotaname")
    with pytest.raises(ResolveError):
        await AuditPlanner(repo).build(scope)


# ---------------------------------------------------------------------------
# Multi-user deduplication
# ---------------------------------------------------------------------------

async def test_multi_user_deduplicates_shared_courses():
    """
    3 users all enrolled in course 12977, plus each has 1 unique course.
    Result should be 4 unique course steps, not 3×3=9.
    """
    users = [_user(1, "A"), _user(2, "B"), _user(3, "C")]
    enrollments = {
        1: [_enrollment(1, 12977), _enrollment(1, 12978)],
        2: [_enrollment(2, 12977), _enrollment(2, 12979)],
        3: [_enrollment(3, 12977), _enrollment(3, 12980)],
    }
    repo = FakePlannerRepo(
        search_users_result=users,
        enrollments=enrollments,
    )
    scope = AuditScope(engine="classic", user_query="Test")
    plan = await AuditPlanner(repo).build(scope)

    # 4 unique courses: 12977, 12978, 12979, 12980
    assert len(plan.steps) == 4
    assert {s.course_id for s in plan.steps} == {12977, 12978, 12979, 12980}


async def test_multi_user_step_carries_correct_user_ids_set():
    """The shared course step should contain all users enrolled in it."""
    users = [_user(1, "A"), _user(2, "B"), _user(3, "C")]
    enrollments = {
        1: [_enrollment(1, 12977)],
        2: [_enrollment(2, 12977)],
        3: [_enrollment(3, 12977)],
    }
    repo = FakePlannerRepo(
        search_users_result=users,
        enrollments=enrollments,
    )
    scope = AuditScope(engine="classic", user_query="Test")
    plan = await AuditPlanner(repo).build(scope)

    assert len(plan.steps) == 1
    step = plan.steps[0]
    assert step.user_ids == frozenset({1, 2, 3})
    assert step.user_id is None  # single user_id not set for multi-user steps


async def test_multi_user_each_unique_course_has_correct_user():
    """Each user's unique course step should have only that user's ID."""
    users = [_user(1, "A"), _user(2, "B")]
    enrollments = {
        1: [_enrollment(1, 12977)],  # unique to user 1
        2: [_enrollment(2, 12978)],  # unique to user 2
    }
    repo = FakePlannerRepo(
        search_users_result=users,
        enrollments=enrollments,
    )
    scope = AuditScope(engine="classic", user_query="Test")
    plan = await AuditPlanner(repo).build(scope)

    by_course = {s.course_id: s for s in plan.steps}
    assert by_course[12977].user_ids == frozenset({1})
    assert by_course[12978].user_ids == frozenset({2})


async def test_multi_user_no_enrollments_returns_empty_plan():
    users = [_user(1, "A"), _user(2, "B")]
    repo = FakePlannerRepo(
        search_users_result=users,
        enrollments={},
    )
    scope = AuditScope(engine="classic", user_query="Test")
    plan = await AuditPlanner(repo).build(scope)
    assert plan.steps == []


async def test_multi_user_enrollment_fetches_are_concurrent():
    """All users' enrollments are fetched (one list_enrollments call per user)."""
    users = [_user(i, f"User {i}") for i in range(5)]
    enrollments = {i: [_enrollment(i, 12977 + i)] for i in range(5)}
    repo = FakePlannerRepo(
        search_users_result=users,
        enrollments=enrollments,
    )
    scope = AuditScope(engine="classic", user_query="User")
    await AuditPlanner(repo).build(scope)

    # Exactly one list_enrollments call per user
    called_user_ids = {call[0] for call in repo.list_enrollments_calls}
    assert called_user_ids == {0, 1, 2, 3, 4}


# ---------------------------------------------------------------------------
# Empty scope raises ValueError
# ---------------------------------------------------------------------------

async def test_build_empty_scope_raises_value_error():
    repo = FakePlannerRepo()
    scope = AuditScope(engine="classic")
    with pytest.raises(ValueError):
        await AuditPlanner(repo).build(scope)
