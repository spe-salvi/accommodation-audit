"""
Unit tests for AccommodationService.audit_user.

Tests cover:
  - quiz scope: returns only that user's rows for one quiz
  - course scope: returns only that user's rows for one course
  - enrollment scope: uses list_enrollments to find courses, audits them
  - spell-check rows excluded (user_id=None filtered out)
  - user with no submissions returns empty list
  - user not in enrollment list returns empty list
  - NotImplementedError raised for JsonRepo (no list_enrollments)
  - multiple enrollments → multiple courses audited

Uses JsonRepo for quiz/course scope tests (no enrollment lookup needed).
Uses a FakeEnrollmentRepo for enrollment-scope tests to simulate
list_enrollments without network calls.
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest

from audit.models.audit import AuditRow
from audit.models.canvas import Course, Enrollment, NewQuizItem, Participant, Quiz, Submission
from audit.repos.base import AccommodationRepo, AccommodationType
from audit.services.accommodations import AccommodationService

DUMPS_DIR = Path(__file__).resolve().parent.parent.parent
PATH = DUMPS_DIR / "dumps"


# ---------------------------------------------------------------------------
# Fake enrollment repo
# ---------------------------------------------------------------------------

class FakeEnrollmentRepo(AccommodationRepo):
    """
    Wraps a JsonRepo and adds a fake list_enrollments method.
    Allows testing the enrollment-scope path of audit_user without
    hitting the Canvas API.
    """

    def __init__(self, inner: AccommodationRepo, enrollments: list[Enrollment]):
        self._inner = inner
        self._enrollments = enrollments

    async def list_enrollments(
        self, user_id: int, *, term_id: int | None = None
    ) -> list[Enrollment]:
        result = [e for e in self._enrollments if e.user_id == user_id]
        if term_id is not None:
            # Simulate server-side term filtering by checking enrollment_term_id
            # on the course objects. Use "classic" engine as the catalog key —
            # course term membership is engine-independent in JsonRepo.
            courses = await self._inner.list_courses(term_id=term_id, engine="classic")
            if not courses:
                # Fall back to "new" engine if classic returns nothing
                courses = await self._inner.list_courses(term_id=term_id, engine="new")
            valid_course_ids = {c.course_id for c in courses}
            result = [e for e in result if e.course_id in valid_course_ids]
        return result

    async def get_course_by_id(self, course_id: int) -> Optional[Course]:
        # Fall back to get_course with term_id=117 (our test term)
        return await self._inner.get_course(
            term_id=117, course_id=course_id, engine="classic"
        )

    # Delegate all AccommodationRepo methods to inner repo
    async def list_participants(self, **kwargs): return await self._inner.list_participants(**kwargs)
    async def get_participant(self, **kwargs): return await self._inner.get_participant(**kwargs)
    async def list_submissions(self, **kwargs): return await self._inner.list_submissions(**kwargs)
    async def get_submission(self, **kwargs): return await self._inner.get_submission(**kwargs)
    async def list_items(self, **kwargs): return await self._inner.list_items(**kwargs)
    async def list_quizzes(self, **kwargs): return await self._inner.list_quizzes(**kwargs)
    async def get_quiz(self, **kwargs): return await self._inner.get_quiz(**kwargs)
    async def list_courses(self, **kwargs): return await self._inner.list_courses(**kwargs)
    async def get_course(self, **kwargs): return await self._inner.get_course(**kwargs)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

from audit.repos.json_repo import JsonRepo

@pytest.fixture
def new_repo() -> JsonRepo:
    return JsonRepo(
        participant_path=PATH / "participants.json",
        submission_path=PATH / "new_submissions.json",
        items_path=PATH / "new_items.json",
        quizzes_path=PATH / "new_quizzes.json",
        courses_path=PATH / "courses.json",
    )

@pytest.fixture
def classic_repo() -> JsonRepo:
    return JsonRepo(
        submission_path=PATH / "classic_submissions.json",
        quizzes_path=PATH / "classic_quizzes.json",
        courses_path=PATH / "courses.json",
    )


def _enrollments_for_user(user_id: int, course_ids: list[int]) -> list[Enrollment]:
    return [Enrollment(user_id=user_id, course_id=cid) for cid in course_ids]


# ---------------------------------------------------------------------------
# Quiz scope
# ---------------------------------------------------------------------------

async def test_audit_user_quiz_scope_returns_only_that_user(new_repo):
    """
    audit_user with quiz+course scope should return only rows for the
    requested user, not all users in the quiz.
    """
    svc = AccommodationService(new_repo)
    user_id = 9448

    all_rows = await svc.audit_quiz(
        course_id=12977, quiz_id=189437, engine="new",
        accommodation_types=[AccommodationType.EXTRA_TIME],
    )
    user_rows = await svc.audit_user(
        user_id=user_id, engine="new",
        course_id=12977, quiz_id=189437,
        accommodation_types=[AccommodationType.EXTRA_TIME],
    )

    # All returned rows belong to the requested user
    assert all(r.user_id == user_id for r in user_rows)
    # Fewer rows than the full quiz audit (other users excluded)
    assert len(user_rows) < len(all_rows)


async def test_audit_user_quiz_scope_no_rows_for_absent_user(classic_repo):
    """A user not in the quiz submissions should produce no rows."""
    svc = AccommodationService(classic_repo)
    quizzes = await classic_repo.list_quizzes(course_id=12977, engine="classic")
    quiz_id = quizzes[0].quiz_id

    rows = await svc.audit_user(
        user_id=99999,
        engine="classic",
        course_id=12977,
        quiz_id=quiz_id,
        accommodation_types=[AccommodationType.EXTRA_TIME],
    )
    assert rows == []


# ---------------------------------------------------------------------------
# Course scope
# ---------------------------------------------------------------------------

async def test_audit_user_course_scope_returns_only_that_user(classic_repo):
    """audit_user with course scope filters to one user across all quizzes."""
    svc = AccommodationService(classic_repo)
    submissions = await classic_repo.list_submissions(
        course_id=12977, quiz_id=48379, engine="classic"
    )
    if not submissions:
        pytest.skip("No classic submissions in fixtures")

    user_id = submissions[0].user_id
    rows = await svc.audit_user(
        user_id=user_id,
        engine="classic",
        course_id=12977,
        accommodation_types=[AccommodationType.EXTRA_TIME, AccommodationType.EXTRA_ATTEMPT],
    )

    assert len(rows) > 0
    assert all(r.user_id == user_id for r in rows)


async def test_audit_user_course_scope_excludes_spell_check_rows(new_repo):
    """
    Spell-check rows have user_id=None and should never appear in
    user-scoped results.
    """
    svc = AccommodationService(new_repo)
    rows = await svc.audit_user(
        user_id=9448,
        engine="new",
        course_id=12977,
    )
    # No spell-check rows in user results
    assert not any(r.accommodation_type == AccommodationType.SPELL_CHECK for r in rows)
    # No rows with user_id=None
    assert not any(r.user_id is None for r in rows)


# ---------------------------------------------------------------------------
# Enrollment scope
# ---------------------------------------------------------------------------

async def test_audit_user_enrollment_scope_audits_enrolled_courses(new_repo):
    """
    With list_enrollments available, audit_user should audit only the
    courses the user is enrolled in.
    """
    user_id = 9448
    enrollments = _enrollments_for_user(user_id, [12977])
    repo = FakeEnrollmentRepo(new_repo, enrollments)
    svc = AccommodationService(repo)

    rows = await svc.audit_user(
        user_id=user_id,
        engine="new",
        accommodation_types=[AccommodationType.EXTRA_TIME],
    )

    assert all(r.user_id == user_id for r in rows)
    assert all(r.course_id == 12977 for r in rows)


async def test_audit_user_no_enrollments_returns_empty(new_repo):
    """A user with no active enrollments should produce no rows."""
    repo = FakeEnrollmentRepo(new_repo, enrollments=[])
    svc = AccommodationService(repo)

    rows = await svc.audit_user(
        user_id=99999,
        engine="classic",
    )
    assert rows == []


async def test_audit_user_enrollment_scope_multiple_courses(classic_repo):
    """
    With multiple enrollments, audit_user should audit all enrolled courses
    and return only that user's rows.
    """
    user_id = 9448
    # Enroll user in two courses (only 12977 has data in fixtures)
    enrollments = _enrollments_for_user(user_id, [12977, 12454])
    repo = FakeEnrollmentRepo(classic_repo, enrollments)
    svc = AccommodationService(repo)

    rows = await svc.audit_user(
        user_id=user_id,
        engine="classic",
        accommodation_types=[AccommodationType.EXTRA_TIME],
    )

    # All rows belong to the user
    assert all(r.user_id == user_id for r in rows)


async def test_audit_user_term_scope_filters_enrollments(new_repo):
    """
    When term_id is provided, only enrollments in that term are audited.
    """
    user_id = 9448
    # Enroll user in course 12977 (term 117)
    enrollments = _enrollments_for_user(user_id, [12977])
    repo = FakeEnrollmentRepo(new_repo, enrollments)
    svc = AccommodationService(repo)

    rows_term_117 = await svc.audit_user(
        user_id=user_id,
        engine="new",
        term_id=117,
        accommodation_types=[AccommodationType.EXTRA_ATTEMPT],
    )
    rows_term_999 = await svc.audit_user(
        user_id=user_id,
        engine="new",
        term_id=999,  # non-existent term
        accommodation_types=[AccommodationType.EXTRA_ATTEMPT],
    )

    assert len(rows_term_117) > 0
    assert rows_term_999 == []


# ---------------------------------------------------------------------------
# JsonRepo raises NotImplementedError for enrollment scope
# ---------------------------------------------------------------------------

async def test_audit_user_enrollment_scope_raises_for_json_repo(new_repo):
    """
    JsonRepo doesn't support list_enrollments. audit_user without a
    course_id or quiz_id should raise NotImplementedError.
    """
    svc = AccommodationService(new_repo)
    with pytest.raises(NotImplementedError, match="list_enrollments"):
        await svc.audit_user(user_id=9448, engine="new")


# ---------------------------------------------------------------------------
# Result correctness
# ---------------------------------------------------------------------------

async def test_audit_user_results_subset_of_full_course_audit(classic_repo):
    """
    The rows returned by audit_user(course) should be a subset of
    audit_course rows, filtered to the requested user.
    """
    svc = AccommodationService(classic_repo)
    user_id = 9448

    all_rows = await svc.audit_course(course_id=12977, engine="classic")
    user_rows = await svc.audit_user(
        user_id=user_id, engine="classic", course_id=12977,
    )

    all_user_rows_from_full = [r for r in all_rows if r.user_id == user_id]
    assert len(user_rows) == len(all_user_rows_from_full)
