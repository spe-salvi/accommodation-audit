"""
Unit tests for bounded concurrency in AccommodationService.

The semaphore now gates courses (not quizzes) — at most N courses
are processed concurrently in audit_term. Within each course, quizzes
run sequentially.

Tests that:
  - The semaphore limits concurrent courses in audit_term
  - A limit of 1 forces sequential course processing
  - Results are correct regardless of execution order
  - audit_course still produces rows for all quizzes
  - AccommodationService works without an explicit semaphore

Strategy: patch _audit_course_with_semaphore with a mock that acquires
the semaphore and records peak in-flight count with a brief sleep so
the event loop can interleave tasks.
"""

import asyncio

from audit.services.accommodations import AccommodationService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_concurrency_tracking_patch(svc: AccommodationService) -> dict:
    """
    Replace svc._audit_course_with_semaphore with a mock that:
      - Acquires the semaphore (as the real method does)
      - Tracks how many calls are in-flight simultaneously
      - Records the peak concurrency seen
      - Holds the slot briefly so other tasks can queue up
      - Returns [] (no rows needed for concurrency tests)

    Returns in_flight counter dict.
    """
    in_flight = {"count": 0, "peak": 0}

    async def tracked(**kwargs):
        async with svc._semaphore:
            in_flight["count"] += 1
            in_flight["peak"] = max(in_flight["peak"], in_flight["count"])
            await asyncio.sleep(0.01)
            in_flight["count"] -= 1
        return []

    svc._audit_course_with_semaphore = tracked
    return in_flight


# ---------------------------------------------------------------------------
# Semaphore limits concurrent courses
# ---------------------------------------------------------------------------

async def test_audit_term_respects_semaphore_limit(new_repo):
    """
    With a semaphore limit of 2, peak concurrency should never exceed 2
    even when the term has many courses.
    """
    limit = 2
    svc = AccommodationService(new_repo, semaphore=asyncio.Semaphore(limit))
    in_flight = _make_concurrency_tracking_patch(svc)

    await svc.audit_term(term_id=117, engine="new")

    assert in_flight["peak"] <= limit


async def test_audit_term_limit_1_is_sequential(new_repo):
    """A semaphore of 1 should process courses one at a time."""
    svc = AccommodationService(new_repo, semaphore=asyncio.Semaphore(1))
    in_flight = _make_concurrency_tracking_patch(svc)

    await svc.audit_term(term_id=117, engine="new")

    assert in_flight["peak"] == 1


async def test_audit_term_runs_courses_concurrently(new_repo):
    """
    With a generous semaphore limit, multiple courses should be
    in-flight simultaneously.
    """
    limit = 20
    svc = AccommodationService(new_repo, semaphore=asyncio.Semaphore(limit))
    in_flight = _make_concurrency_tracking_patch(svc)

    await svc.audit_term(term_id=117, engine="new")

    # Term 117 has multiple courses — peak should be > 1 with a big limit.
    assert in_flight["peak"] > 1


# ---------------------------------------------------------------------------
# Correctness
# ---------------------------------------------------------------------------

async def test_audit_course_processes_all_quizzes(new_repo):
    """audit_course should produce rows for all quizzes in the course."""
    svc = AccommodationService(new_repo)
    quizzes = await new_repo.list_quizzes(course_id=12977, engine="new")
    rows = await svc.audit_course(course_id=12977, engine="new")

    quiz_ids_with_rows = {row.quiz_id for row in rows}
    assert len(quiz_ids_with_rows) > 0
    assert all(qid in {q.quiz_id for q in quizzes} for qid in quiz_ids_with_rows)


async def test_audit_course_results_correct_under_concurrency(new_repo):
    """
    Sequential (limit=1) and concurrent (limit=10) term audits should
    produce the same row count.
    """
    svc_seq = AccommodationService(new_repo, semaphore=asyncio.Semaphore(1))
    rows_seq = await svc_seq.audit_course(course_id=12977, engine="new")

    svc_con = AccommodationService(new_repo, semaphore=asyncio.Semaphore(10))
    rows_con = await svc_con.audit_course(course_id=12977, engine="new")

    assert len(rows_seq) == len(rows_con)


async def test_audit_term_results_correct_under_concurrency(new_repo):
    """
    Term-level concurrent execution should produce the same row count
    as sequential execution.
    """
    svc_seq = AccommodationService(new_repo, semaphore=asyncio.Semaphore(1))
    rows_seq = await svc_seq.audit_term(term_id=117, engine="new")

    svc_con = AccommodationService(new_repo, semaphore=asyncio.Semaphore(10))
    rows_con = await svc_con.audit_term(term_id=117, engine="new")

    assert len(rows_seq) == len(rows_con)


# ---------------------------------------------------------------------------
# Default semaphore
# ---------------------------------------------------------------------------

async def test_default_semaphore_is_created_when_none_provided(new_repo):
    """AccommodationService should work without an explicit semaphore."""
    svc = AccommodationService(new_repo)
    rows = await svc.audit_course(course_id=12977, engine="new")
    assert isinstance(rows, list)
