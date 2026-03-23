from __future__ import annotations

from typing import Optional

from audit.clients.canvas_client import CanvasClient
from audit.models.canvas import Course, Quiz, Participant, Submission, NewQuizItem
from audit.repos.base import AccommodationRepo


class CanvasRepo(AccommodationRepo):
    def __init__(self, client: CanvasClient) -> None:
        self.client = client

    async def list_participants(
        self, *, course_id: int, quiz_id: int, engine: str
    ) -> list[Participant]:
        if engine != "new":
            return []

        payload = await self.client.get_paginated_json(
            f"/api/quiz/v1/courses/{course_id}/quizzes/{quiz_id}/participants"
        )
        return Participant.list_from_api(
            course_id=course_id,
            quiz_id=quiz_id,
            engine=engine,
            payload=payload,
        )

    async def get_participant(
        self, *, course_id: int, quiz_id: int, user_id: int, engine: str
    ) -> Optional[Participant]:
        participants = await self.list_participants(
            course_id=course_id, quiz_id=quiz_id, engine=engine
        )
        for participant in participants:
            if participant.user_id == user_id:
                return participant
        return None

    async def list_submissions(
        self, *, course_id: int, quiz_id: int, engine: str
    ) -> list[Submission]:
        if engine == "new":
            path = f"/api/v1/courses/{course_id}/assignments/{quiz_id}/submissions"
        else:
            path = f"/api/v1/courses/{course_id}/quizzes/{quiz_id}/submissions"

        payload = await self.client.get_paginated_json(path)
        return Submission.list_from_api(
            course_id=course_id,
            quiz_id=quiz_id,
            engine=engine,
            payload=payload,
        )

    async def get_submission(
        self, *, course_id: int, quiz_id: int, user_id: int, engine: str
    ) -> Optional[Submission]:
        submissions = await self.list_submissions(
            course_id=course_id, quiz_id=quiz_id, engine=engine
        )
        for submission in submissions:
            if submission.user_id == user_id:
                return submission
        return None

    async def list_items(
        self, *, course_id: int, quiz_id: int, engine: str
    ) -> list[NewQuizItem]:
        if engine != "new":
            return []

        payload = await self.client.get_paginated_json(
            f"/api/quiz/v1/courses/{course_id}/quizzes/{quiz_id}/items"
        )
        return NewQuizItem.list_from_api(
            course_id=course_id,
            quiz_id=quiz_id,
            engine=engine,
            payload=payload,
        )

    async def list_quizzes(self, *, course_id: int, engine: str) -> list[Quiz]:
        if engine == "new":
            path = f"/api/quiz/v1/courses/{course_id}/quizzes"
        else:
            path = f"/api/v1/courses/{course_id}/quizzes"

        payload = await self.client.get_paginated_json(path)
        return Quiz.list_from_api(
            engine=engine,
            payload=payload,
            course_id=course_id,
        )

    async def get_quiz(
        self, *, course_id: int, quiz_id: int, engine: str
    ) -> Optional[Quiz]:
        quizzes = await self.list_quizzes(course_id=course_id, engine=engine)
        for quiz in quizzes:
            if quiz.quiz_id == quiz_id:
                return quiz
        return None

    async def list_courses(self, *, term_id: int, engine: str) -> list[Course]:
        payload = await self.client.get_paginated_json(
            "/api/v1/courses",
            params={"enrollment_term_id": term_id},
        )
        return Course.list_from_api(payload)

    async def get_course(
        self, *, term_id: int, course_id: int, engine: str
    ) -> Optional[Course]:
        payload = await self.client.get_json(f"/api/v1/courses/{course_id}")
        course = Course.from_api(payload)
        if course is None:
            return None
        if course.enrollment_term_id != term_id:
            return None
        return course
