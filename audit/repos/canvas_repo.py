"""
Canvas API repository implementation.

This is the production data access layer — it implements the
``AccommodationRepo`` protocol by making live HTTP calls to the
Canvas REST API via ``CanvasClient``.

Each method translates a domain-level query (e.g., "list submissions
for this quiz") into the correct Canvas API path, dispatches the
request, and parses the response into domain models. The method
signatures mirror the protocol exactly, so the service layer can
swap between ``CanvasRepo`` and ``JsonRepo`` without changes.

API path conventions:
  - Classic quizzes: ``/api/v1/courses/{id}/quizzes/...``
  - New quizzes:     ``/api/quiz/v1/courses/{id}/quizzes/...``
    (submissions use ``/api/v1/.../assignments/{id}/submissions``)
"""

from __future__ import annotations

from typing import Optional

from audit.clients.canvas_client import CanvasClient
from audit.models.canvas import Course, Quiz, Participant, Submission, NewQuizItem
from audit.repos.base import AccommodationRepo


class CanvasRepo(AccommodationRepo):
    """
    Live Canvas API implementation of AccommodationRepo.

    Delegates HTTP concerns to ``CanvasClient`` and focuses on
    mapping between Canvas API paths/payloads and domain models.
    """

    def __init__(self, client: CanvasClient, *, account_id: int) -> None:
        self.client = client
        self._account_id = account_id

    async def list_participants(
        self, *, course_id: int, quiz_id: int, engine: str
    ) -> list[Participant]:
        """
        Fetch all participants for a new-engine quiz.

        Classic quizzes have no participant concept — returns an empty
        list for non-new engines.
        """
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
        """Fetch a single participant by scanning the full list."""
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
        """
        Fetch all submissions for a quiz.

        Routes to the correct API path based on engine type:
          - New engine: ``/api/v1/courses/{id}/assignments/{id}/submissions``
          - Classic:    ``/api/v1/courses/{id}/quizzes/{id}/submissions``
        """
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
        """Fetch a single submission by scanning the full list."""
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
        """
        Fetch all items for a new-engine quiz.

        Classic quizzes do not expose per-item configuration via the
        API — returns an empty list for non-new engines.
        """
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
        """
        Fetch all quizzes in a course for the given engine.

        Routes to the correct API namespace based on engine type.
        """
        if engine == "new":
            path = f"/api/quiz/v1/courses/{course_id}/quizzes"
        else:
            path = f"/api/v1/courses/{course_id}/quizzes"

        payload = await self.client.get_paginated_json(path)
        return Quiz.list_from_api(
            engine=engine,
            payload=payload,
            course_id=course_id,
            course_id_by_quiz={},  # not needed when course_id is known
        )

    async def get_quiz(
        self, *, course_id: int, quiz_id: int, engine: str
    ) -> Optional[Quiz]:
        """Fetch a single quiz by scanning the full list."""
        quizzes = await self.list_quizzes(course_id=course_id, engine=engine)
        for quiz in quizzes:
            if quiz.quiz_id == quiz_id:
                return quiz
        return None

    async def list_courses(self, *, term_id: int, engine: str) -> list[Course]:
        """Fetch all courses for a term under this account."""
        payload = await self.client.get_paginated_json(
            f"/api/v1/accounts/{self._account_id}/courses",
            params={"enrollment_term_id": term_id},
        )
        return Course.list_from_api(payload, term_id=term_id)

    async def get_course(
        self, *, term_id: int, course_id: int, engine: str
    ) -> Optional[Course]:
        """
        Fetch a single course and validate it belongs to the given term.

        Returns None if the course doesn't exist or belongs to a
        different term.
        """
        payload = await self.client.get_json(f"/api/v1/courses/{course_id}")
        course = Course.from_api(payload)
        if course is None:
            return None
        if course.enrollment_term_id != term_id:
            return None
        return course
