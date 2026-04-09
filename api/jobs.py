"""
Background job management for long-running audit tasks.

Since this is a single-user internal tool, we use asyncio background
tasks (no Celery, no Redis). Jobs are stored in an in-memory dict keyed
by UUID. On Render free tier, the process restarts on inactivity —
that's acceptable since users are actively waiting for results.

Job lifecycle
-------------
    pending → running → complete
                     ↘ error

SSE stream events
-----------------
Each job maintains an asyncio.Queue of progress dicts. The SSE
endpoint reads from this queue and forwards events to the client.

Event shapes:
    {"type": "start",    "total": 1142, "engine": "classic"}
    {"type": "progress", "completed": 142, "total": 1142, "pct": 12}
    {"type": "enrich",   "message": "Enriching 487 users..."}
    {"type": "complete", "row_count": 58174, "elapsed": 126.4,
                         "api_calls": 1142, "p_cache_hits": 3000,
                         "p_cache_misses": 4, "rt_cache_hits": 847}
    {"type": "error",    "message": "No term found matching 'Summerr'"}
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Job state
# ---------------------------------------------------------------------------

class JobStatus:
    PENDING  = "pending"
    RUNNING  = "running"
    COMPLETE = "complete"
    ERROR    = "error"


@dataclass
class Job:
    job_id: str
    status: str = JobStatus.PENDING
    rows: list[dict] = field(default_factory=list)
    error: str | None = None
    started_at: float = field(default_factory=time.perf_counter)
    elapsed: float = 0.0
    cancelled: bool = False
    # SSE event queue — SSE endpoint reads from this
    events: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=256))
    # Summary metrics written on completion
    metrics: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------

class JobStore:
    """Thread-safe (asyncio-safe) in-memory job registry."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def create(self) -> Job:
        job_id = str(uuid.uuid4())
        job = Job(job_id=job_id)
        self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def all(self) -> list[Job]:
        return list(self._jobs.values())

    def cleanup_old(self, max_age_seconds: int = 3600) -> int:
        """Remove jobs older than max_age_seconds. Returns count removed."""
        now = time.perf_counter()
        stale = [
            jid for jid, job in self._jobs.items()
            if now - job.started_at > max_age_seconds
        ]
        for jid in stale:
            del self._jobs[jid]
        return len(stale)


# Singleton — imported by routes
job_store = JobStore()


# ---------------------------------------------------------------------------
# Tqdm-compatible progress callback
# ---------------------------------------------------------------------------

class SSEProgressCallback:
    """
    Drop-in replacement for tqdm that pushes SSE events instead of
    printing to stdout. Passed to AuditPlan.execute() via a custom
    tqdm-like wrapper.
    """

    def __init__(self, job: Job, total: int, desc: str = "Auditing") -> None:
        self._job = job
        self._total = total
        self._completed = 0
        self._desc = desc

    def update(self, n: int = 1) -> None:
        self._completed += n
        pct = int(self._completed / self._total * 100) if self._total else 0
        event = {
            "type": "progress",
            "completed": self._completed,
            "total": self._total,
            "pct": pct,
            "desc": self._desc,
        }
        try:
            self._job.events.put_nowait(event)
        except asyncio.QueueFull:
            pass  # Drop event if queue is full — client will catch up

    def set_description(self, desc: str) -> None:
        self._desc = desc


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------

