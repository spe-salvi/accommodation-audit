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

  - Terms   (TTL: 1 year  — essentially never change)
  - Courses (TTL: 30 days — stable within a term)
  - Quizzes (TTL: 1 day   — instructors may edit during a term)
  - Users   (TTL: 1 year  — name/SIS changes are rare)

Submissions, participants, and items are intentionally NOT cached —
they change frequently during a term and must always reflect the
current state of Canvas.

Enrollments are also not cached — enrollment status changes (drops,
late adds) and must always reflect the current state of Canvas.

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
from typing import Optional

from audit.cache.persistent import CacheEntity, PersistentCache
from audit.clients.canvas_client import CanvasClient
from audit.clients.new_quiz_client import NewQuizClient
from audit.exceptions import LtiError
from audit.models.canvas import (
    Course, Enrollment, NewQuizItem, Participant,
    Quiz, Submission, Term, User,
)
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
    # Terms  (cached by account_id, TTL 1 year)
    # ------------------------------------------------------------------

    async def list_terms(self) -> list[Term]:
        """
        Fetch all enrollment terms for this account.

        Cached by account_id with a 1-year TTL. Terms are essentially
        immutable in Canvas — created once per semester, never renamed.
        """
        cache_key = f"account:{self._account_id}:terms"

        if self._cache is not None:
            cached = self._cache.get_list(CacheEntity.TERM, cache_key)
            if cached is not None:
                return Term.list_from_api(cached)

        raw_list = await self.client.get_paginated_json(
            f"/api/v1/accounts/{self._account_id}/terms"
        )
        terms = Term.list_from_api(raw_list)

        if self._cache is not None:
            self._cache.set(CacheEntity.TERM, cache_key, raw_list)

        logger.debug("list_terms: fetched %d terms from Canvas", len(terms))
        return terms

    # ------------------------------------------------------------------
    # Users  (cached by user_id, TTL 1 year)
    # ------------------------------------------------------------------

    async def get_user(self, user_id: int) -> User | None:
        """
        Fetch a single user's profile by user ID.

        Cached by ``user_id`` with a 1-year TTL. Uses
        ``/api/v1/users/{user_id}/profile`` which returns ``sortable_name``
        and ``sis_user_id``.

        Returns None if the user cannot be found or parsed.
        """
        if self._cache is not None:
            cached = self._cache.get(CacheEntity.USER, user_id)
            if cached is not None:
                return User.from_api(cached)

        try:
            payload = await self.client.get_json(
                f"/api/v1/users/{user_id}/profile"
            )
        except Exception as exc:
            logger.warning(
                "get_user: failed to fetch user_id=%d: %s", user_id, exc
            )
            return None

        user = User.from_api(payload)
        if user is None:
            return None

        if self._cache is not None:
            self._cache.set(CacheEntity.USER, user_id, payload)

        return user

    # ------------------------------------------------------------------
    # Enrollments  (not cached — enrollment status changes frequently)
    # ------------------------------------------------------------------

    async def list_enrollments(
        self,
        user_id: int,
        *,
        term_id: int | None = None,
    ) -> list[Enrollment]:
        """
        Fetch all active course enrollments for a user.

        Not cached — enrollment status changes when students drop or add
        courses and must always reflect the current state of Canvas.

        Parameters
        ----------
        user_id:
            Canvas user ID to look up enrollments for.
        term_id:
            Optional enrollment term ID. When provided, the Canvas API
            filters results server-side to that term, avoiding the need
            to page through all historical enrollments.

        Returns
        -------
        list[Enrollment]
            Active student enrollments only (``enrollment_state=active``).
            Inactive, invited, and rejected enrollments are excluded so
            that user-scoped audits only cover courses the student is
            currently attending.
        """
        params: dict = {"type[]": "StudentEnrollment", "state[]": "active"}
        if term_id is not None:
            params["enrollment_term_id"] = term_id

        payload = await self.client.get_paginated_json(
            f"/api/v1/users/{user_id}/enrollments",
            params=params,
        )

        enrollments = Enrollment.list_from_api(payload)
        logger.debug(
            "list_enrollments: user_id=%d term_id=%s → %d active enrollment(s)",
            user_id,
            term_id,
            len(enrollments),
        )
        return enrollments

    # ------------------------------------------------------------------
    # Participants  (not cached — changes frequently)
    # ------------------------------------------------------------------

    async def list_participants(
        self, *, course_id: int, quiz_id: int, engine: str
    ) -> list[Participant]:
        """
        Fetch all participants for a new-engine quiz.

        Not cached — participant data changes when accommodations are
        updated and must always be current.
        """
        if engine != "new" or self._new_quiz_client is None:
            return []

        try:
            payload = await self._new_quiz_client.list_participants(
                canvas_assignment_id=quiz_id,
            )
        except LtiError as exc:
            logger.warning(
                "list_participants: LTI error for quiz_id=%d: %s", quiz_id, exc,
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

        Not cached — submission data changes as students complete quizzes.
        """
        if engine == "new":
            path = (
                f"/api/v1/courses/{course_id}/assignments/{quiz_id}/submissions"
            )
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

        Not cached — spell-check settings may be updated by instructors.
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

        Cached by ``(course_id, engine)`` with a 1-day TTL.
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

        Cached by ``term_id`` with a 30-day TTL.
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


    async def get_course_by_id(self, course_id: int) -> Course | None:
        """
        Fetch a single course by ID without term validation.

        Used when the caller knows the course_id but not the term_id —
        for example, when resolving course metadata from an enrollment.

        Checks the persistent cache first (courses are cached by course_id
        in get_course, so this will usually be a hit after the first
        term audit). Only falls back to the Canvas API on a cache miss.

        Returns None if the course cannot be found or parsed.
        """
        if self._cache is not None:
            cached = self._cache.get(CacheEntity.COURSE, course_id)
            if cached is not None:
                course = Course.from_api(cached)
                if course is not None:
                    return course

        try:
            payload = await self.client.get_json(f"/api/v1/courses/{course_id}")
        except Exception as exc:
            logger.warning(
                "get_course_by_id: failed to fetch course_id=%d: %s",
                course_id, exc,
            )
            return None

        course = Course.from_api(payload)
        if course is None:
            return None

        if self._cache is not None:
            self._cache.set(CacheEntity.COURSE, course_id, payload)

        return course