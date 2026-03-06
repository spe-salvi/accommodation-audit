from __future__ import annotations
from typing import Protocol, Optional
from enum import Enum
from audit.models.canvas import Participant


class AccommodationType(str, Enum):
    EXTRA_TIME = "extra_time"


class AccommodationRepo(Protocol):
    async def get_participant(self, *, course_id: int, quiz_id: int, user_id: int) -> Optional[Participant]:
        ...