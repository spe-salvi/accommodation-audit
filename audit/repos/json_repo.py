import json
from pathlib import Path
from typing import Optional
from audit.models.canvas import Participant, Submission

class JsonRepo:
    def __init__(
        self,
        *,
        participant_path: str,
        submission_path: Optional[str] = None,
        # items_path: Optional[str] = None,
    ):
        self.participant_path = Path(participant_path)
        self.submission_path = Path(submission_path) if submission_path else None
        # self.items_path = Path(items_path) if items_path else None

    async def list_participants(self, *, course_id: int, quiz_id: int) -> list[Participant]:
        data = json.loads(self.participant_path.read_text(encoding="utf-8"))
        return Participant.list_from_api(course_id=course_id, quiz_id=quiz_id, payload=data)

    async def get_participant(self, *, course_id: int, quiz_id: int, user_id: int) -> Optional[Participant]:
        participants = await self.list_participants(course_id=course_id, quiz_id=quiz_id)
        for p in participants:
            if p.user_id == user_id:
                return p
        return None
    
    async def list_submissions(self, *, course_id: int, quiz_id: int) -> list[Submission]:
        if not self.submission_path:
            return []

        data = json.loads(self.submission_path.read_text(encoding="utf-8"))
        submissions = []
        for item in data:
            s = Submission.from_api(course_id=course_id, quiz_id=quiz_id, data=item)
            if s is not None:
                submissions.append(s)
        return submissions