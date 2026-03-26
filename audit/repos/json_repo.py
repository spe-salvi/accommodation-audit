"""
Local JSON file repository implementation.

This module implements the ``AccommodationRepo`` protocol by reading
Canvas API response data from local JSON files. It was the first
repository built and remains essential for two purposes:

  1. **Development:** Iterate on business logic without network access
     or Canvas API rate limits.
  2. **Testing:** Provide deterministic, reproducible data for the
     test suite without mocking HTTP calls.

On initialization, ``JsonRepo`` eagerly loads and indexes all data
into a ``JsonCatalog`` — an in-memory structure with dict-based
lookups keyed by (course_id, quiz_id, engine, user_id) tuples.
This makes all subsequent reads O(1) and avoids repeated file I/O.

Engine inference:
    Since local JSON files don't carry an explicit engine label, the
    repo infers the engine from payload structure (e.g., presence of
    ``quiz_id`` vs. ``assignment_id``). See ``_infer_quiz_engine``
    and ``_infer_submission_engine`` for the heuristics.
"""

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from audit.models.canvas import Course, Quiz, Participant, Submission, NewQuizItem


@dataclass(slots=True)
class JsonCatalog:
    """
    In-memory index of all loaded data.

    Each dict is keyed by a composite tuple for O(1) lookups.
    Built once during ``JsonRepo.__init__`` and never mutated afterward.
    """

    courses_by_term: dict[int, list[Course]] = field(default_factory=dict)
    courses_by_id: dict[int, Course] = field(default_factory=dict)

    quizzes_by_course_engine: dict[tuple[int, str], list[Quiz]] = field(default_factory=dict)
    quizzes_by_key: dict[tuple[int, int, str], Quiz] = field(default_factory=dict)

    submissions_by_quiz_engine: dict[tuple[int, int, str], list[Submission]] = field(default_factory=dict)
    submissions_by_user: dict[tuple[int, int, int, str], Submission] = field(default_factory=dict)


