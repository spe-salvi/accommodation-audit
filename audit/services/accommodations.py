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

Bucket 1 enrichment
--------------------
``QuizAuditContext`` carries the full ``Course`` and ``Quiz`` objects
alongside raw data. Row builders extract human-readable fields
(course_name, quiz_title, etc.) directly — no additional API calls.

User-scoped auditing
--------------------
``audit_user`` accepts a ``user_id`` plus optional scope narrowing
(term_id, course_id, quiz_id). It uses the enrollments endpoint to
discover only the courses a student is actually enrolled in, then
audits those courses and filters results to the requested user.

Concurrency model
-----------------
``audit_term`` and ``audit_user`` fan out across courses concurrently
via ``asyncio.gather``, gated by a shared ``asyncio.Semaphore``.
Within each course, quizzes are processed sequentially.

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
    """The minimal data slice needed to evaluate one accommodation for one user."""
    engine: str
    participant: Participant | None = None
    submission: Submission | None = None
    items: list[NewQuizItem] | None = None


@dataclass(frozen=True)
class QuizAuditContext:
    """
    Pre-loaded data for auditing all users/items on a single quiz.

    Carries the full Course and Quiz objects so row builders can
    populate human-readable fields without additional API calls.
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
        concurrently in ``audit_term`` and ``audit_user``.
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
        quiz = await self.repo.get_quiz(
            course_id=request.course_id,
            quiz_id=request.quiz_id,
            engine=request.engine,
        )
        if quiz is None:
            return []

        course = _placeholder_course(request.course_id)
        ctx = await self._load_quiz_audit_context(
            course=course, quiz=quiz,
            engine=request.engine,
            accommodation_types=[request.accommodation_type],
        )
        return self._audit_accommodation_with_quiz_context(
            ctx=ctx, accommodation_type=request.accommodation_type,
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
            redundant lookups.
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

        quiz = _quiz or await self.repo.get_quiz(
            course_id=course_id, quiz_id=quiz_id, engine=engine,
        )
        if quiz is None:
            return []

        course = _course or _placeholder_course(course_id)

        ctx = await self._load_quiz_audit_context(
            course=course, quiz=quiz, engine=engine,
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

        Fetches the Course object once and passes it to each quiz audit.
        """
        quizzes = await self.repo.list_quizzes(course_id=course_id, engine=engine)
        types = list(accommodation_types) if accommodation_types is not None else None

        course = _course
        if course is None:
            try:
                course = await self.repo.get_course(
                    term_id=0, course_id=course_id, engine=engine,
                )
            except Exception:
                course = None
        if course is None:
            course = _placeholder_course(course_id)

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
        """Acquire the semaphore then run audit_course."""
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

        Passes the Course object from list_courses down to each course
        audit so course metadata is available without re-fetching.
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

    async def audit_user(
        self,
        *,
        user_id: int,
        engine: str,
        accommodation_types: Iterable[AccommodationType] | None = None,
        term_id: int | None = None,
        course_id: int | None = None,
        quiz_id: int | None = None,
        show_progress: bool = False,
    ) -> list[AuditRow]:
        """
        Audit accommodations for a single user.

        Uses the enrollments endpoint to discover which courses the user
        is enrolled in, then audits only those courses — avoiding a
        full-term scan. Results are filtered to rows belonging to the
        requested user (spell-check rows, which are item-level and have
        no user_id, are excluded from user-scoped results).

        Scope combinations
        ------------------
        - user + quiz:   audit one quiz, return that user's rows
        - user + course: audit one course, return that user's rows
        - user + term:   use enrollments filtered by term, audit those courses
        - user alone:    use all active enrollments, audit those courses

        Parameters
        ----------
        user_id:
            Canvas user ID to audit.
        engine:
            Quiz engine ("new", "classic", or called once per engine from CLI).
        accommodation_types:
            Accommodation types to evaluate. Defaults to all types.
        term_id:
            Optional term scope. Filters enrollments server-side when provided.
        course_id:
            Optional course scope. Skips enrollment lookup entirely.
        quiz_id:
            Optional quiz scope. Requires course_id to be set.
        show_progress:
            If True, show tqdm bar over course audits (only when multiple
            courses are being audited).
        """
        types = list(accommodation_types) if accommodation_types is not None else None

        # --- quiz scope: direct, no enrollment lookup needed ---
        if quiz_id is not None and course_id is not None:
            _course = None
            if hasattr(self.repo, "get_course_by_id"):
                _course = await self.repo.get_course_by_id(course_id)
            rows = await self.audit_quiz(
                course_id=course_id,
                quiz_id=quiz_id,
                engine=engine,
                accommodation_types=types,
                _course=_course,
            )
            return _filter_user(rows, user_id)

        # --- course scope: direct, no enrollment lookup needed ---
        if course_id is not None:
            _course = None
            if hasattr(self.repo, "get_course_by_id"):
                _course = await self.repo.get_course_by_id(course_id)
            rows = await self.audit_course(
                course_id=course_id,
                engine=engine,
                accommodation_types=types,
                _course=_course,
            )
            return _filter_user(rows, user_id)

        # --- term or global scope: use enrollments to find courses ---
        # CanvasRepo.list_enrollments is not on the protocol so we access
        # it directly. This is a live-Canvas-only feature.
        if not hasattr(self.repo, "list_enrollments"):
            raise NotImplementedError(
                "audit_user with term/global scope requires a CanvasRepo with "
                "list_enrollments support. JsonRepo does not support this."
            )

        enrollments = await self.repo.list_enrollments(
            user_id, term_id=term_id,
        )

        if not enrollments:
            logger.info(
                "audit_user: no active enrollments found for user_id=%d term_id=%s",
                user_id, term_id,
            )
            return []

        logger.info(
            "audit_user: user_id=%d has %d active enrollment(s) — auditing",
            user_id, len(enrollments),
        )

        # Fetch Course objects concurrently — usually warm in persistent cache.
        if hasattr(self.repo, "get_course_by_id"):
            course_results = await asyncio.gather(
                *[self.repo.get_course_by_id(e.course_id) for e in enrollments],
                return_exceptions=True,
            )
            course_by_id: dict[int, Course] = {
                e.course_id: c
                for e, c in zip(enrollments, course_results)
                if isinstance(c, Course)
            }
        else:
            course_by_id = {}

        tasks = [
            self._audit_course_with_semaphore(
                course_id=enrollment.course_id,
                engine=engine,
                accommodation_types=types,
                _course=course_by_id.get(enrollment.course_id),
            )
            for enrollment in enrollments
        ]


        if show_progress and len(tasks) > 1:
            from tqdm.asyncio import tqdm_asyncio
            results = await tqdm_asyncio.gather(
                *tasks,
                desc=f"Auditing courses for user {user_id} ({engine})",
                unit="course",
                total=len(enrollments),
            )
        else:
            results = await asyncio.gather(*tasks)

        all_rows = [row for course_rows in results for row in course_rows]
        return _filter_user(all_rows, user_id)

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
                enrollment_term_id=ctx.course.enrollment_term_id,
                course_name=ctx.course.name or None,
                course_code=ctx.course.course_code,
                sis_course_id=ctx.course.sis_course_id,
                quiz_title=ctx.quiz.title or None,
                quiz_due_at=ctx.quiz.due_at,
                quiz_lock_at=ctx.quiz.lock_at,
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
        enrollment_term_id = ctx.course.enrollment_term_id
        course_name = ctx.course.name or None
        course_code = ctx.course.course_code
        sis_course_id = ctx.course.sis_course_id
        quiz_title = ctx.quiz.title or None
        quiz_due_at = ctx.quiz.due_at
        quiz_lock_at = ctx.quiz.lock_at

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
                    enrollment_term_id=enrollment_term_id,
                    course_name=course_name,
                    course_code=course_code,
                    sis_course_id=sis_course_id,
                    quiz_title=quiz_title,
                    quiz_due_at=quiz_due_at,
                    quiz_lock_at=quiz_lock_at,
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
                enrollment_term_id=enrollment_term_id,
                course_name=course_name,
                course_code=course_code,
                sis_course_id=sis_course_id,
                quiz_title=quiz_title,
                quiz_due_at=quiz_due_at,
                quiz_lock_at=quiz_lock_at,
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
            (participant.timer_multiplier_enabled
             and (participant.timer_multiplier_value or 0) > 1)
            or (participant.extra_time_enabled
                and (participant.extra_time_in_seconds or 0) > 0)
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


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _placeholder_course(course_id: int) -> Course:
    """
    Build a minimal Course object when the full course data is unavailable.

    Used when audit_quiz or audit_accommodation is called without a
    pre-fetched Course (e.g. in spot-check scenarios or from JsonRepo).
    Bucket 1 fields will be empty in the resulting rows.
    """
    return Course(
        course_id=course_id,
        name="",
        course_code=None,
        sis_course_id=None,
        enrollment_term_id=None,
    )


def _filter_user(rows: list[AuditRow], user_id: int) -> list[AuditRow]:
    """
    Return only rows belonging to *user_id*.

    Spell-check rows have ``user_id=None`` (they are item-level, not
    user-level) and are excluded from user-scoped results — this is
    correct behaviour since spell-check is a quiz configuration setting,
    not a per-student accommodation.
    """
    return [r for r in rows if r.user_id == user_id]
