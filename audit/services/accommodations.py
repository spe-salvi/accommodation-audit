from dataclasses import dataclass
from enum import Enum
from audit.repos.base import AccommodationRepo

class AccommodationType(str, Enum):
    EXTRA_TIME = "extra_time"

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