"""
Core business logic for accommodation auditing.

This module contains ``AccommodationService`` — the central orchestrator
that evaluates whether students have accommodations applied to their
quizzes and produces structured audit results.

Bucket 1 enrichment
--------------------
``QuizAuditContext`` now carries the full ``Course`` and ``Quiz`` objects
alongside raw data. Row builders extract human-readable fields
(course_name, quiz_title, due_at, etc.) directly from these objects —
no additional API calls are needed.

Concurrency model
-----------------
``audit_term`` fans out across courses concurrently via ``asyncio.gather``,
gated by a shared ``asyncio.Semaphore``. Within each course, quizzes are
processed sequentially to keep Canvas API request volume bounded.

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
from audit.models.canvas import Course, Participant, Quiz, Submission, NewQuizItem
from audit.repos.base import AccommodationRepo, AccommodationType

logger = logging.getLogger(__name__)

_DEFAULT_CONCURRENCY = 10


@dataclass(frozen=True)
class AccommodationResult:
    """The outcome of evaluating a single accommodation for a single user."""
    has_accommodation: bool
    details: dict


@dataclass(frozen=True)
class EvaluationContext:
    """
    The minimal data slice needed to evaluate one accommodation for one user.
    """
    engine: str
    participant: Participant | None = None
    submission: Submission | None = None
    items: list[NewQuizItem] | None = None


@dataclass(frozen=True)
class QuizAuditContext:
    """
    Pre-loaded data for auditing all users/items on a single quiz.

    Carries the full Course and Quiz objects so row builders can populate
    human-readable fields (course_name, quiz_title, etc.) without
    additional API calls.
    """
    course: Course
    quiz: Quiz
    engine: str
    participants: list[Participant]
    submissions: list[Submission]
    items: list[NewQuizItem]
    submissions_by_user: dict[int, Submission]
    submissions_by_session: dict[tuple[str | None, str | None], Submission]

    @property
    def course_id(self) -> int:
        return self.course.course_id

    @property
    def quiz_id(self) -> int:
        return self.quiz.quiz_id


Evaluator = Callable[[EvaluationContext], AccommodationResult]


class AccommodationService:
    """
    Orchestrates accommodation evaluation and audit report generation.

    Parameters
    ----------
    repo:
        Data access layer. Works with both ``CanvasRepo`` and ``JsonRepo``.
    semaphore:
        Optional shared semaphore bounding how many courses are processed
        concurrently in ``audit_term``.
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
        return EvaluationContext(
            engine=engine, participant=participant,
            submission=submission, items=items,
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
        """Single-user path used by evaluate()."""
        participant = None
        submission = None
        items = None

        if accommodation_type == AccommodationType.EXTRA_TIME:
            if engine == "new":
                participant = await self.repo.get_participant(
                    course_id=course_id, quiz_id=quiz_id,
                    user_id=user_id, engine=engine,
                )
            elif engine == "classic":
                submission = await self.repo.get_submission(
                    course_id=course_id, quiz_id=quiz_id,
                    user_id=user_id, engine=engine,
                )
        elif accommodation_type == AccommodationType.EXTRA_ATTEMPT:
            submission = await self.repo.get_submission(
                course_id=course_id, quiz_id=quiz_id,
                user_id=user_id, engine=engine,
            )
        elif accommodation_type == AccommodationType.SPELL_CHECK:
            if engine == "new":
                items = await self.repo.list_items(
                    course_id=course_id, quiz_id=quiz_id, engine=engine,
                )

        return self._build_evaluation_context(
            engine=engine, participant=participant,
            submission=submission, items=items,
        )

    async def _load_quiz_audit_context(
        self,
        *,
        course: Course,
        quiz: Quiz,
        engine: str,
        accommodation_types: list[AccommodationType] | None = None,
    ) -> QuizAuditContext:
        """
        Eagerly load all data for a quiz into a single context object.

        Accepts the full Course and Quiz objects (already loaded by
        audit_course) so row builders have access to human-readable fields.
        """
        course_id = course.course_id
        quiz_id = quiz.quiz_id

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
                course_id=course_id, quiz_id=quiz_id, engine=engine,
            )

        submissions = await self.repo.list_submissions(
            course_id=course_id, quiz_id=quiz_id, engine=engine,
        )

        items: list[NewQuizItem] = []
        if engine == "new":
            items = await self.repo.list_items(
                course_id=course_id, quiz_id=quiz_id, engine=engine,
            )

        submissions_by_user = {s.user_id: s for s in submissions}
        submissions_by_session = {
            (s.participant_session_id, s.quiz_session_id): s
            for s in submissions
            if s.participant_session_id is not None or s.quiz_session_id is not None
        }

        return QuizAuditContext(
            course=course,
            quiz=quiz,
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
        ctx = await self._load_evaluation_context(
            course_id=course_id, quiz_id=quiz_id,
            user_id=user_id, engine=engine,
            accommodation_type=accommodation_type,
        )
        return self.evaluate_models(accommodation_type=accommodation_type, ctx=ctx)

    # ----------------------------
    # Audit APIs
    # ----------------------------

    async def audit_accommodation(self, request: AuditRequest) -> list[AuditRow]:
        """Audit a single accommodation type across all users on a quiz."""
        # For audit_accommodation we need Course and Quiz objects.
        # Use placeholder objects since this path is used for single-quiz spot checks.
        from audit.models.canvas import Course as C, Quiz as Q
        quiz = await self.repo.get_quiz(
            course_id=request.course_id,
            quiz_id=request.quiz_id,
            engine=request.engine,
        )
        # Fall back gracefully if quiz not found
        if quiz is None:
            return []

        courses = await self.repo.list_courses(term_id=0, engine=request.engine)
        course = next((c for c in courses if c.course_id == request.course_id), None)
        if course is None:
            # Build a minimal placeholder Course
            course = C(
                course_id=request.course_id,
                name="",
                course_code=None,
                sis_course_id=None,
                enrollment_term_id=None,
            )

        ctx = await self._load_quiz_audit_context(
            course=course,
            quiz=quiz,
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
        _course: Course | None = None,
        _quiz: Quiz | None = None,
    ) -> list[AuditRow]:
        """
        Audit all (or selected) accommodation types on a single quiz.

        Parameters
        ----------
        _course, _quiz:
            Pre-fetched objects passed down from audit_course to avoid
            redundant lookups. When None, they are fetched from the repo.
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
            requested = [a for a in requested if a != AccommodationType.SPELL_CHECK]

        # Resolve Course and Quiz if not passed in
        quiz = _quiz or await self.repo.get_quiz(
            course_id=course_id, quiz_id=quiz_id, engine=engine,
        )
        if quiz is None:
            return []

        course = _course
        if course is None:
            from audit.models.canvas import Course as C
            course = C(
                course_id=course_id, name="", course_code=None,
                sis_course_id=None, enrollment_term_id=None,
            )

        ctx = await self._load_quiz_audit_context(
            course=course,
            quiz=quiz,
            engine=engine,
            accommodation_types=requested,
        )

        rows: list[AuditRow] = []
        for accommodation_type in requested:
            rows.extend(
                self._audit_accommodation_with_quiz_context(
                    ctx=ctx, accommodation_type=accommodation_type,
                )
            )
        return rows

    async def audit_course(
        self,
        *,
        course_id: int,
        engine: str,
        accommodation_types: Iterable[AccommodationType] | None = None,
        _course: Course | None = None,
    ) -> list[AuditRow]:
        """
        Audit all quizzes in a course sequentially.

        Fetches the Course object once and passes it down to each quiz
        audit so row builders have access to course_name, course_code, etc.
        """
        quizzes = await self.repo.list_quizzes(course_id=course_id, engine=engine)
        types = list(accommodation_types) if accommodation_types is not None else None

        # Fetch Course object once for the whole course audit
        course = _course
        if course is None:
            # Try to get it from the repo; build a placeholder if unavailable
            # (JsonRepo doesn't support get_course without term_id)
            try:
                course = await self.repo.get_course(
                    term_id=0, course_id=course_id, engine=engine,
                )
            except Exception:
                course = None

        if course is None:
            from audit.models.canvas import Course as C
            course = C(
                course_id=course_id, name="", course_code=None,
                sis_course_id=None, enrollment_term_id=None,
            )

        rows: list[AuditRow] = []
        for quiz in quizzes:
            quiz_rows = await self.audit_quiz(
                course_id=course_id,
                quiz_id=quiz.quiz_id,
                engine=engine,
                accommodation_types=types,
                _course=course,
                _quiz=quiz,
            )
            rows.extend(quiz_rows)

        return rows

    async def _audit_course_with_semaphore(
        self,
        *,
        course_id: int,
        engine: str,
        accommodation_types: list[AccommodationType] | None,
        _course: Course | None = None,
    ) -> list[AuditRow]:
        async with self._semaphore:
            return await self.audit_course(
                course_id=course_id,
                engine=engine,
                accommodation_types=accommodation_types,
                _course=_course,
            )

    async def audit_term(
        self,
        *,
        term_id: int,
        engine: str,
        accommodation_types: Iterable[AccommodationType] | None = None,
        show_progress: bool = False,
    ) -> list[AuditRow]:
        """
        Audit all courses in a term concurrently.

        Passes the Course object from list_courses down to audit_course
        so each course's name/code is available without a second fetch.
        """
        courses = await self.repo.list_courses(term_id=term_id, engine=engine)
        types = list(accommodation_types) if accommodation_types is not None else None

        tasks = [
            self._audit_course_with_semaphore(
                course_id=course.course_id,
                engine=engine,
                accommodation_types=types,
                _course=course,
            )
            for course in courses
        ]

        if show_progress:
            from tqdm.asyncio import tqdm_asyncio
            results = await tqdm_asyncio.gather(
                *tasks,
                desc=f"Auditing courses ({engine})",
                unit="course",
                total=len(courses),
            )
        else:
            results = await asyncio.gather(*tasks)

        return [row for course_rows in results for row in course_rows]

    # ----------------------------
    # Row builders
    # ----------------------------

    def _build_spell_check_rows(self, *, ctx: QuizAuditContext) -> list[AuditRow]:
        """Build per-item audit rows for spell-check on essay questions."""
        if ctx.engine != "new":
            return []

        rows: list[AuditRow] = []
        for item in ctx.items:
            if item.interaction_type_slug != "essay":
                logger.debug(
                    "spell_check: skipping non-essay item item_id=%s quiz_id=%d",
                    item.item_id, ctx.quiz_id,
                )
                continue

            enabled = bool(item.essay_spell_check_enabled)
            rows.append(AuditRow(
                course_id=ctx.course_id,
                quiz_id=ctx.quiz_id,
                user_id=None,
                item_id=item.item_id,
                engine=ctx.engine,
                accommodation_type=AccommodationType.SPELL_CHECK,
                has_accommodation=enabled,
                details={"spell_check": enabled, "position": item.position},
                completed=None,
                # Bucket 1 enrichment
                course_name=ctx.course.name or None,
                course_code=ctx.course.course_code,
                sis_course_id=ctx.course.sis_course_id,
                quiz_title=ctx.quiz.title or None,
                quiz_due_at=ctx.quiz.due_at,
                quiz_lock_at=ctx.quiz.lock_at,
                enrollment_term_id=ctx.course.enrollment_term_id,
            ))

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

        # Shared context fields for every row in this quiz
        course_name = ctx.course.name or None
        course_code = ctx.course.course_code
        sis_course_id = ctx.course.sis_course_id
        quiz_title = ctx.quiz.title or None
        quiz_due_at = ctx.quiz.due_at
        quiz_lock_at = ctx.quiz.lock_at
        enrollment_term_id = ctx.course.enrollment_term_id

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
                    engine=ctx.engine, participant=participant, submission=submission,
                )
                result = self.evaluate_models(
                    accommodation_type=accommodation_type, ctx=eval_ctx,
                )
                if not result.has_accommodation:
                    logger.debug(
                        "no accommodation: user_id=%d quiz_id=%d type=%s engine=new",
                        participant.user_id, ctx.quiz_id, accommodation_type.value,
                    )
                completed = submission.date == "past" if submission else None
                attempts_left = submission.attempts_left if submission else None
                rows.append(AuditRow(
                    course_id=ctx.course_id,
                    quiz_id=ctx.quiz_id,
                    user_id=participant.user_id,
                    item_id=None,
                    engine=ctx.engine,
                    accommodation_type=accommodation_type,
                    has_accommodation=result.has_accommodation,
                    details=result.details,
                    completed=completed,
                    attempts_left=attempts_left,
                    course_name=course_name,
                    course_code=course_code,
                    sis_course_id=sis_course_id,
                    quiz_title=quiz_title,
                    quiz_due_at=quiz_due_at,
                    quiz_lock_at=quiz_lock_at,
                    enrollment_term_id=enrollment_term_id,
                ))
            return rows

        # --- All other cases: source is submissions ---
        for submission in ctx.submissions:
            eval_ctx = self._build_evaluation_context(
                engine=ctx.engine, participant=None, submission=submission,
            )
            result = self.evaluate_models(
                accommodation_type=accommodation_type, ctx=eval_ctx,
            )
            if not result.has_accommodation:
                logger.debug(
                    "no accommodation: user_id=%d quiz_id=%d type=%s engine=%s",
                    submission.user_id, ctx.quiz_id, accommodation_type.value, ctx.engine,
                )
            rows.append(AuditRow(
                course_id=ctx.course_id,
                quiz_id=ctx.quiz_id,
                user_id=submission.user_id,
                item_id=None,
                engine=ctx.engine,
                accommodation_type=accommodation_type,
                has_accommodation=result.has_accommodation,
                details=result.details,
                completed=submission.date == "past",
                attempts_left=submission.attempts_left,
                course_name=course_name,
                course_code=course_code,
                sis_course_id=sis_course_id,
                quiz_title=quiz_title,
                quiz_due_at=quiz_due_at,
                quiz_lock_at=quiz_lock_at,
                enrollment_term_id=enrollment_term_id,
            ))

        return rows

    def _audit_accommodation_with_quiz_context(
        self,
        *,
        ctx: QuizAuditContext,
        accommodation_type: AccommodationType,
    ) -> list[AuditRow]:
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
        participant = ctx.participant
        if participant is None:
            return AccommodationResult(False, {})
        has = (
            (participant.timer_multiplier_enabled and (participant.timer_multiplier_value or 0) > 1)
            or (participant.extra_time_enabled and (participant.extra_time_in_seconds or 0) > 0)
        )
        return AccommodationResult(has, {
            "timer_multiplier_value": participant.timer_multiplier_value,
            "extra_time_in_seconds": participant.extra_time_in_seconds,
        })

    def _evaluate_extra_time_classic(self, ctx: EvaluationContext) -> AccommodationResult:
        submission = ctx.submission
        if submission is None:
            return AccommodationResult(False, {})
        has = (submission.extra_time or 0) > 0
        return AccommodationResult(has, {"extra_time": submission.extra_time})

    def _evaluate_extra_attempts(self, ctx: EvaluationContext) -> AccommodationResult:
        submission = ctx.submission
        if submission is None:
            return AccommodationResult(False, {})
        has = (submission.extra_attempts or 0) > 0
        return AccommodationResult(has, {"extra_attempts": submission.extra_attempts})
