"""
Core business logic for accommodation auditing.

This module contains ``AccommodationService`` — the central orchestrator
that evaluates whether students have accommodations applied to their
quizzes and produces structured audit results.

The service is organized into three concerns:

  1. **Context loading** — Fetching and assembling the data needed to
     evaluate accommodations (participants, submissions, items).
  2. **Evaluation** — Pure functions that inspect model data and return
     an ``AccommodationResult`` (has/doesn't have + details).
  3. **Audit composition** — Methods that combine context loading and
     evaluation across users, quizzes, courses, and terms to produce
     lists of ``AuditRow`` objects.

The service depends on ``AccommodationRepo`` (a protocol), so it works
identically whether the data comes from the Canvas API or local JSON.

Concurrency
-----------
``audit_course`` and ``audit_term`` run per-quiz work concurrently via
``asyncio.gather``. A shared ``asyncio.Semaphore`` bounds the number of
quizzes processed simultaneously across the entire run — including when
``audit_term`` fans out across multiple courses. The default limit is 10.

Pass a semaphore explicitly to share it across service instances, or
let the service create its own (useful for single-course audits).

Data source routing
-------------------
  +-----------------+---------+--------------------------------------------+
  | Type            | Engine  | Source                                     |
  +-----------------+---------+--------------------------------------------+
  | EXTRA_TIME      | new     | Participant (LTI API — enrollment fields)  |
  | EXTRA_TIME      | classic | Submission (Canvas API — extra_time field) |
  | EXTRA_ATTEMPT   | new     | Submission (Canvas API — extra_attempts)   |
  | EXTRA_ATTEMPT   | classic | Submission (Canvas API — extra_attempts)   |
  | SPELL_CHECK     | new     | Quiz item (Canvas API — interaction_data)  |
  | SPELL_CHECK     | classic | N/A (classic engine has no per-item config)|
  +-----------------+---------+--------------------------------------------+
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable, Iterable

from audit.models.audit import AuditRow, AuditRequest
from audit.models.canvas import Participant, Submission, NewQuizItem
from audit.repos.base import AccommodationRepo, AccommodationType

logger = logging.getLogger(__name__)

_DEFAULT_CONCURRENCY = 10


@dataclass(frozen=True)
class AccommodationResult:
    """
    The outcome of evaluating a single accommodation for a single user.

    ``has_accommodation`` is the boolean verdict; ``details`` carries
    the raw values that led to the decision (e.g., extra_time_in_seconds)
    for inclusion in audit output.
    """
    has_accommodation: bool
    details: dict


@dataclass(frozen=True)
class EvaluationContext:
    """
    The minimal data slice needed to evaluate one accommodation for one user.

    Which fields are populated depends on the accommodation type and engine:
        - Extra time (new):     needs ``participant``
        - Extra time (classic): needs ``submission``
        - Extra attempts:       needs ``submission`` (both engines)
        - Spell check:          needs ``items`` (per-item, not per-user)
    """
    engine: str
    participant: Participant | None = None
    submission: Submission | None = None
    items: list[NewQuizItem] | None = None


@dataclass(frozen=True)
class QuizAuditContext:
    """
    Pre-loaded data for auditing all users/items on a single quiz.

    Eagerly loading all participants, submissions, and items upfront
    minimizes API calls at the cost of memory — the right tradeoff when
    auditing hundreds of students per quiz.

    Note on participants:
        For new-engine quizzes, participants may be empty if the LTI
        client is not available. In that case, EXTRA_TIME rows will not
        be generated. EXTRA_ATTEMPT rows are always generated from
        submissions regardless of participant availability.
    """
    course_id: int
    quiz_id: int
    engine: str
    participants: list[Participant]
    submissions: list[Submission]
    items: list[NewQuizItem]
    submissions_by_user: dict[int, Submission]
    submissions_by_session: dict[tuple[str | None, str | None], Submission]


# Type alias for evaluator functions.
Evaluator = Callable[[EvaluationContext], AccommodationResult]


class AccommodationService:
    """
    Orchestrates accommodation evaluation and audit report generation.

    Parameters
    ----------
    repo:
        Data access layer. Works with both ``CanvasRepo`` and ``JsonRepo``.
    semaphore:
        Optional shared semaphore for bounding concurrent quiz processing.
        When None, a new semaphore with ``_DEFAULT_CONCURRENCY`` slots is
        created. Pass a shared instance when running multiple service calls
        in parallel (e.g., from a term-level audit across many courses) to
        enforce a global concurrency limit.
    """

    def __init__(
        self,
        repo: AccommodationRepo,
        *,
        semaphore: asyncio.Semaphore | None = None,
    ):
        self.repo = repo
        self._semaphore = semaphore or asyncio.Semaphore(_DEFAULT_CONCURRENCY)
        self._evaluators: dict[tuple[str, AccommodationType], Evaluator] = {
            ("new", AccommodationType.EXTRA_TIME): self._evaluate_extra_time_new,
            ("classic", AccommodationType.EXTRA_TIME): self._evaluate_extra_time_classic,
            ("new", AccommodationType.EXTRA_ATTEMPT): self._evaluate_extra_attempts,
            ("classic", AccommodationType.EXTRA_ATTEMPT): self._evaluate_extra_attempts,
        }

    # ----------------------------
    # Context builders / loaders
    # ----------------------------

    def _build_evaluation_context(
        self,
        *,
        engine: str,
        participant: Participant | None = None,
        submission: Submission | None = None,
        items: list[NewQuizItem] | None = None,
    ) -> EvaluationContext:
        """Construct an EvaluationContext from pre-fetched model objects."""
        return EvaluationContext(
            engine=engine,
            participant=participant,
            submission=submission,
            items=items,
        )

    async def _load_evaluation_context(
        self,
        *,
        course_id: int,
        quiz_id: int,
        user_id: int,
        engine: str,
        accommodation_type: AccommodationType,
    ) -> EvaluationContext:
        """
        Fetch only the data needed for a specific accommodation evaluation.

        This is the single-user path — loads just the participant,
        submission, or items needed for the requested accommodation type.

        EXTRA_ATTEMPT always reads from submissions regardless of engine.
        EXTRA_TIME reads from participants for new quizzes (LTI required)
        and from submissions for classic quizzes.
        """
        participant = None
        submission = None
        items = None

        if accommodation_type == AccommodationType.EXTRA_TIME:
            if engine == "new":
                participant = await self.repo.get_participant(
                    course_id=course_id,
                    quiz_id=quiz_id,
                    user_id=user_id,
                    engine=engine,
                )
            elif engine == "classic":
                submission = await self.repo.get_submission(
                    course_id=course_id,
                    quiz_id=quiz_id,
                    user_id=user_id,
                    engine=engine,
                )

        elif accommodation_type == AccommodationType.EXTRA_ATTEMPT:
            submission = await self.repo.get_submission(
                course_id=course_id,
                quiz_id=quiz_id,
                user_id=user_id,
                engine=engine,
            )

        elif accommodation_type == AccommodationType.SPELL_CHECK:
            if engine == "new":
                items = await self.repo.list_items(
                    course_id=course_id,
                    quiz_id=quiz_id,
                    engine=engine,
                )

        return self._build_evaluation_context(
            engine=engine,
            participant=participant,
            submission=submission,
            items=items,
        )

    async def _load_quiz_audit_context(
        self,
        *,
        course_id: int,
        quiz_id: int,
        engine: str,
        accommodation_types: list[AccommodationType] | None = None,
    ) -> QuizAuditContext:
        """
        Eagerly load all data for a quiz into a single context object.

        Participants are only fetched for new-engine quizzes when
        EXTRA_TIME is in the requested accommodation types, avoiding
        unnecessary LTI API calls when only EXTRA_ATTEMPT or SPELL_CHECK
        are requested.
        """
        needs_participants = (
            engine == "new"
            and (
                accommodation_types is None
                or AccommodationType.EXTRA_TIME in accommodation_types
            )
        )

        participants: list[Participant] = []
        if needs_participants:
            participants = await self.repo.list_participants(
                course_id=course_id,
                quiz_id=quiz_id,
                engine=engine,
            )

        submissions = await self.repo.list_submissions(
            course_id=course_id,
            quiz_id=quiz_id,
            engine=engine,
        )

        items: list[NewQuizItem] = []
        if engine == "new":
            items = await self.repo.list_items(
                course_id=course_id,
                quiz_id=quiz_id,
                engine=engine,
            )

        submissions_by_user = {s.user_id: s for s in submissions}
        submissions_by_session = {
            (s.participant_session_id, s.quiz_session_id): s
            for s in submissions
            if s.participant_session_id is not None or s.quiz_session_id is not None
        }

        return QuizAuditContext(
            course_id=course_id,
            quiz_id=quiz_id,
            engine=engine,
            participants=participants,
            submissions=submissions,
            items=items,
            submissions_by_user=submissions_by_user,
            submissions_by_session=submissions_by_session,
        )

    # ----------------------------
    # Single-result evaluation
    # ----------------------------

    def evaluate_models(
        self,
        *,
        accommodation_type: AccommodationType,
        ctx: EvaluationContext,
    ) -> AccommodationResult:
        """
        Dispatch to the correct evaluator based on engine and accommodation type.

        Returns a "not found" result if no evaluator is registered for
        the given combination (e.g., spell-check on classic quizzes).
        """
        handler = self._evaluators.get((ctx.engine, accommodation_type))
        if handler is None:
            return AccommodationResult(False, {})
        return handler(ctx)

    async def evaluate(
        self,
        *,
        course_id: int,
        quiz_id: int,
        user_id: int,
        engine: str,
        accommodation_type: AccommodationType,
    ) -> AccommodationResult:
        """
        Evaluate a single accommodation for a single user.

        Loads just the data needed and runs the evaluator. Useful for
        spot-checking individual students.
        """
        ctx = await self._load_evaluation_context(
            course_id=course_id,
            quiz_id=quiz_id,
            user_id=user_id,
            engine=engine,
            accommodation_type=accommodation_type,
        )
        return self.evaluate_models(
            accommodation_type=accommodation_type,
            ctx=ctx,
        )

    # ----------------------------
    # Audit APIs
    # ----------------------------

    async def audit_accommodation(self, request: AuditRequest) -> list[AuditRow]:
        """Audit a single accommodation type across all users on a quiz."""
        ctx = await self._load_quiz_audit_context(
            course_id=request.course_id,
            quiz_id=request.quiz_id,
            engine=request.engine,
            accommodation_types=[request.accommodation_type],
        )
        return self._audit_accommodation_with_quiz_context(
            ctx=ctx,
            accommodation_type=request.accommodation_type,
        )

    async def audit_quiz(
        self,
        *,
        course_id: int,
        quiz_id: int,
        engine: str,
        accommodation_types: Iterable[AccommodationType] | None = None,
    ) -> list[AuditRow]:
        """
        Audit all (or selected) accommodation types on a single quiz.

        If *accommodation_types* is None, all known types are evaluated.
        Spell-check is automatically excluded for classic quizzes since
        the classic engine does not expose per-item configuration.
        """
        if accommodation_types is None:
            requested = [
                AccommodationType.EXTRA_TIME,
                AccommodationType.EXTRA_ATTEMPT,
                AccommodationType.SPELL_CHECK,
            ]
        else:
            requested = list(accommodation_types)

        if engine != "new":
            requested = [
                a for a in requested
                if a != AccommodationType.SPELL_CHECK
            ]

        ctx = await self._load_quiz_audit_context(
            course_id=course_id,
            quiz_id=quiz_id,
            engine=engine,
            accommodation_types=requested,
        )

        rows: list[AuditRow] = []
        for accommodation_type in requested:
            rows.extend(
                self._audit_accommodation_with_quiz_context(
                    ctx=ctx,
                    accommodation_type=accommodation_type,
                )
            )
        return rows

    async def _audit_quiz_with_semaphore(
        self,
        *,
        course_id: int,
        quiz_id: int,
        engine: str,
        accommodation_types: list[AccommodationType] | None,
    ) -> list[AuditRow]:
        """
        Acquire the semaphore then run audit_quiz.

        This is the unit of work that gets passed to asyncio.gather in
        audit_course and audit_term. The semaphore ensures at most
        _DEFAULT_CONCURRENCY quizzes are processed at once globally,
        regardless of how many courses are running in parallel.
        """
        async with self._semaphore:
            return await self.audit_quiz(
                course_id=course_id,
                quiz_id=quiz_id,
                engine=engine,
                accommodation_types=accommodation_types,
            )

    async def audit_course(
        self,
        *,
        course_id: int,
        engine: str,
        accommodation_types: Iterable[AccommodationType] | None = None,
    ) -> list[AuditRow]:
        """
        Audit all quizzes in a course concurrently.

        Quizzes are processed in parallel, bounded by the shared semaphore.
        Results are gathered and flattened into a single list.
        """
        quizzes = await self.repo.list_quizzes(
            course_id=course_id,
            engine=engine,
        )

        types = list(accommodation_types) if accommodation_types is not None else None

        tasks = [
            self._audit_quiz_with_semaphore(
                course_id=course_id,
                quiz_id=quiz.quiz_id,
                engine=engine,
                accommodation_types=types,
            )
            for quiz in quizzes
        ]

        results = await asyncio.gather(*tasks)
        return [row for quiz_rows in results for row in quiz_rows]

    async def audit_term(
        self,
        *,
        term_id: int,
        engine: str,
        accommodation_types: Iterable[AccommodationType] | None = None,
    ) -> list[AuditRow]:
        """
        Audit all courses in a term concurrently.

        Courses are fetched sequentially (there are few of them), but
        the per-quiz work within each course runs concurrently, all
        sharing the same global semaphore.
        """
        courses = await self.repo.list_courses(
            term_id=term_id,
            engine=engine,
        )

        types = list(accommodation_types) if accommodation_types is not None else None

        tasks = [
            self.audit_course(
                course_id=course.course_id,
                engine=engine,
                accommodation_types=types,
            )
            for course in courses
        ]

        results = await asyncio.gather(*tasks)
        return [row for course_rows in results for row in course_rows]

    # ----------------------------
    # Row builders
    # ----------------------------

    def _build_spell_check_rows(self, *, ctx: QuizAuditContext) -> list[AuditRow]:
        """
        Build per-item audit rows for spell-check on essay questions.

        Spell-check is a quiz-level accommodation — either enabled or
        disabled on each essay question. Non-essay items are skipped.
        """
        if ctx.engine != "new":
            return []

        rows: list[AuditRow] = []

        for item in ctx.items:
            if item.interaction_type_slug != "essay":
                logger.debug(
                    "spell_check: skipping non-essay item item_id=%s quiz_id=%d",
                    item.item_id,
                    ctx.quiz_id,
                )
                continue

            enabled = bool(item.essay_spell_check_enabled)

            rows.append(
                AuditRow(
                    course_id=ctx.course_id,
                    quiz_id=ctx.quiz_id,
                    user_id=None,
                    item_id=item.item_id,
                    engine=ctx.engine,
                    accommodation_type=AccommodationType.SPELL_CHECK,
                    has_accommodation=enabled,
                    details={
                        "spell_check": enabled,
                        "position": item.position,
                    },
                    completed=None,
                )
            )

        return rows

    def _build_user_rows(
        self,
        *,
        ctx: QuizAuditContext,
        accommodation_type: AccommodationType,
    ) -> list[AuditRow]:
        """
        Build per-user audit rows for a given accommodation type.

        Routing:
          - EXTRA_TIME + new:     iterate participants (LTI data)
          - EXTRA_ATTEMPT + new:  iterate submissions (Canvas API)
          - anything + classic:   iterate submissions
        """
        rows: list[AuditRow] = []

        # --- New engine, EXTRA_TIME: source is participants ---
        if ctx.engine == "new" and accommodation_type == AccommodationType.EXTRA_TIME:
            for participant in ctx.participants:
                submission = self._match_submission(
                    engine=ctx.engine,
                    participant=participant,
                    submissions_by_user=ctx.submissions_by_user,
                    submissions_by_session=ctx.submissions_by_session,
                )

                eval_ctx = self._build_evaluation_context(
                    engine=ctx.engine,
                    participant=participant,
                    submission=submission,
                )

                result = self.evaluate_models(
                    accommodation_type=accommodation_type,
                    ctx=eval_ctx,
                )

                if not result.has_accommodation:
                    logger.debug(
                        "no accommodation: user_id=%d quiz_id=%d type=%s engine=new",
                        participant.user_id,
                        ctx.quiz_id,
                        accommodation_type.value,
                    )

                completed = submission.date == "past" if submission else None

                rows.append(
                    AuditRow(
                        course_id=ctx.course_id,
                        quiz_id=ctx.quiz_id,
                        user_id=participant.user_id,
                        item_id=None,
                        engine=ctx.engine,
                        accommodation_type=accommodation_type,
                        has_accommodation=result.has_accommodation,
                        details=result.details,
                        completed=completed,
                    )
                )
            return rows

        # --- All other cases: source is submissions ---
        for submission in ctx.submissions:
            eval_ctx = self._build_evaluation_context(
                engine=ctx.engine,
                participant=None,
                submission=submission,
            )

            result = self.evaluate_models(
                accommodation_type=accommodation_type,
                ctx=eval_ctx,
            )

            if not result.has_accommodation:
                logger.debug(
                    "no accommodation: user_id=%d quiz_id=%d type=%s engine=%s",
                    submission.user_id,
                    ctx.quiz_id,
                    accommodation_type.value,
                    ctx.engine,
                )

            rows.append(
                AuditRow(
                    course_id=ctx.course_id,
                    quiz_id=ctx.quiz_id,
                    user_id=submission.user_id,
                    item_id=None,
                    engine=ctx.engine,
                    accommodation_type=accommodation_type,
                    has_accommodation=result.has_accommodation,
                    details=result.details,
                    completed=submission.date == "past",
                )
            )

        return rows

    def _audit_accommodation_with_quiz_context(
        self,
        *,
        ctx: QuizAuditContext,
        accommodation_type: AccommodationType,
    ) -> list[AuditRow]:
        """Route to the correct row-builder based on accommodation type."""
        if accommodation_type == AccommodationType.SPELL_CHECK:
            return self._build_spell_check_rows(ctx=ctx)
        return self._build_user_rows(ctx=ctx, accommodation_type=accommodation_type)

    # ----------------------------
    # Matching
    # ----------------------------

    def _match_submission(
        self,
        *,
        engine: str,
        participant: Participant,
        submissions_by_user: dict[int, Submission],
        submissions_by_session: dict[tuple[str | None, str | None], Submission],
    ) -> Submission | None:
        """
        Find the submission that belongs to a given participant.

        For new-engine quizzes, tries session-based matching first then
        falls back to user_id. For classic quizzes, matches by user_id only.
        """
        if engine == "new":
            submission = submissions_by_session.get(
                (participant.participant_session_id, participant.quiz_session_id)
            )
            if submission is not None:
                return submission
            return submissions_by_user.get(participant.user_id)

        if engine == "classic":
            return submissions_by_user.get(participant.user_id)

        return None

    # ----------------------------
    # Accommodation evaluators
    # ----------------------------

    def _evaluate_extra_time_new(self, ctx: EvaluationContext) -> AccommodationResult:
        """Extra time for new-engine quizzes — reads from LTI participant."""
        participant = ctx.participant
        if participant is None:
            return AccommodationResult(False, {})

        has = (
            (participant.timer_multiplier_enabled and (participant.timer_multiplier_value or 0) > 1)
            or (participant.extra_time_enabled and (participant.extra_time_in_seconds or 0) > 0)
        )
        return AccommodationResult(
            has,
            {
                "timer_multiplier_value": participant.timer_multiplier_value,
                "extra_time_in_seconds": participant.extra_time_in_seconds,
            },
        )

    def _evaluate_extra_time_classic(self, ctx: EvaluationContext) -> AccommodationResult:
        """Extra time for classic quizzes — reads from submission.extra_time."""
        submission = ctx.submission
        if submission is None:
            return AccommodationResult(False, {})

        has = (submission.extra_time or 0) > 0
        return AccommodationResult(has, {"extra_time": submission.extra_time})

    def _evaluate_extra_attempts(self, ctx: EvaluationContext) -> AccommodationResult:
        """Extra attempts for either engine — reads from submission.extra_attempts."""
        submission = ctx.submission
        if submission is None:
            return AccommodationResult(False, {})

        has = (submission.extra_attempts or 0) > 0
        return AccommodationResult(has, {"extra_attempts": submission.extra_attempts})
