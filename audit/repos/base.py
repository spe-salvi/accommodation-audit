from __future__ import annotations
from enum import Enum
from typing import Protocol, Optional
from audit.models.canvas import Participant, Submission, NewQuizItem


class AccommodationType(str, Enum):
    EXTRA_TIME = "extra_time"
    EXTRA_ATTEMPT = "extra_attempt"
    SPELL_CHECK = "spell_check"


class AccommodationRepo(Protocol):
    async def get_participant(
        self, *, course_id: int, quiz_id: int, user_id: int, engine: str
    ) -> Optional[Participant]:
        ...

    async def get_submission(
        self, *, course_id: int, quiz_id: int, user_id: int, engine: str
    ) -> Optional[Submission]:
        ...

    async def list_items(
        self, *, course_id: int, quiz_id: int, engine: str
    ) -> list[NewQuizItem]:
        ...