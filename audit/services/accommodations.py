from dataclasses import dataclass
from typing import Callable, Iterable

from audit.models.audit import AuditRow, AuditRequest
from audit.models.canvas import Participant, Submission, NewQuizItem
from audit.repos.base import AccommodationRepo, AccommodationType


@dataclass(frozen=True)
class AccommodationResult:
    has_accommodation: bool
    details: dict


@dataclass(frozen=True)
class EvaluationContext:
    engine: str
    participant: Participant | None = None
    submission: Submission | None = None
    items: list[NewQuizItem] | None = None


@dataclass(frozen=True)
class QuizAuditContext:
    course_id: int
    quiz_id: int
    engine: str
    participants: list[Participant]
    submissions: list[Submission]
    items: list[NewQuizItem]
    submissions_by_user: dict[int, Submission]
    submissions_by_session: dict[tuple[str | None, str | None], Submission]


Evaluator = Callable[[EvaluationContext], AccommodationResult]


class AccommodationService:
    def __init__(self, repo: AccommodationRepo):
        self.repo = repo
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
    ) -> QuizAuditContext:
        participants: list[Participant] = []
        if engine == "new":
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
        ctx = await self._load_quiz_audit_context(
            course_id=request.course_id,
            quiz_id=request.quiz_id,
            engine=request.engine,
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

    def _build_spell_check_rows(self, *, ctx: QuizAuditContext) -> list[AuditRow]:
        if ctx.engine != "new":
            return []

        rows: list[AuditRow] = []

        for item in ctx.items:
            if item.interaction_type_slug != "essay":
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
        rows: list[AuditRow] = []

        if ctx.engine == "new":
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
        if accommodation_type == AccommodationType.SPELL_CHECK:
            return self._build_spell_check_rows(ctx=ctx)

        return self._build_user_rows(
            ctx=ctx,
            accommodation_type=accommodation_type,
        )
    

    async def audit_course(
        self,
        *,
        course_id: int,
        engine: str,
        accommodation_types: Iterable[AccommodationType] | None = None,
    ) -> list[AuditRow]:
        quizzes = await self.repo.list_quizzes(
            course_id=course_id,
            engine=engine,
        )

        rows: list[AuditRow] = []

        for quiz in quizzes:
            quiz_rows = await self.audit_quiz(
                course_id=course_id,
                quiz_id=quiz.quiz_id,
                engine=engine,
                accommodation_types=accommodation_types,
            )
            rows.extend(quiz_rows)

        return rows


    async def audit_term(
        self,
        *,
        term_id: int,
        engine: str,
        accommodation_types: Iterable[AccommodationType] | None = None,
    ) -> list[AuditRow]:
        courses = await self.repo.list_courses(
            term_id=term_id,
            engine=engine,
        )

        rows: list[AuditRow] = []

        for course in courses:
            course_rows = await self.audit_course(
                course_id=course.course_id,
                engine=engine,
                accommodation_types=accommodation_types,
            )
            rows.extend(course_rows)

        return rows


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
        return AccommodationResult(
            has,
            {
                "timer_multiplier_value": participant.timer_multiplier_value,
                "extra_time_in_seconds": participant.extra_time_in_seconds,
            },
        )

    def _evaluate_extra_time_classic(self, ctx: EvaluationContext) -> AccommodationResult:
        submission = ctx.submission
        if submission is None:
            return AccommodationResult(False, {})

        has = (submission.extra_time or 0) > 0
        return AccommodationResult(
            has,
            {
                "extra_time": submission.extra_time,
            },
        )

    def _evaluate_extra_attempts(self, ctx: EvaluationContext) -> AccommodationResult:
        submission = ctx.submission
        if submission is None:
            return AccommodationResult(False, {})

        has = (submission.extra_attempts or 0) > 0
        return AccommodationResult(
            has,
            {
                "extra_attempts": submission.extra_attempts,
            },
        )