class JsonRepo:
    """
    File-based implementation of AccommodationRepo.

    Reads Canvas API response JSON from local files, parses them into
    domain models, and indexes everything into a ``JsonCatalog`` for
    fast lookups. Methods are async to conform to the protocol, even
    though the underlying reads are synchronous — this ensures the
    service layer can use ``await`` uniformly regardless of the repo.

    Args:
        participant_path: Path to a JSON file containing participant data.
        submission_path: Path to a JSON file containing submission data.
        items_path: Path to a JSON file containing quiz item data.
        quizzes_path: Path to a JSON file containing quiz data.
        courses_path: Path to a JSON file containing course data.
    """

    def __init__(
        self,
        *,
        participant_path: str | None = None,
        submission_path: str | None = None,
        items_path: str | None = None,
        quizzes_path: str | None = None,
        courses_path: str | None = None,
    ):
        self.participant_path = Path(participant_path) if participant_path else None
        self.submission_path = Path(submission_path) if submission_path else None
        self.items_path = Path(items_path) if items_path else None
        self.quizzes_path = Path(quizzes_path) if quizzes_path else None
        self.courses_path = Path(courses_path) if courses_path else None

        self._participants_cache: dict[tuple[int, int, str], list[Participant]] = {}
        self._items_cache: dict[tuple[int, int, str], list[NewQuizItem]] = {}
        self._catalog = self._build_catalog()

    # ------------------------------------------------------------------
    # Catalog construction
    # ------------------------------------------------------------------

    def _load_json(self, path: Path | None) -> object:
        """Load and parse a JSON file, returning an empty list if path is None."""
        if path is None:
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def _build_catalog(self) -> JsonCatalog:
        """
        Parse all JSON files and build the in-memory index.

        Processing order matters: submissions are loaded before quizzes
        so that ``course_id_by_quiz`` (derived from submission data) is
        available to fill in missing course_id on quiz payloads.
        """
        catalog = JsonCatalog()

        # --- Courses ---
        courses_payload = self._load_json(self.courses_path)
        if isinstance(courses_payload, list):
            courses = Course.list_from_api(courses_payload)
            courses_by_term: dict[int, list[Course]] = defaultdict(list)
            for course in courses:
                catalog.courses_by_id[course.course_id] = course
                if course.enrollment_term_id is not None:
                    courses_by_term[course.enrollment_term_id].append(course)
            catalog.courses_by_term = dict(courses_by_term)

        # --- Submissions (loaded before quizzes to build course_id_by_quiz) ---
        submissions: list[Submission] = []
        course_id_by_quiz: dict[int, int] = {}

        submissions_payload = self._load_json(self.submission_path)
        if submissions_payload:
            engine = self._infer_submission_engine(submissions_payload)
            submissions = Submission.list_from_api(
                engine=engine,
                payload=submissions_payload,
            )

            submissions_by_quiz_engine: dict[tuple[int, int, str], list[Submission]] = defaultdict(list)
            for submission in submissions:
                submissions_by_quiz_engine[
                    (submission.course_id, submission.quiz_id, submission.engine)
                ].append(submission)
                catalog.submissions_by_user[
                    (submission.course_id, submission.quiz_id, submission.user_id, submission.engine)
                ] = submission

                if submission.quiz_id not in course_id_by_quiz:
                    course_id_by_quiz[submission.quiz_id] = submission.course_id

            catalog.submissions_by_quiz_engine = dict(submissions_by_quiz_engine)

        # --- Quizzes ---
        quizzes_payload = self._load_json(self.quizzes_path)
        if isinstance(quizzes_payload, list):
            engine = self._infer_quiz_engine(quizzes_payload)
            quizzes = Quiz.list_from_api(
                engine=engine,
                payload=quizzes_payload,
                course_id_by_quiz=course_id_by_quiz,
            )

            quizzes_by_course_engine: dict[tuple[int, str], list[Quiz]] = defaultdict(list)
            for quiz in quizzes:
                quizzes_by_course_engine[(quiz.course_id, quiz.engine)].append(quiz)
                catalog.quizzes_by_key[(quiz.course_id, quiz.quiz_id, quiz.engine)] = quiz

            catalog.quizzes_by_course_engine = dict(quizzes_by_course_engine)

        return catalog

    # ------------------------------------------------------------------
    # Engine inference heuristics
    # ------------------------------------------------------------------

    def _infer_quiz_engine(self, payload: list[dict]) -> str:
        """
        Guess the quiz engine from a quiz payload's structure.

        Classic quizzes have fields like ``quiz_reports_url`` or
        ``html_url``. If neither is present, assume new engine.
        Defaults to "classic" for empty payloads.
        """
        if not payload:
            return "classic"
        first = payload[0]
        if "quiz_reports_url" in first or "html_url" in first:
            return "classic"
        return "new"

    def _infer_submission_engine(self, payload: object) -> str:
        """
        Guess the engine from a submission payload's structure.

        Checks for ``quiz_id`` (classic) vs. ``assignment_id`` (new),
        handling both the wrapped dict form and bare list form.
        Defaults to "classic" when the structure is ambiguous.
        """
        if isinstance(payload, dict) and "quiz_submissions" in payload:
            rows = payload["quiz_submissions"]
            if rows:
                first = rows[0]
                if "quiz_id" in first:
                    return "classic"
                if "assignment_id" in first:
                    return "new"
            return "classic"

        if isinstance(payload, list) and payload:
            first = payload[0]
            if "quiz_id" in first:
                return "classic"
            if "assignment_id" in first:
                return "new"

        return "classic"

    # ------------------------------------------------------------------
    # AccommodationRepo protocol methods
    # ------------------------------------------------------------------

    async def list_participants(self, *, course_id: int, quiz_id: int, engine: str) -> list[Participant]:
        """Load participants from JSON, caching after first read."""
        key = (course_id, quiz_id, engine)
        if key in self._participants_cache:
            return list(self._participants_cache[key])

        if engine != "new" or self.participant_path is None:
            self._participants_cache[key] = []
            return []

        data = self._load_json(self.participant_path)
        payload = data if isinstance(data, list) else []

        participants = Participant.list_from_api(
            course_id=course_id,
            quiz_id=quiz_id,
            engine=engine,
            payload=payload,
        )
        self._participants_cache[key] = participants
        return list(participants)

    async def get_participant(self, *, course_id: int, quiz_id: int, user_id: int, engine: str) -> Optional[Participant]:
        """Find a single participant by user_id via list scan."""
        participants = await self.list_participants(course_id=course_id, quiz_id=quiz_id, engine=engine)
        for p in participants:
            if p.user_id == user_id:
                return p
        return None

    async def list_submissions(self, *, course_id: int, quiz_id: int, engine: str) -> list[Submission]:
        """Look up pre-indexed submissions by composite key."""
        return list(
            self._catalog.submissions_by_quiz_engine.get((course_id, quiz_id, engine), [])
        )

    async def get_submission(self, *, course_id: int, quiz_id: int, engine: str, user_id: int) -> Optional[Submission]:
        """Look up a pre-indexed submission by composite key."""
        return self._catalog.submissions_by_user.get(
            (course_id, quiz_id, user_id, engine)
        )

    async def list_items(self, *, course_id: int, quiz_id: int, engine: str) -> list[NewQuizItem]:
        """Load quiz items from JSON, caching after first read."""
        key = (course_id, quiz_id, engine)
        if key in self._items_cache:
            return list(self._items_cache[key])

        if engine != "new" or self.items_path is None:
            self._items_cache[key] = []
            return []

        data = self._load_json(self.items_path)
        payload = data if isinstance(data, list) else []

        items = NewQuizItem.list_from_api(
            course_id=course_id,
            quiz_id=quiz_id,
            engine=engine,
            payload=payload,
        )
        self._items_cache[key] = items
        return list(items)

    async def list_quizzes(self, *, course_id: int, engine: str) -> list[Quiz]:
        """Look up pre-indexed quizzes by (course_id, engine)."""
        return list(self._catalog.quizzes_by_course_engine.get((course_id, engine), []))

    async def get_quiz(self, *, course_id: int, quiz_id: int, engine: str) -> Optional[Quiz]:
        """Look up a pre-indexed quiz by composite key."""
        return self._catalog.quizzes_by_key.get((course_id, quiz_id, engine))

    async def list_courses(self, *, term_id: int, engine: str) -> list[Course]:
        """Look up pre-indexed courses by term_id."""
        return list(self._catalog.courses_by_term.get(term_id, []))

    async def get_course(self, *, term_id: int, course_id: int, engine: str) -> Optional[Course]:
        """Look up a pre-indexed course, validating term membership."""
        course = self._catalog.courses_by_id.get(course_id)
        if course is None:
            return None
        if course.enrollment_term_id != term_id:
            return None
        return course
