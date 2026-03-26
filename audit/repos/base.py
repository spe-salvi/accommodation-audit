"""
Repository protocol and shared types for the data access layer.

This module defines the ``AccommodationRepo`` protocol — the abstract
interface that the business logic layer (``AccommodationService``)
programs against. Any class that implements these methods can serve
as a data source, whether it reads from local JSON files, calls the
Canvas API, or pulls from a cache.

This is the key seam in the architecture: by depending on a protocol
rather than a concrete class, the service layer is completely
decoupled from I/O concerns.
"""

from enum import Enum
from typing import Protocol, Optional
from audit.models.canvas import Course, Quiz, Participant, Submission, NewQuizItem


"""
The types of quiz accommodations this system can audit.

Each value corresponds to a distinct evaluation strategy in the
service layer. Extending the system with a new accommodation type
means adding a value here and registering an evaluator.
"""
class AccommodationType(str, Enum):

    EXTRA_TIME = "extra_time"
    EXTRA_ATTEMPT = "extra_attempt"
    SPELL_CHECK = "spell_check"


"""
Abstract data access interface for accommodation auditing.

Implementations must provide async methods for retrieving
participants, submissions, quiz items, quizzes, and courses.
The service layer calls these methods without knowing whether
the data comes from a file, an API, or a cache.

Current implementations:
    - ``CanvasRepo``: Live Canvas API calls
    - ``JsonRepo``: Local JSON files (development/testing)
"""
class AccommodationRepo(Protocol):

    async def list_participants(
        self, *, course_id: int, quiz_id: int, engine: str
    ) -> list[Participant]:
        """List all participants for a quiz (new engine only)."""
        ...

    async def get_participant(
        self, *, course_id: int, quiz_id: int, user_id: int, engine: str
    ) -> Optional[Participant]:
        """Get a single participant by user ID."""
        ...

    async def list_submissions(
        self, *, course_id: int, quiz_id: int, engine: str
    ) -> list[Submission]:
        """List all submissions for a quiz."""
        ...

    async def get_submission(
        self, *, course_id: int, quiz_id: int, user_id: int, engine: str
    ) -> Optional[Submission]:
        """Get a single submission by user ID."""
        ...

    async def list_items(
        self, *, course_id: int, quiz_id: int, engine: str
    ) -> list[NewQuizItem]:
        """List all items/questions for a quiz (new engine only)."""
        ...

    async def list_quizzes(
        self, *, course_id: int, engine: str
    ) -> list[Quiz]:
        """List all quizzes in a course for a given engine."""
        ...

    async def get_quiz(
        self, *, course_id: int, quiz_id: int, engine: str
    ) -> Optional[Quiz]:
        """Get a single quiz by ID."""
        ...

    async def list_courses(
        self, *, term_id: int, engine: str
    ) -> list[Course]:
        """List all courses in a term."""
        ...

    async def get_course(
        self, *, term_id: int, course_id: int, engine: str
    ) -> Optional[Course]:
        """Get a single course, validating it belongs to the given term."""
        ...    