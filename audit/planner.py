"""
Audit planner — resolves input scope into an optimised execution plan.

The planner sits between the CLI and the service layer. It takes a
description of what the user wants to audit (``AuditScope``) and
produces a concrete list of steps (``AuditPlan``) that the service
layer can execute.

Why a planner?
--------------
``AccommodationService`` is the right home for *how* to evaluate
accommodations. The planner owns *what to fetch* and *in what order*.
Separating these concerns:

  - Keeps ``accommodations.py`` focused on evaluation logic.
  - Provides a single place to add new traversal strategies (e.g.
    fuzzy name resolution in Phase 12) without touching the service.
  - Makes traversal independently testable — plan construction is pure
    async logic with no evaluation side effects.

Traversal strategies
--------------------
The planner selects the minimal API traversal for the given scope:

  ┌──────────────────────┬────────────────────────────────────────────┐
  │ Scope                │ Traversal                                  │
  ├──────────────────────┼────────────────────────────────────────────┤
  │ term                 │ list_courses(term) → courses               │
  │ course               │ direct (course_id known)                   │
  │ quiz                 │ direct (course_id + quiz_id known)         │
  │ user                 │ list_enrollments(user) → courses           │
  │ user + term          │ list_enrollments(user, term) → courses     │
  │ user + course        │ direct (no enrollment lookup needed)       │
  │ user + course + quiz │ direct (no enrollment lookup needed)       │
  └──────────────────────┴────────────────────────────────────────────┘

Future traversal strategies (Phase 12):
  - Fuzzy name resolution: resolve term/course/quiz/user names to IDs
    before building the plan, then proceed as above.

Usage
-----
    scope = AuditScope(
        term_id=117,
        engine="classic",
        accommodation_types=[AccommodationType.EXTRA_TIME],
    )
    plan = await AuditPlanner(repo).build(scope)
    rows = await plan.execute(svc, show_progress=True)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING

from audit.models.audit import AuditRow
from audit.repos.base import AccommodationType

if TYPE_CHECKING:
    from audit.repos.base import AccommodationRepo
    from audit.services.accommodations import AccommodationService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AuditScope:
    """
    Describes what the user wants to audit.

    Accepts either integer IDs (direct) or string queries (fuzzy search).
    When a query field is set, ``AuditPlanner.build()`` resolves it to
    one or more IDs before building the execution plan. ID fields take
    precedence over query fields if both are set.

    Parameters
    ----------
    engine:
        One engine string ("new" or "classic"). The CLI passes one
        engine at a time; ``main.py`` calls the planner twice for
        ``--engine all``.
    accommodation_types:
        Types to evaluate. None means all types.
    user_id, term_id, course_id, quiz_id:
        Direct Canvas IDs. At most one of term_id/course_id/quiz_id
        should be set when user_id is None.
    user_query, term_query, course_query, quiz_query:
        Name-based search queries. Resolved to IDs by the planner via
        the Canvas search API before execution.

        Rules:
          - ``course_query`` requires ``term_id`` or ``term_query``
          - ``quiz_query`` requires ``course_id`` or ``course_query``
          - ``user_query`` may be used alone or with any scope modifier
    """
    engine: str
    accommodation_types: list[AccommodationType] | None = None
    # --- Direct IDs ---
    user_id: int | None = None
    term_id: int | None = None
    course_id: int | None = None
    quiz_id: int | None = None
    # --- Name queries (fuzzy search, resolved before execution) ---
    user_query: str | None = None
    term_query: str | None = None
    course_query: str | None = None
    quiz_query: str | None = None


# ---------------------------------------------------------------------------
# Plan steps
# ---------------------------------------------------------------------------

class StepKind(Enum):
    TERM   = auto()   # audit all courses in a term
    COURSE = auto()   # audit one course
    QUIZ   = auto()   # audit one quiz
    USER   = auto()   # audit one course filtered to one user


@dataclass(frozen=True)
class AuditStep:
    """
    A single unit of executable audit work.

    The planner produces a list of these; the plan executes them.
    Each step maps to exactly one ``AccommodationService`` method call.

    Single-user steps use ``user_id``. Multi-user steps (produced when
    a name query resolves to multiple users sharing a course) use
    ``user_ids`` — the course is audited once and rows are filtered to
    any user in the set, avoiding redundant course fetches.
    """
    kind: StepKind
    engine: str
    accommodation_types: list[AccommodationType] | None
    # Populated depending on kind:
    user_id: int | None = None
    user_ids: frozenset | None = None   # multi-user filter (overrides user_id)
    term_id: int | None = None
    course_id: int | None = None
    quiz_id: int | None = None
    # Pre-fetched Course object (avoids re-fetch in service layer):
    course: object | None = None  # audit.models.canvas.Course


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

@dataclass
class AuditPlan:
    """
    A resolved, executable list of audit steps.

    Built by ``AuditPlanner.build()`` and executed by ``execute()``.
    Holds enough context to run the audit without knowing how steps
    were derived — the planner handles derivation, the plan handles
    execution.
    """
    steps: list[AuditStep]
    scope: AuditScope

    @property
    def course_count(self) -> int:
        """Number of distinct course-level steps (for progress bar sizing)."""
        return sum(
            1 for s in self.steps
            if s.kind in (StepKind.COURSE, StepKind.USER)
        )

    async def execute(
        self,
        svc: AccommodationService,
        *,
        semaphore: asyncio.Semaphore,
        show_progress: bool = False,
    ) -> list[AuditRow]:
        """
        Execute all steps concurrently, gated by *semaphore*.

        Each step dispatches to the appropriate service method and
        all results are gathered and flattened into a single list.

        Parameters
        ----------
        svc:
            ``AccommodationService`` instance.
        semaphore:
            Shared semaphore bounding concurrent course-level work.
        show_progress:
            If True, show a tqdm bar advancing as each step completes.
        """
        tasks = [
            _execute_step(step, svc, semaphore=semaphore)
            for step in self.steps
        ]

        if not tasks:
            return []

        desc = _progress_desc(self.scope)

        if show_progress and len(tasks) > 1:
            from tqdm.asyncio import tqdm_asyncio
            results = await tqdm_asyncio.gather(
                *tasks,
                desc=desc,
                unit="course",
                total=len(tasks),
            )
        else:
            results = await asyncio.gather(*tasks)

        return [row for step_rows in results for row in step_rows]


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------

class AuditPlanner:
    """
    Resolves an ``AuditScope`` into an ``AuditPlan``.

    The planner is stateless between calls — create one instance per
    run and call ``build()`` once per engine (the CLI passes one engine
    at a time).

    Parameters
    ----------
    repo:
        Repository instance. Used to fetch enrollments and course
        objects needed to build the plan. Not used for evaluation.
    """

    def __init__(self, repo: AccommodationRepo) -> None:
        self._repo = repo

    async def build(self, scope: AuditScope) -> AuditPlan:
        """
        Resolve *scope* into an ``AuditPlan``.

        If any query fields are set (term_query, course_query, etc.),
        they are resolved to IDs first via the Canvas search API.
        When a query matches multiple entities, one sub-plan is built
        per result and all steps are merged into a single plan.

        Raises
        ------
        ResolveError
            If a name query matches no Canvas entities.
        NotImplementedError
            If a user-scoped enrollment traversal is requested but the
            repo does not support ``list_enrollments`` (e.g. JsonRepo).
        ValueError
            If the scope is invalid (e.g. quiz_id without course_id).
        """
        # Resolve any query fields to IDs before building the plan.
        # Each resolution may return multiple matches — we fan out.
        if _has_queries(scope):
            return await self._build_from_queries(scope)

        return await self._build_from_ids(scope)

    async def _build_from_queries(self, scope: AuditScope) -> AuditPlan:
        """
        Resolve query strings to IDs, then build plans for all matches.

        Resolution order:
          1. term_query  → list[Term]   → fan out per term_id
          2. course_query → list[Course] → fan out per course_id
          3. quiz_query  → list[Quiz]   → fan out per quiz_id
          4. user_query  → list[User]   → fan out per user_id

        Each resolved entity produces a sub-scope with the ID set, which
        is passed to ``_build_from_ids``. All resulting steps are merged.
        """
        from audit.resolver import Resolver
        resolver = Resolver(self._repo)
        all_steps: list[AuditStep] = []

        # --- Resolve term query → one plan per matching term ---
        if scope.term_query is not None and scope.term_id is None:
            terms = await resolver.resolve_term(scope.term_query)
            for term in terms:
                sub_scope = _replace_scope(scope, term_id=term.term_id, term_query=None)
                sub_plan = await self._build_from_queries(sub_scope)
                all_steps.extend(sub_plan.steps)
            return AuditPlan(steps=all_steps, scope=scope)

        # --- Resolve course query → one plan per matching course ---
        if scope.course_query is not None and scope.course_id is None:
            if scope.term_id is None:
                raise ValueError(
                    "course_query requires a term_id or term_query to be set. "
                    "Add --term to scope the course search."
                )
            courses = await resolver.resolve_course(
                scope.course_query, term_id=scope.term_id,
            )
            for course in courses:
                sub_scope = _replace_scope(
                    scope, course_id=course.course_id, course_query=None,
                )
                sub_plan = await self._build_from_queries(sub_scope)
                all_steps.extend(sub_plan.steps)
            return AuditPlan(steps=all_steps, scope=scope)

        # --- Resolve quiz query → one plan per matching quiz ---
        if scope.quiz_query is not None and scope.quiz_id is None:
            if scope.course_id is None:
                raise ValueError(
                    "quiz_query requires a course_id or course_query to be set. "
                    "Add --course to scope the quiz search."
                )
            quizzes = await resolver.resolve_quiz(
                scope.quiz_query,
                course_id=scope.course_id,
                engine=scope.engine,
            )
            for quiz in quizzes:
                sub_scope = _replace_scope(
                    scope, quiz_id=quiz.quiz_id, quiz_query=None,
                )
                sub_plan = await self._build_from_ids(sub_scope)
                all_steps.extend(sub_plan.steps)
            return AuditPlan(steps=all_steps, scope=scope)

        # --- Resolve user query → deduplicated multi-user plan ---
        if scope.user_query is not None and scope.user_id is None:
            users = await resolver.resolve_user(scope.user_query)
            if len(users) == 1:
                # Single match — build a normal single-user plan.
                sub_scope = _replace_scope(
                    scope, user_id=users[0].id, user_query=None,
                )
                return await self._build_from_ids(sub_scope)
            else:
                # Multiple matches — deduplicate courses across all users.
                return await self._build_multi_user_plan(scope, users)

        # All queries resolved — proceed with ID-based plan building.
        return await self._build_from_ids(scope)

    async def _build_from_ids(self, scope: AuditScope) -> AuditPlan:
        """Build a plan from a scope that contains only IDs (no query strings)."""
        types = scope.accommodation_types

        # --- Quiz scope (most specific, no traversal needed) ---
        if scope.quiz_id is not None:
            if scope.course_id is None:
                raise ValueError(
                    "quiz_id requires course_id to be set in the scope."
                )
            step = AuditStep(
                kind=StepKind.QUIZ if scope.user_id is None else StepKind.USER,
                engine=scope.engine,
                accommodation_types=types,
                user_id=scope.user_id,
                course_id=scope.course_id,
                quiz_id=scope.quiz_id,
            )
            return AuditPlan(steps=[step], scope=scope)

        # --- Course scope (direct, no traversal needed) ---
        if scope.course_id is not None:
            course = await self._fetch_course(scope.course_id)
            step = AuditStep(
                kind=StepKind.USER if scope.user_id is not None else StepKind.COURSE,
                engine=scope.engine,
                accommodation_types=types,
                user_id=scope.user_id,
                course_id=scope.course_id,
                course=course,
            )
            return AuditPlan(steps=[step], scope=scope)

        # --- Term scope (no user — list all courses in term) ---
        if scope.term_id is not None and scope.user_id is None:
            courses = await self._repo.list_courses(
                term_id=scope.term_id, engine=scope.engine,
            )
            steps = [
                AuditStep(
                    kind=StepKind.COURSE,
                    engine=scope.engine,
                    accommodation_types=types,
                    course_id=c.course_id,
                    course=c,
                )
                for c in courses
            ]
            logger.info(
                "AuditPlanner: term=%d → %d course(s) (%s)",
                scope.term_id, len(steps), scope.engine,
            )
            return AuditPlan(steps=steps, scope=scope)

        # --- User scope (enrollment traversal) ---
        if scope.user_id is not None:
            return await self._build_user_plan(scope)

        raise ValueError(
            "AuditScope must specify at least one of: "
            "term_id, course_id, quiz_id, user_id, or a query field."
        )

    async def _build_user_plan(self, scope: AuditScope) -> AuditPlan:
        """
        Build a plan for a user-scoped audit by resolving enrollments.

        Fetches the user's active enrollments (optionally filtered by
        term) and produces one COURSE/USER step per enrolled course.
        Also pre-fetches Course objects for each enrollment concurrently
        so the service layer doesn't need to re-fetch them.
        """
        if not hasattr(self._repo, "list_enrollments"):
            raise NotImplementedError(
                "User-scoped auditing with enrollment traversal requires "
                "a CanvasRepo with list_enrollments support. "
                "JsonRepo does not support this."
            )

        enrollments = await self._repo.list_enrollments(
            scope.user_id, term_id=scope.term_id,
        )

        if not enrollments:
            logger.info(
                "AuditPlanner: user_id=%d term_id=%s → no active enrollments",
                scope.user_id, scope.term_id,
            )
            return AuditPlan(steps=[], scope=scope)

        logger.info(
            "AuditPlanner: user_id=%d → %d active enrollment(s)",
            scope.user_id, len(enrollments),
        )

        # Deduplicate course IDs (a user can have multiple enrollment
        # records for the same course, e.g. different sections).
        seen: set[int] = set()
        unique_enrollments = []
        for e in enrollments:
            if e.course_id not in seen:
                seen.add(e.course_id)
                unique_enrollments.append(e)

        if len(unique_enrollments) < len(enrollments):
            logger.debug(
                "AuditPlanner: deduplicated %d → %d unique course(s)",
                len(enrollments), len(unique_enrollments),
            )

        # Pre-fetch Course objects concurrently (usually cache hits).
        course_objects = await asyncio.gather(
            *[self._fetch_course(e.course_id) for e in unique_enrollments],
            return_exceptions=True,
        )
        course_by_id = {
            e.course_id: c
            for e, c in zip(unique_enrollments, course_objects)
            if not isinstance(c, Exception) and c is not None
        }

        steps = [
            AuditStep(
                kind=StepKind.USER,
                engine=scope.engine,
                accommodation_types=scope.accommodation_types,
                user_id=scope.user_id,
                course_id=e.course_id,
                course=course_by_id.get(e.course_id),
            )
            for e in unique_enrollments
        ]

        return AuditPlan(steps=steps, scope=scope)

    async def _build_multi_user_plan(
        self, scope: AuditScope, users: list
    ) -> AuditPlan:
        """
        Build a deduplicated plan for multiple users from a name query.

        When a user query resolves to N users, naively building one USER
        step per (user, course) pair means the same course gets audited
        N times. This method:

          1. Fetches enrollments for all users concurrently.
          2. Builds a {course_id: set[user_id]} mapping.
          3. Produces one USER step per unique course, carrying the full
             set of matching user IDs. The step audits the course once
             and filters rows to any user in the set.

        Reduces course audits from (N × avg_courses_per_user) to the
        number of unique courses across all users.
        """
        if not hasattr(self._repo, "list_enrollments"):
            raise NotImplementedError(
                "Multi-user search requires a CanvasRepo with list_enrollments."
            )

        # Fetch enrollments for all users concurrently.
        enrollment_lists = await asyncio.gather(*[
            self._repo.list_enrollments(u.id, term_id=scope.term_id)
            for u in users
        ], return_exceptions=True)

        # Build course_id → set of user_ids mapping.
        course_to_users: dict[int, set[int]] = {}
        for user, result in zip(users, enrollment_lists):
            if isinstance(result, Exception):
                logger.warning(
                    "_build_multi_user_plan: enrollment fetch failed for "
                    "user_id=%d: %s", user.id, result,
                )
                continue
            for enrollment in result:
                course_to_users.setdefault(enrollment.course_id, set()).add(user.id)

        if not course_to_users:
            logger.info(
                "_build_multi_user_plan: no enrollments found for %d user(s)",
                len(users),
            )
            return AuditPlan(steps=[], scope=scope)

        logger.info(
            "_build_multi_user_plan: %d user(s) → %d unique course(s)",
            len(users), len(course_to_users),
        )

        # Pre-fetch Course objects concurrently (usually persistent cache hits).
        course_ids = list(course_to_users.keys())
        course_objects = await asyncio.gather(*[
            self._fetch_course(cid) for cid in course_ids
        ], return_exceptions=True)
        course_by_id = {
            cid: c
            for cid, c in zip(course_ids, course_objects)
            if not isinstance(c, Exception) and c is not None
        }

        steps = [
            AuditStep(
                kind=StepKind.USER,
                engine=scope.engine,
                accommodation_types=scope.accommodation_types,
                user_ids=frozenset(course_to_users[cid]),
                course_id=cid,
                course=course_by_id.get(cid),
            )
            for cid in course_ids
        ]
        return AuditPlan(steps=steps, scope=scope)

    async def _fetch_course(self, course_id: int):
        """Fetch a Course object, falling back gracefully on error."""
        if hasattr(self._repo, "get_course_by_id"):
            try:
                return await self._repo.get_course_by_id(course_id)
            except Exception as exc:
                logger.warning(
                    "AuditPlanner: could not fetch course_id=%d: %s",
                    course_id, exc,
                )
        return None


# ---------------------------------------------------------------------------
# Step execution
# ---------------------------------------------------------------------------

async def _execute_step(
    step: AuditStep,
    svc: AccommodationService,
    *,
    semaphore: asyncio.Semaphore,
) -> list[AuditRow]:
    """
    Execute a single ``AuditStep`` via the service layer.

    All course-level work is gated by the semaphore. Quiz-level steps
    are not semaphore-gated since they're already maximally specific.
    """
    types = step.accommodation_types

    if step.kind == StepKind.QUIZ:
        return await svc.audit_quiz(
            course_id=step.course_id,
            quiz_id=step.quiz_id,
            engine=step.engine,
            accommodation_types=types,
            _course=step.course,
        )

    if step.kind == StepKind.COURSE:
        async with semaphore:
            return await svc.audit_course(
                course_id=step.course_id,
                engine=step.engine,
                accommodation_types=types,
                _course=step.course,
            )

    if step.kind == StepKind.USER:
        # Course filtered to one or more users — gated by semaphore.
        async with semaphore:
            rows = await svc.audit_course(
                course_id=step.course_id,
                engine=step.engine,
                accommodation_types=types,
                _course=step.course,
            )
            return _filter_user(rows, step.user_id, step.user_ids)

    if step.kind == StepKind.TERM:
        # TERM steps are expanded into COURSE steps during plan building
        # and should not appear in the final step list. Guard against it.
        logger.warning("AuditPlanner: unexpected TERM step in execute — skipping")
        return []

    return []


def _filter_user(
    rows: list[AuditRow],
    user_id: int | None,
    user_ids: frozenset | None = None,
) -> list[AuditRow]:
    """
    Return only rows belonging to the specified user(s).

    Spell-check rows (user_id=None) are always excluded from user-scoped
    results — they are item-level configuration, not per-student.

    When ``user_ids`` is set (multi-user query), rows are kept if their
    user_id is in the set. When only ``user_id`` is set, only that user's
    rows are kept.
    """
    if user_ids is not None:
        return [r for r in rows if r.user_id is not None and r.user_id in user_ids]
    if user_id is not None:
        return [r for r in rows if r.user_id == user_id]
    return rows


def _has_queries(scope: AuditScope) -> bool:
    """Return True if any query field is set and its corresponding ID is not."""
    return (
        (scope.term_query   is not None and scope.term_id   is None) or
        (scope.course_query is not None and scope.course_id is None) or
        (scope.quiz_query   is not None and scope.quiz_id   is None) or
        (scope.user_query   is not None and scope.user_id   is None)
    )


def _replace_scope(scope: AuditScope, **kwargs) -> AuditScope:
    """Return a new AuditScope with the given fields replaced."""
    from dataclasses import replace
    return replace(scope, **kwargs)


def _progress_desc(scope: AuditScope) -> str:
    """Build a descriptive label for the tqdm progress bar."""
    if scope.user_query is not None:
        base = f"Auditing courses for '{scope.user_query}'"
    elif scope.user_id is not None:
        base = f"Auditing courses for user {scope.user_id}"
    elif scope.term_query is not None:
        base = f"Auditing courses (term '{scope.term_query}')"
    elif scope.term_id is not None:
        base = f"Auditing courses (term {scope.term_id})"
    elif scope.course_query is not None:
        base = f"Auditing '{scope.course_query}'"
    else:
        base = "Auditing"
    return f"{base} ({scope.engine})"
