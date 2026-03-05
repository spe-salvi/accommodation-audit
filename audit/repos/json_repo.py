import json
from pathlib import Path
from typing import Optional
from audit.models.canvas import Participant

class JsonRepo:
    def __init__(self, *, participant_path: str, items_path: Optional[str] = None):
        self.participant_path = Path(participant_path)
        self.items_path = Path(items_path) if items_path else None

    async def get_participant(self, *, course_id: int, quiz_id: int, user_id: int) -> Optional[Participant]:
        data = json.loads(self.participant_path.read_text(encoding="utf-8"))
        p = Participant.from_api(course_id=course_id, quiz_id=quiz_id, data=data)
        if not p or p.user_id != user_id:
            return None
        return p