from __future__ import annotations
from enum import Enum
from typing import Protocol, Optional
from audit.models.canvas import Course, Quiz, Participant, Submission, NewQuizItem


class AccommodationType(str, Enum):
    EXTRA_TIME = "extra_time"
    EXTRA_ATTEMPT = "extra_attempt"
    SPELL_CHECK = "spell_check"


class AccommodationRepo(Protocol):
    async def list_participants(
        self, *, course_id: int, quiz_id: int, engine: str
    ) -> list[Participant]:
        ...

    async def get_participant(
        self, *, course_id: int, quiz_id: int, user_id: int, engine: str
    ) -> Optional[Participant]:
        ...

    async def list_submissions(
        self, *, course_id: int, quiz_id: int, engine: str
    ) -> list[Submission]:
        ...

    async def get_submission(
        self, *, course_id: int, quiz_id: int, user_id: int, engine: str
    ) -> Optional[Submission]:
        ...

    async def list_items(
        self, *, course_id: int, quiz_id: int, engine: str
    ) -> list[NewQuizItem]:
        ...

    async def list_quizzes(
        self, *, course_id: int, engine: str
    ) -> list[Quiz]:
        ...
    
    async def get_quiz(
        self, *, course_id: int, quiz_id: int, engine: str
    ) -> Optional[Quiz]:
        ...

    async def list_courses(
        self, *, term_id: int, engine: str
    ) -> list[Course]:
        ...
    
    async def get_course(
        self, *, term_id: int, course_id: int, engine: str
    ) -> Optional[Course]:
        ...