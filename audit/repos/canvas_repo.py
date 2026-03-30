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

API path conventions
--------------------
Classic quizzes: ``/api/v1/courses/{course_id}/quizzes/...``
New quizzes:     ``/api/quiz/v1/courses/{course_id}/quizzes/...``
  (submissions use ``/api/v1/.../assignments/{assignment_id}/submissions``)

Exceptions
----------
Domain exceptions from ``audit.exceptions`` propagate to callers.
``list_participants`` is the exception: a missing LTI ID or expired
session is treated as an empty result rather than a hard failure,
since participant data is optional accommodation context that should
not abort a full-term audit.
"""

from __future__ import annotations

import logging
from typing import Optional

from audit.clients.canvas_client import CanvasClient
from audit.clients.new_quiz_client import NewQuizClient
from audit.exceptions import LtiError
from audit.models.canvas import Course, NewQuizItem, Participant, Quiz, Submission
from audit.repos.base import AccommodationRepo

logger = logging.getLogger(__name__)


class CanvasRepo(AccommodationRepo):
    """
    Live Canvas API implementation of AccommodationRepo.

    Delegates HTTP concerns to ``CanvasClient`` and ``NewQuizClient``,
    and focuses on routing each query to the correct endpoint.

    Parameters
    ----------
    client:
        Authenticated Canvas REST API client.
    account_id:
        Root Canvas account ID used for account-scoped endpoints
        such as course listings.
    new_quiz_client:
        Optional LTI service client for participant data. When None,
        ``list_participants`` returns an empty list. Inject this to
        enable extra-time auditing for new quizzes.
    """

    def __init__(
        self,
        client: CanvasClient,
        *,
        account_id: int,
        new_quiz_client: NewQuizClient | None = None,
    ) -> None:
        self.client = client
        self._account_id = account_id
        self._new_quiz_client = new_quiz_client

    # ------------------------------------------------------------------
    # Participants
    # ------------------------------------------------------------------

    async def list_participants(
        self, *, course_id: int, quiz_id: int, engine: str
    ) -> list[Participant]:
        """
        Fetch all participants for a new-engine quiz.

        Requires a ``NewQuizClient`` injected at construction — returns
        an empty list if none is available or if engine is not 'new'.

        LTI errors (missing ID, expired session) are treated as empty
        results and logged as warnings rather than raised, since
        participant data is optional context that should not abort a
        full-term audit. All other errors propagate normally.
        """
        if engine != "new" or self._new_quiz_client is None:
            return []

        try:
            payload = await self._new_quiz_client.list_participants(
                canvas_assignment_id=quiz_id,
            )
        except LtiError as exc:
            logger.warning(
                "list_participants: LTI error for quiz_id=%d: %s",
                quiz_id,
                exc,
            )
            return []

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

    # ------------------------------------------------------------------
    # Submissions
    # ------------------------------------------------------------------

    async def list_submissions(
        self, *, course_id: int, quiz_id: int, engine: str
    ) -> list[Submission]:
        """
        Fetch all submissions for a quiz.

        Routes to the correct API path based on engine:
          - New:     /api/v1/courses/{id}/assignments/{id}/submissions
          - Classic: /api/v1/courses/{id}/quizzes/{id}/submissions
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

    # ------------------------------------------------------------------
    # Items
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Quizzes
    # ------------------------------------------------------------------

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
            course_id_by_quiz={},
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

    # ------------------------------------------------------------------
    # Courses
    # ------------------------------------------------------------------

    async def list_courses(self, *, term_id: int, engine: str) -> list[Course]:
        """
        Fetch all courses for a term under this account.

        Canvas ignores the enrollment_term_id query param on the user-
        scoped /courses endpoint, so we use the account-scoped endpoint
        and filter client-side via Course.list_from_api.
        """
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

        Returns None if the course does not exist or belongs to a
        different term.
        """
        payload = await self.client.get_json(f"/api/v1/courses/{course_id}")
        course = Course.from_api(payload)
        if course is None:
            return None
        if course.enrollment_term_id != term_id:
            return None
        return course