async def run_audit_job(job: Job, request_data: dict) -> None:
    """
    Execute the full audit pipeline as a background asyncio task.

    Pushes SSE events to job.events as work progresses. On completion,
    writes serialised rows to job.rows and metrics to job.metrics.
    """
    import httpx
    from audit.cache.persistent import CacheEntity, PersistentCache
    from audit.cache.runtime import RequestCache
    from audit.clients.canvas_client import CanvasClient
    from audit.config import settings
    from audit.enrichment import Enricher
    from audit.metrics import collect_metrics
    from audit.planner import AuditPlanner, AuditScope
    from audit.repos.base import AccommodationType
    from audit.repos.canvas_repo import CanvasRepo
    from audit.resolver import ResolveError
    from audit.services.accommodations import AccommodationService
    from main import _parse_id_or_query
    from pathlib import Path

    _TYPE_MAP = {
        "extra_time":    AccommodationType.EXTRA_TIME,
        "extra_attempt": AccommodationType.EXTRA_ATTEMPT,
        "spell_check":   AccommodationType.SPELL_CHECK,
    }
    _ENGINE_LIST = {
        "all":     ["new", "classic"],
        "new":     ["new"],
        "classic": ["classic"],
    }

    job.status = JobStatus.RUNNING
    audit_start = time.perf_counter()

    try:
        # Parse scope
        term_id,   term_query   = _parse_id_or_query(request_data.get("term"))
        course_id, course_query = _parse_id_or_query(request_data.get("course"))
        quiz_id,   quiz_query   = _parse_id_or_query(request_data.get("quiz"))
        user_id,   user_query   = _parse_id_or_query(request_data.get("user"))
        engines = _ENGINE_LIST.get(request_data.get("engine", "all"), ["new", "classic"])
        types = [_TYPE_MAP[t] for t in request_data.get("types", list(_TYPE_MAP.keys()))]

        persistent_cache = PersistentCache(Path(".cache"))
        runtime_cache    = RequestCache()

        async with httpx.AsyncClient() as http:
            client = CanvasClient(
                base_url=settings.canvas_base_url,
                token=settings.canvas_token,
                http=http,
                cache=runtime_cache,
            )
            repo    = CanvasRepo(client, account_id=settings.canvas_account_id,
                                 persistent_cache=persistent_cache)
            svc     = AccommodationService(repo)
            enricher = Enricher(repo=repo)

            # Build plans
            scopes = [
                AuditScope(
                    engine=eng,
                    accommodation_types=types,
                    term_id=term_id, term_query=term_query,
                    course_id=course_id, course_query=course_query,
                    quiz_id=quiz_id, quiz_query=quiz_query,
                    user_id=user_id, user_query=user_query,
                )
                for eng in engines
            ]

            planner = AuditPlanner(repo)
            plans   = await asyncio.gather(*[planner.build(s) for s in scopes])

            # Total steps across all engines for progress bar
            total_steps = sum(len(p.steps) for p in plans)
            await job.events.put({
                "type": "start",
                "total": total_steps,
                "engines": engines,
            })

            # Execute plans, collecting rows
            all_rows = []
            completed = 0
            for plan, scope in zip(plans, scopes):
                for step in plan.steps:
                    if job.cancelled:
                        raise asyncio.CancelledError()
                    from audit.planner import _execute_step
                    step_rows = await _execute_step(step, svc, semaphore=svc._semaphore)
                    all_rows.extend(step_rows)
                    completed += 1
                    pct = int(completed / total_steps * 100) if total_steps else 100
                    try:
                        job.events.put_nowait({
                            "type": "progress",
                            "completed": completed,
                            "total": total_steps,
                            "pct": pct,
                            "desc": f"Auditing ({scope.engine})",
                        })
                    except asyncio.QueueFull:
                        pass

            audit_elapsed = time.perf_counter() - audit_start

            # Enrich
            await job.events.put({"type": "enrich", "message": "Enriching display data..."})
            enrich_start = time.perf_counter()
            all_rows = await enricher.enrich(all_rows)
            enrich_elapsed = time.perf_counter() - enrich_start

            # Remove test student rows (user_id set but no SIS ID —
            # real students always have a SIS user ID at this institution)
            all_rows = _filter_reportable_rows(all_rows)

        # Collect metrics
        metrics = collect_metrics(
            client=client,
            runtime_cache=runtime_cache,
            persistent_cache=persistent_cache,
            enricher=enricher,
            audit_elapsed=audit_elapsed,
            enrich_elapsed=enrich_elapsed,
            write_elapsed=0.0,
            row_count=len(all_rows),
        )

        # Serialise rows
        job.rows = [_serialise_row(r) for r in all_rows]
        job.elapsed = time.perf_counter() - audit_start
        job.metrics = {
            "row_count":            metrics.row_count,
            "api_requests_made":    metrics.api_requests_made,
            "api_retries_fired":    metrics.api_retries_fired,
            "persistent_cache_hits":   metrics.persistent_cache_hits,
            "persistent_cache_misses": metrics.persistent_cache_misses,
            "runtime_cache_hits":   metrics.runtime_cache_hits,
            "runtime_cache_misses": metrics.runtime_cache_misses,
            "users_fetched":        metrics.users_fetched,
            "terms_fetched":        metrics.terms_fetched,
            "audit_elapsed":        audit_elapsed,
            "enrich_elapsed":       enrich_elapsed,
            "total_elapsed":        job.elapsed,
        }
        job.status = JobStatus.COMPLETE

        await job.events.put({
            "type":     "complete",
            "row_count": len(job.rows),
            "elapsed":   round(job.elapsed, 1),
            "metrics":   job.metrics,
        })

    except (ResolveError, ValueError) as exc:
        job.status = JobStatus.ERROR
        job.error  = str(exc)
        await job.events.put({"type": "error", "message": str(exc)})
        logger.warning("Audit job %s failed (scope): %s", job.job_id, exc)

    except asyncio.CancelledError:
        job.status = JobStatus.ERROR
        job.error  = "Audit cancelled."
        await job.events.put({"type": "error", "message": "Audit cancelled."})
        logger.info("Audit job %s was cancelled.", job.job_id)

    except Exception as exc:
        job.status = JobStatus.ERROR
        job.error  = str(exc)
        await job.events.put({"type": "error", "message": str(exc)})
        logger.exception("Audit job %s failed (unexpected)", job.job_id)


