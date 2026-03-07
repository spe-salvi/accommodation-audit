from dataclasses import dataclass
from audit.models.audit import AuditRow
from audit.repos.base import AccommodationRepo, AccommodationType


@dataclass(frozen=True)
class AccommodationResult:
    has_accommodation: bool
    reason: str
    details: dict

class AccommodationService:
    def __init__(self, repo: AccommodationRepo):
        self.repo = repo

    async def evaluate(
        self,
        *,
        course_id: int,
        quiz_id: int,
        user_id: int,
        accommodation_type: AccommodationType,
    ) -> AccommodationResult:

        participant = await self.repo.get_participant(course_id=course_id, quiz_id=quiz_id, user_id=user_id)
        if not participant:
            return AccommodationResult(False, "No participant record found.", {})

        # Some accommodations are participant-level
        if accommodation_type == AccommodationType.EXTRA_TIME:
            # New Quizzes commonly uses a timer multiplier (ex: 1.5) :contentReference[oaicite:3]{index=3}
            has = (participant.timer_multiplier_enabled and participant.timer_multiplier_value > 1) \
                  or (participant.extra_time_enabled and participant.extra_time_in_seconds > 0)
            return AccommodationResult(
                has,
                "Timer multiplier/extra time found." if has else "No extra time settings found.",
                {"timer_multiplier_value": participant.timer_multiplier_value,
                 "extra_time_in_seconds": participant.extra_time_in_seconds},
            )

        return AccommodationResult(False, "Unsupported accommodation type.", {})


    async def audit_course_quiz(
        self,
        *,
        course_id: int,
        quiz_id: int,
        accommodation_type: AccommodationType,
    ) -> list[AuditRow]:

        participants = await self.repo.list_participants(
            course_id=course_id,
            quiz_id=quiz_id,
        )
        submissions = await self.repo.list_submissions(
            course_id=course_id,
            quiz_id=quiz_id,
        )

        submission_by_session = {
            (s.participant_session_id, s.quiz_session_id): s
            for s in submissions
        }

        rows: list[AuditRow] = []

        # print("participant keys:")
        # for p in participants[:5]:
        #     print((p.participant_session_id, p.quiz_session_id), p.user_id)

        # print("submission keys:")
        # for s in submissions[:5]:
        #     print((s.participant_session_id, s.quiz_session_id), s.user_id)

        for participant in participants:
            submission = submission_by_session.get(
                (participant.participant_session_id, participant.quiz_session_id)
            )

            result = await self.evaluate(
                course_id=course_id,
                quiz_id=quiz_id,
                user_id=participant.user_id,
                accommodation_type=accommodation_type,
            )

            completed = submission.date == "past" if submission else None

            rows.append(
                AuditRow(
                    course_id=course_id,
                    quiz_id=quiz_id,
                    user_id=participant.user_id,
                    accommodation_type=accommodation_type,
                    has_accommodation=result.has_accommodation,
                    completed=completed,
                )
            )

        return rows