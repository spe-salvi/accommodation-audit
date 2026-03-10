from dataclasses import dataclass
from audit.models.audit import AuditRow, AuditRequest
from audit.repos.base import AccommodationRepo, AccommodationType


@dataclass(frozen=True)
class AccommodationResult:
    has_accommodation: bool
    reason: str
    details: dict

class AccommodationService:
    def __init__(self, repo: AccommodationRepo):
        self.repo = repo

    def _evaluate_extra_time(self, participant) -> AccommodationResult:
        has = (
            (participant.timer_multiplier_enabled and (participant.timer_multiplier_value or 0) > 1)
            or
            (participant.extra_time_enabled and (participant.extra_time_in_seconds or 0) > 0)
        )
        return AccommodationResult(
            has,
            "Timer multiplier/extra time found." if has else "No extra time settings found.",
            {
                "timer_multiplier_value": participant.timer_multiplier_value,
                "extra_time_in_seconds": participant.extra_time_in_seconds,
            },
        )

    def _evaluate_extra_attempts(self, submission) -> AccommodationResult:
        has = (submission.extra_attempts or 0) > 0
        return AccommodationResult(
            has,
            "Extra attempts found." if has else "No extra attempts found.",
            {
                "extra_attempts": submission.extra_attempts,
            },
        )

    async def evaluate(
            self,
            *,
            course_id: int,
            quiz_id: int,
            user_id: int,
            engine: str, 
            accommodation_type: AccommodationType,
        ) -> AccommodationResult:

        if accommodation_type == AccommodationType.EXTRA_TIME:
            participant = await self.repo.get_participant(
                course_id=course_id,
                quiz_id=quiz_id,
                user_id=user_id,
                engine=engine,
            )
            if not participant:
                return AccommodationResult(False, "No participant record found.", {})
            return self._evaluate_extra_time(participant)

        if accommodation_type == AccommodationType.EXTRA_ATTEMPTS:
            submission = await self.repo.get_submission(
                course_id=course_id,
                quiz_id=quiz_id,
                user_id=user_id,
                engine=engine
            )
            if not submission:
                return AccommodationResult(False, "No submission record found.", {})
            return self._evaluate_extra_attempts(submission)

        return AccommodationResult(False, "Unsupported accommodation type.", {})

    async def audit(self, request: AuditRequest) -> list[AuditRow]:

        participants = await self.repo.list_participants(
            course_id=request.course_id,
            quiz_id=request.quiz_id,
            engine=request.engine,
        )
        submissions = await self.repo.list_submissions(
            course_id=request.course_id,
            quiz_id=request.quiz_id,
            engine=request.engine,
        )

        submission_by_session = {
            (s.participant_session_id, s.quiz_session_id): s
            for s in submissions
        }

        rows: list[AuditRow] = []


        for participant in participants:
            submission = submission_by_session.get(
                (participant.participant_session_id, participant.quiz_session_id)
            )

            result = await self.evaluate(
                course_id=request.course_id,
                quiz_id=request.quiz_id,
                user_id=participant.user_id,
                engine=request.engine,
                accommodation_type=request.accommodation_type,
            )

            completed = submission.date == "past" if submission else None

            rows.append(
                AuditRow(
                    course_id=request.course_id,
                    quiz_id=request.quiz_id,
                    user_id=participant.user_id,
                    engine=request.engine,
                    accommodation_type=request.accommodation_type,
                    has_accommodation=result.has_accommodation,
                    completed=completed,
                )
            )

        return rows