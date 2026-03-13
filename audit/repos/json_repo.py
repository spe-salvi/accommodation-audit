import json
from pathlib import Path
from typing import Optional
from audit.models.canvas import Quiz, Participant, Submission, NewQuizItem

class JsonRepo:
    def __init__(
        self,
        *,
        participant_path: str | None = None,
        submission_path: str | None = None,
        items_path: str | None = None,
        quizzes_path: str | None = None,
    ):
        self.participant_path = Path(participant_path) if participant_path else None
        self.submission_path = Path(submission_path) if submission_path else None
        self.items_path = Path(items_path) if items_path else None
        self.quizzes_path = Path(quizzes_path) if quizzes_path else None

    async def list_participants(self, *, course_id: int, quiz_id: int, engine: str) -> list[Participant]:
        data = json.loads(self.participant_path.read_text(encoding="utf-8")) if self.participant_path else []
        return Participant.list_from_api(course_id=course_id, quiz_id=quiz_id, engine=engine, payload=data)

    async def get_participant(self, *, course_id: int, quiz_id: int, user_id: int, engine: str) -> Optional[Participant]:
        participants = await self.list_participants(course_id=course_id, quiz_id=quiz_id, engine=engine)
        for p in participants:
            if p.user_id == user_id:
                return p
        return None
    
    async def list_submissions(self, *, course_id: int, quiz_id: int, engine: str) -> list[Submission]:
        data = json.loads(self.submission_path.read_text(encoding="utf-8")) if self.submission_path else []
        return Submission.list_from_api(course_id=course_id, quiz_id=quiz_id, engine=engine, payload=data)
    
    async def get_submission(self, *, course_id: int, quiz_id: int, engine: str, user_id: int) -> Optional[Submission]:
        submissions = await self.list_submissions(course_id=course_id, quiz_id=quiz_id, engine=engine)
        for s in submissions:
            if s.course_id == course_id and s.user_id == user_id and s.quiz_id == quiz_id:
                return s
        return None
    
    async def list_items(self, *, course_id: int, quiz_id: int, engine: str) -> list[NewQuizItem]:
        data = json.loads(self.items_path.read_text(encoding="utf-8")) if self.items_path else []
        return NewQuizItem.list_from_api(
            course_id=course_id,
            quiz_id=quiz_id,
            engine=engine,
            payload=data,
        )
    
    async def list_quizzes(self, *, course_id: int, engine: str) -> list[Quiz]:
        data = json.loads(self.quizzes_path.read_text(encoding="utf-8")) if self.quizzes_path else []
        return Quiz.list_from_api(
            course_id=course_id,
            engine=engine,
            payload=data
        )

    async def get_quiz(self, *, course_id: int, quiz_id: int, engine: str) -> Optional[Quiz]:
        quizzes = await self.list_quizzes(course_id=course_id, engine=engine)
        for q in quizzes:
            if q.course_id == course_id and q.quiz_id == quiz_id:
                return q
        return None