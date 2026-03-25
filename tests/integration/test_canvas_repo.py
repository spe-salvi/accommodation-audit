import pytest

from audit.repos.base import AccommodationType
from audit.services.accommodations import AccommodationService


# ---------------------------------------------------------------------------
# Submissions
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_list_submissions_new(canvas_repo):
    submissions = await canvas_repo.list_submissions(
        course_id=12977, quiz_id=189437, engine="new"
    )
    assert len(submissions) > 0
    assert all(s.course_id == 12977 for s in submissions)
    assert all(s.quiz_id == 189437 for s in submissions)
    assert all(s.engine == "new" for s in submissions)


@pytest.mark.integration
async def test_list_submissions_classic(canvas_repo):
    submissions = await canvas_repo.list_submissions(
        course_id=12977, quiz_id=48379, engine="classic"
    )
    assert len(submissions) > 0
    assert all(s.course_id == 12977 for s in submissions)
    assert all(s.quiz_id == 48379 for s in submissions)
    assert all(s.engine == "classic" for s in submissions)


# ---------------------------------------------------------------------------
# Quizzes
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_list_quizzes_new(canvas_repo):
    quizzes = await canvas_repo.list_quizzes(course_id=12977, engine="new")
    quiz_ids = {q.quiz_id for q in quizzes}
    assert 189437 in quiz_ids
    assert all(q.course_id == 12977 for q in quizzes)


@pytest.mark.integration
async def test_list_quizzes_classic(canvas_repo):
    quizzes = await canvas_repo.list_quizzes(course_id=12977, engine="classic")
    quiz_ids = {q.quiz_id for q in quizzes}
    assert 48379 in quiz_ids
    assert all(q.course_id == 12977 for q in quizzes)


# ---------------------------------------------------------------------------
# Courses
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_list_courses(canvas_repo):
    courses = await canvas_repo.list_courses(term_id=117, engine="new")
    course_ids = {c.course_id for c in courses}
    assert 12977 in course_ids
    assert all(c.enrollment_term_id == 117 for c in courses)


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_list_items_new(canvas_repo):
    items = await canvas_repo.list_items(
        course_id=12977, quiz_id=189437, engine="new"
    )
    assert len(items) > 0
    assert all(i.course_id == 12977 for i in items)
    assert all(i.quiz_id == 189437 for i in items)


# ---------------------------------------------------------------------------
# Full audit service smoke test
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_audit_quiz_new_via_canvas_repo(canvas_repo):
    svc = AccommodationService(canvas_repo)
    # NOTE: EXTRA_TIME for new quizzes requires participants endpoint
    # which needs the LTI token (Phase 3). Rows will exist but
    # has_accommodation will be False until then.
    rows = await svc.audit_quiz(
        course_id=12977,
        quiz_id=189437,
        engine="new",
        accommodation_types=[
            AccommodationType.EXTRA_ATTEMPT,
        ],
    )
    assert len(rows) > 0
    assert all(r.course_id == 12977 for r in rows)
    assert all(r.quiz_id == 189437 for r in rows)


@pytest.mark.integration
async def test_audit_quiz_classic_via_canvas_repo(canvas_repo):
    svc = AccommodationService(canvas_repo)
    rows = await svc.audit_quiz(
        course_id=12977,
        quiz_id=48379,
        engine="classic",
        accommodation_types=[
            AccommodationType.EXTRA_TIME,
            AccommodationType.EXTRA_ATTEMPT,
        ],
    )
    assert len(rows) > 0
    assert all(r.course_id == 12977 for r in rows)
    assert all(r.quiz_id == 48379 for r in rows)
