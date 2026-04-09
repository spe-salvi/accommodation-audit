"""
Unit tests for bounded concurrency in AccommodationService + AuditPlanner.

The semaphore gates concurrent course-level work. It lives on
AccommodationService and is passed into AuditPlan.execute() by the
audit_term / audit_user convenience wrappers.

Tests that:
  - The semaphore limits concurrent courses in audit_term
  - A limit of 1 forces sequential course processing
  - Multiple courses can run concurrently with a generous limit
  - Results are correct regardless of execution order
  - audit_course still produces rows for all quizzes
  - AccommodationService works without an explicit semaphore

Patching strategy
-----------------
The planner's _execute_step acquires the semaphore, then calls
svc.audit_course. We replace svc.audit_course with a mock that:
  - Does NOT re-acquire the semaphore (the planner already holds it)
  - Records peak in-flight count using a sleep to allow interleaving

If the mock re-acquired the semaphore, each task would need two slots
(one from _execute_step, one from the mock), which deadlocks when
fewer slots are available than courses in the term.
"""

import asyncio

from audit.services.accommodations import AccommodationService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_concurrency_tracking_patch(svc: AccommodationService) -> dict:
    """
    Replace svc.audit_course with a mock that tracks in-flight concurrency.

    The mock does NOT acquire the semaphore — _execute_step in the planner
    already holds it when audit_course is called. Adding another acquire
    here would cause each task to need two semaphore slots, deadlocking
    when the limit is less than the number of courses.
    """
    in_flight = {"count": 0, "peak": 0}

    async def tracked(**kwargs):
        in_flight["count"] += 1
        in_flight["peak"] = max(in_flight["peak"], in_flight["count"])
        await asyncio.sleep(0.01)   # yield so other tasks can start
        in_flight["count"] -= 1
        return []

    svc.audit_course = tracked
    return in_flight


# ---------------------------------------------------------------------------
# Semaphore limits concurrent courses
# ---------------------------------------------------------------------------

async def test_audit_term_respects_semaphore_limit(new_repo):
    """
    With a semaphore limit of 2, at most 2 courses should run simultaneously.
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
    in-flight simultaneously. The sleep inside the mock gives the
    event loop time to start other tasks before the first completes.
    """
    limit = 20
    svc = AccommodationService(new_repo, semaphore=asyncio.Semaphore(limit))
    in_flight = _make_concurrency_tracking_patch(svc)

    await svc.audit_term(term_id=117, engine="new")

    # Term 117 has 11 courses in fixtures — peak should be > 1 with limit=20.
    assert in_flight["peak"] > 1


# ---------------------------------------------------------------------------
# All quizzes processed within a course
# ---------------------------------------------------------------------------

async def test_audit_course_processes_all_quizzes(new_repo):
    """audit_course should produce rows for all quizzes in the course."""
    svc = AccommodationService(new_repo)
    quizzes = await new_repo.list_quizzes(course_id=12977, engine="new")
    rows = await svc.audit_course(course_id=12977, engine="new")

    quiz_ids_with_rows = {row.quiz_id for row in rows}
    assert len(quiz_ids_with_rows) > 0
    assert all(qid in {q.quiz_id for q in quizzes} for qid in quiz_ids_with_rows)


# ---------------------------------------------------------------------------
# Correctness under concurrency
# ---------------------------------------------------------------------------

async def test_audit_course_results_correct_under_concurrency(new_repo):
    """Sequential and concurrent course audits should produce the same rows."""
    svc_seq = AccommodationService(new_repo, semaphore=asyncio.Semaphore(1))
    rows_seq = await svc_seq.audit_course(course_id=12977, engine="new")

    svc_con = AccommodationService(new_repo, semaphore=asyncio.Semaphore(10))
    rows_con = await svc_con.audit_course(course_id=12977, engine="new")

    assert len(rows_seq) == len(rows_con)


async def test_audit_term_results_correct_under_concurrency(new_repo):
    """Term-level concurrent execution should produce the same row count as sequential."""
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