def _serialise_row(row) -> dict:
    """Convert an AuditRow dataclass to a JSON-serialisable dict."""
    return {
        "course_id":           row.course_id,
        "quiz_id":             row.quiz_id,
        "user_id":             row.user_id,
        "item_id":             row.item_id,
        "engine":              row.engine,
        "accommodation_type":  row.accommodation_type.value if row.accommodation_type else None,
        "has_accommodation":   row.has_accommodation,
        "details":             row.details or {},
        "completed":           row.completed,
        "attempts_left":       row.attempts_left,
        "enrollment_term_id":  row.enrollment_term_id,
        "term_name":           row.term_name,
        "course_name":         row.course_name,
        "course_code":         row.course_code,
        "sis_course_id":       row.sis_course_id,
        "quiz_title":          row.quiz_title,
        "quiz_due_at":         row.quiz_due_at,
        "quiz_lock_at":        row.quiz_lock_at,
        "user_name":           row.user_name,
        "sis_user_id":         row.sis_user_id,
    }


def _filter_reportable_rows(rows: list) -> list:
    """
    Remove rows that should not appear in reports:

    1. Test student rows — user_id is set but sis_user_id is null or the
       string 'None'. Real students at this institution always have a SIS
       user ID; the Canvas test student account does not.

    2. Spell-check rows (user_id=None) are kept — they represent quiz item
       configuration and are valid audit data.
    """
    def _keep(row) -> bool:
        if row.user_id is None:
            return True  # spell-check row — keep
        sis = row.sis_user_id
        if sis is None or str(sis) == 'None' or str(sis).strip() == '':
            return False  # test student — drop
        return True

    before = len(rows)
    filtered = [r for r in rows if _keep(r)]
    dropped = before - len(filtered)
    if dropped:
        logger.debug("_filter_reportable_rows: dropped %d test-student row(s)", dropped)
    return filtered
