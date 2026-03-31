"""
Canvas API repository implementation.

This is the production data access layer — it implements the
``AccommodationRepo`` protocol by making live HTTP calls to the
Canvas REST API via ``CanvasClient``.

Persistent caching
------------------
An optional ``PersistentCache`` can be injected at construction. When
present, the following entity types are read from cache before hitting
Canvas, and written back on a miss:

  - Terms   (TTL: 30 days)
  - Courses (TTL: 30 days)
  - Quizzes (TTL: 1 day, keyed by course_id:engine)
  - Users   (TTL: 7 days)

Submissions, participants, and items are intentionally NOT cached —
they change frequently during a term and must always reflect the
current state of Canvas.

API path conventions
--------------------
Classic quizzes: ``/api/v1/courses/{course_id}/quizzes/...``
New quizzes:     ``/api/quiz/v1/courses/{course_id}/quizzes/...``
  (submissions use ``/api/v1/.../assignments/{assignment_id}/submissions``)

Exceptions
----------
Domain exceptions from ``audit.exceptions`` propagate to callers.
``list_participants`` is the exception: a missing LTI ID or expired
session is treated as an empty result rather than a hard failure.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Optional

from audit.cache.persistent import CacheEntity, PersistentCache
from audit.clients.canvas_client import CanvasClient
from audit.clients.new_quiz_client import NewQuizClient
from audit.exceptions import LtiError
from audit.models.canvas import Course, NewQuizItem, Participant, Quiz, Submission
from audit.repos.base import AccommodationRepo

logger = logging.getLogger(__name__)


class CanvasRepo(AccommodationRepo):
    """
    Live Canvas API implementation of AccommodationRepo.

    Parameters
    ----------
    client:
        Authenticated Canvas REST API client.
    account_id:
        Root Canvas account ID used for account-scoped endpoints.
    new_quiz_client:
        Optional LTI service client for participant data.
    persistent_cache:
        Optional persistent TTL cache for terms, courses, quizzes, and
        users. When None, every call goes directly to the Canvas API.
    """

    def __init__(
        self,
        client: CanvasClient,
        *,
        account_id: int,
        new_quiz_client: NewQuizClient | None = None,
        persistent_cache: PersistentCache | None = None,
    ) -> None:
        self.client = client
        self._account_id = account_id
        self._new_quiz_client = new_quiz_client
        self._cache = persistent_cache

    # ------------------------------------------------------------------
    # Participants  (not cached — changes frequently)
    # ------------------------------------------------------------------

    async def list_participants(
        self, *, course_id: int, quiz_id: int, engine: str
    ) -> list[Participant]:
        """
        Fetch all participants for a new-engine quiz.

        Not cached — participant data (extra time, multipliers) changes
        when accommodations are updated and must always be current.
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
    # Submissions  (not cached — changes frequently)
    # ------------------------------------------------------------------

    async def list_submissions(
        self, *, course_id: int, quiz_id: int, engine: str
    ) -> list[Submission]:
        """
        Fetch all submissions for a quiz.

        Not cached — submission data changes as students complete quizzes
        and instructors grade them.
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
    # Items  (not cached — quiz content may change)
    # ------------------------------------------------------------------

    async def list_items(
        self, *, course_id: int, quiz_id: int, engine: str
    ) -> list[NewQuizItem]:
        """
        Fetch all items for a new-engine quiz.

        Not cached — spell-check settings on individual questions may be
        updated by instructors between audit runs.
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
    # Quizzes  (cached by course_id:engine, TTL 1 day)
    # ------------------------------------------------------------------

    async def list_quizzes(self, *, course_id: int, engine: str) -> list[Quiz]:
        """
        Fetch all quizzes in a course for the given engine.

        Cached by ``(course_id, engine)`` with a 1-day TTL. A full term
        audit fetches quizzes for every course — caching this cuts
        repeated runs from minutes to seconds.
        """
        cache_key = f"{course_id}:{engine}"

        if self._cache is not None:
            cached = self._cache.get_list(CacheEntity.QUIZ, cache_key)
            if cached is not None:
                return Quiz.list_from_api(
                    engine=engine,
                    payload=cached,
                    course_id=course_id,
                    course_id_by_quiz={},
                )

        if engine == "new":
            path = f"/api/quiz/v1/courses/{course_id}/quizzes"
        else:
            path = f"/api/v1/courses/{course_id}/quizzes"

        payload = await self.client.get_paginated_json(path)
        quizzes = Quiz.list_from_api(
            engine=engine,
            payload=payload,
            course_id=course_id,
            course_id_by_quiz={},
        )

        if self._cache is not None:
            self._cache.set(CacheEntity.QUIZ, cache_key, payload)

        return quizzes

    async def get_quiz(
        self, *, course_id: int, quiz_id: int, engine: str
    ) -> Optional[Quiz]:
        """Fetch a single quiz by scanning the cached/fetched list."""
        quizzes = await self.list_quizzes(course_id=course_id, engine=engine)
        for quiz in quizzes:
            if quiz.quiz_id == quiz_id:
                return quiz
        return None

    # ------------------------------------------------------------------
    # Courses  (cached by term_id, TTL 30 days)
    # ------------------------------------------------------------------

    async def list_courses(self, *, term_id: int, engine: str) -> list[Course]:
        """
        Fetch all courses for a term under this account.

        Cached by ``term_id`` with a 30-day TTL. Course enrolments and
        availability are stable within a term — this is the highest-
        value cache entry since it's fetched once per term per engine.

        Note: ``engine`` is not part of the cache key because the course
        list is the same regardless of engine. The engine parameter is
        kept in the protocol signature for protocol conformance.
        """
        if self._cache is not None:
            cached = self._cache.get_list(CacheEntity.COURSE, term_id)
            if cached is not None:
                return Course.list_from_api(cached, term_id=term_id)

        payload = await self.client.get_paginated_json(
            f"/api/v1/accounts/{self._account_id}/courses",
            params={"enrollment_term_id": term_id},
        )
        courses = Course.list_from_api(payload, term_id=term_id)

        if self._cache is not None:
            self._cache.set(CacheEntity.COURSE, term_id, payload)

        return courses

    async def get_course(
        self, *, term_id: int, course_id: int, engine: str
    ) -> Optional[Course]:
        """
        Fetch a single course, checking the cache before hitting Canvas.

        Falls back to a direct API call if the course isn't in the cache.
        """
        if self._cache is not None:
            cached = self._cache.get(CacheEntity.COURSE, course_id)
            if cached is not None:
                course = Course.from_api(cached)
                if course is not None and course.enrollment_term_id == term_id:
                    return course

        payload = await self.client.get_json(f"/api/v1/courses/{course_id}")
        course = Course.from_api(payload)
        if course is None:
            return None
        if course.enrollment_term_id != term_id:
            return None

        if self._cache is not None:
            self._cache.set(CacheEntity.COURSE, course_id, payload)

        return course
