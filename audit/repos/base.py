from __future__ import annotations
from typing import Protocol, Optional
from audit.models.canvas import Participant

class AccommodationRepo(Protocol):
    async def get_participant(self, *, course_id: int, quiz_id: int, user_id: int) -> Optional[Participant]:
        ...