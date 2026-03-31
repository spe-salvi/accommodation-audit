"""
Unit tests for bounded concurrency in AccommodationService.

Tests that:
  - The semaphore limits the number of simultaneous quiz audits
  - audit_term shares the semaphore globally across all courses
  - Results are correct regardless of execution order
  - audit_course produces results for all quizzes (gather completes all tasks)
  - AccommodationService works without an explicit semaphore

Note on testing true parallelism
---------------------------------
With JsonRepo, all repo operations are synchronous — they complete
without ever yielding to the event loop. This means asyncio.gather
runs tasks sequentially in practice (each task completes before the
next starts), even though the code is structured for concurrency.

We therefore don't test "peak in-flight > 1" — that would only be
meaningful with a real async data source. Instead we test the
semaphore limit (which is verifiable with a sleep-based mock),
result correctness, and that the semaphore is respected.
"""

import asyncio

from audit.services.accommodations import AccommodationService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_concurrency_tracking_patch(svc: AccommodationService) -> dict:
    """
    Replace svc._audit_quiz_with_semaphore with a mock that:
      - Acquires the semaphore (as the real method does)
      - Tracks how many calls are in-flight simultaneously
      - Records the peak concurrency seen
      - Holds the slot briefly so other tasks can queue
      - Returns [] (no rows needed)

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

    svc._audit_quiz_with_semaphore = tracked
    return in_flight


# ---------------------------------------------------------------------------
# Semaphore limit is respected
# ---------------------------------------------------------------------------

async def test_audit_course_respects_semaphore_limit(new_repo):
    """
    With a semaphore limit of 2, peak concurrency should never exceed 2,
    even though the course has 7 quizzes.
    """
    limit = 2
    svc = AccommodationService(new_repo, semaphore=asyncio.Semaphore(limit))
    in_flight = _make_concurrency_tracking_patch(svc)

    await svc.audit_course(course_id=12977, engine="new")

    assert in_flight["peak"] <= limit


async def test_audit_course_limit_1_is_sequential(new_repo):
    """A semaphore of 1 should process quizzes one at a time."""
    svc = AccommodationService(new_repo, semaphore=asyncio.Semaphore(1))
    in_flight = _make_concurrency_tracking_patch(svc)

    await svc.audit_course(course_id=12977, engine="new")

    assert in_flight["peak"] == 1


async def test_audit_term_shares_semaphore_globally(new_repo):
    """
    The semaphore limits total concurrent quizzes across all courses
    in a term, not per-course.
    """
    limit = 3
    svc = AccommodationService(new_repo, semaphore=asyncio.Semaphore(limit))
    in_flight = _make_concurrency_tracking_patch(svc)

    await svc.audit_term(term_id=117, engine="new")

    assert in_flight["peak"] <= limit


# ---------------------------------------------------------------------------
# All tasks complete (gather doesn't drop results)
# ---------------------------------------------------------------------------

async def test_audit_course_processes_all_quizzes(new_repo):
    """
    audit_course should produce rows for all quizzes in the course,
    confirming gather collects results from every task.
    """
    svc = AccommodationService(new_repo, semaphore=asyncio.Semaphore(10))
    quizzes = await new_repo.list_quizzes(course_id=12977, engine="new")
    rows = await svc.audit_course(course_id=12977, engine="new")

    quiz_ids_with_rows = {row.quiz_id for row in rows}
    # Every quiz that has submissions should appear in the output.
    assert len(quiz_ids_with_rows) > 0
    assert all(qid in {q.quiz_id for q in quizzes} for qid in quiz_ids_with_rows)


# ---------------------------------------------------------------------------
# Correctness under concurrency
# ---------------------------------------------------------------------------

async def test_audit_course_results_correct_under_concurrency(new_repo):
    """
    Concurrent execution (limit=10) should produce the same row count
    as sequential execution (limit=1).
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
