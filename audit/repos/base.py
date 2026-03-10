from __future__ import annotations
from enum import Enum
from typing import Protocol, Optional
from audit.models.canvas import Participant, Submission


class AccommodationType(str, Enum):
    EXTRA_TIME = "extra_time"
    EXTRA_ATTEMPTS = "extra_attempt"


class AccommodationRepo(Protocol):
    async def get_participant(
        self, *, course_id: int, quiz_id: int, user_id: int, engine: str
    ) -> Optional[Participant]:
        ...

    async def get_submission(
        self, *, course_id: int, quiz_id: int, user_id: int, engine: str
    ) -> Optional[Submission]:
        ...