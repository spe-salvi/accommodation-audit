"""
Domain models for Canvas LMS entities.

Each model corresponds to a Canvas API resource and provides two
factory methods for construction from API payloads:

  - ``from_api(data)`` — parse a single JSON dict into a model instance,
    returning None if required fields are missing or invalid.
  - ``list_from_api(payload, ...)`` — parse a list of JSON dicts,
    silently skipping any that fail validation.

Design principles:
  - Models are frozen dataclasses (immutable after construction).
  - All parsing tolerates Canvas's inconsistencies (missing fields,
    mixed types, absent course_id that must be extracted from URLs).
  - No model performs I/O; all data arrives via the factory methods.
  - Each model exposes a ``key`` property for use as a dict/set key.
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse, parse_qs
from audit.models.parsing import (
    parse_int,
    parse_str,
    parse_quiz_id_from_submission,
    parse_submission_id,
    validate_engine_value,
    validate_payload_for_engine,
)

# ---------------------------------------------------------------------------
# URL-based ID extraction
#
# Some Canvas API responses omit course_id or quiz_id from the JSON body
# but embed them in URL fields (html_url, preview_url, etc.). These
# patterns extract IDs from those URLs as a fallback.
# ---------------------------------------------------------------------------

_COURSE_ID_PATTERN = re.compile(r"/courses/(\d+)")
_QUIZ_ID_PATTERN = re.compile(r"/quizzes/(\d+)")
_ASSIGNMENT_ID_PATTERN = re.compile(r"/assignments/(\d+)")


"""Try to extract a course ID from one or more URL-like strings."""
def _parse_course_id_from_urls(*values: Any) -> int | None:
    """Try to extract a course ID from one or more URL-like strings."""
    for value in values:
        text = parse_str(value, default="")
        if not text:
            continue
        match = _COURSE_ID_PATTERN.search(text)
        if match:
            return int(match.group(1))
    return None


"""
Try to extract a quiz or assignment ID from URL-like strings.

Checks for both ``/quizzes/<id>`` (classic) and
``/assignments/<id>`` (new engine) patterns.
"""
def _parse_quiz_id_from_urls(*values: Any) -> int | None:
    """
    Try to extract a quiz or assignment ID from URL-like strings.

    Checks for both ``/quizzes/<id>`` (classic) and
    ``/assignments/<id>`` (new engine) patterns.
    """
    for value in values:
        text = parse_str(value, default="")
        if not text:
            continue
        match = _QUIZ_ID_PATTERN.search(text)
        if match:
            return int(match.group(1))
        match = _ASSIGNMENT_ID_PATTERN.search(text)
        if match:
            return int(match.group(1))
    return None


# ---------------------------------------------------------------------------
# Term
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Term:
    """An enrollment term (semester/quarter) in Canvas."""

    term_id: int
    name: Optional[str]
    sis_term_id: Optional[str]

    @property
    def key(self) -> int:
        return self.term_id

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "Term":
        return cls(
            term_id=int(data.get("id") or None),
            name=str(data.get("name") or None),
            sis_term_id=str(data.get("sis_term_id") or None),
        )

    """Parse terms, handling the ``{"enrollment_terms": [...]}`` wrapper."""
    @classmethod
    def list_from_api(cls, payload: Dict[str, Any] | Iterable[Dict[str, Any]]) -> List["Term"]:
<<<<<<< HEAD
        """Parse terms, handling the ``{"enrollment_terms": [...]}`` wrapper."""
=======
>>>>>>> fb079c2a69e95c5965c6116b2ebe628e50ca8d04
        if isinstance(payload, dict) and "enrollment_terms" in payload:
            payload = payload["enrollment_terms"]
        return [cls.from_api(item) for item in payload]

<<<<<<< HEAD

=======
>>>>>>> fb079c2a69e95c5965c6116b2ebe628e50ca8d04
# ---------------------------------------------------------------------------
# Course
# ---------------------------------------------------------------------------

<<<<<<< HEAD
@dataclass(slots=True, frozen=True)
class Course:
    """A Canvas course, scoped to an enrollment term."""

=======
@dataclass(slots=True, frozen=True)    
class Course:
    """A Canvas course, scoped to an enrollment term."""
>>>>>>> fb079c2a69e95c5965c6116b2ebe628e50ca8d04
    course_id: int
    name: str
    course_code: Optional[str]
    sis_course_id: Optional[str]
    enrollment_term_id: Optional[int]

    @property
    def key(self) -> int:
        return self.course_id

    """Parse a single course payload. Returns None if ``id`` is missing."""
    @classmethod
    def from_api(cls, data: dict) -> "Course | None":
        """Parse a single course payload. Returns None if ``id`` is missing."""
        course_id = parse_int(data.get("id"))
        if course_id is None:
            return None

        return cls(
            course_id=course_id,
            name=parse_str(data.get("name")),
            course_code=parse_str(data.get("course_code"), default="") or None,
            sis_course_id=parse_str(data.get("sis_course_id"), default="") or None,
            enrollment_term_id=parse_int(data.get("enrollment_term_id")),
        )

    """
    Parse a list of course payloads.

    If *term_id* is provided, courses belonging to a different term
    are silently filtered out.
    """
    @classmethod
    def list_from_api(
        cls,
        payload: Iterable[Dict[str, Any]],
        term_id: int | None = None,
    ) -> List["Course"]:
        """
        Parse a list of course payloads.

        If *term_id* is provided, courses belonging to a different term
        are silently filtered out.
        """
        courses: list[Course] = []
        for item in payload:
            course = cls.from_api(item)
            if course is None:
                continue
            if term_id is not None and course.enrollment_term_id != term_id:
                continue
            courses.append(course)
        return courses
<<<<<<< HEAD


=======
    
>>>>>>> fb079c2a69e95c5965c6116b2ebe628e50ca8d04
# ---------------------------------------------------------------------------
# Quiz
# ---------------------------------------------------------------------------

@dataclass(slots=True, frozen=True)
class Quiz:
    """
    A quiz (classic or new engine) within a Canvas course.

    The ``engine`` field distinguishes classic quizzes (the original
    Canvas quiz tool) from new quizzes (the LTI-based replacement).
    The two engines have different API surfaces and different
    accommodation semantics.
    """

    course_id: int
    quiz_id: int
    engine: str
    title: str

    @property
    def key(self) -> tuple[int, int]:
        return (self.course_id, self.quiz_id)

    """
    Parse a single quiz payload.

    ``course_id`` resolution order:
        1. Explicit ``course_id`` field in the payload
        2. *course_id_override* argument (from caller context)
        3. Extracted from embedded URL fields (html_url, etc.)

    Returns None if quiz_id or course_id cannot be determined.
    """
    @classmethod
    def from_api(
        cls,
        engine: str,
        data: Dict[str, Any],
        course_id_override: int | None = None,
    ) -> "Quiz | None":
        """
        Parse a single quiz payload.

        ``course_id`` resolution order:
          1. Explicit ``course_id`` field in the payload
          2. *course_id_override* argument (from caller context)
          3. Extracted from embedded URL fields (html_url, etc.)

        Returns None if quiz_id or course_id cannot be determined.
        """
        engine = validate_engine_value(engine)

        quiz_id = parse_int(data.get("id"))
        if quiz_id is None:
            return None

        course_id = (
            parse_int(data.get("course_id"))
            or course_id_override
            or _parse_course_id_from_urls(
                data.get("html_url"),
                data.get("mobile_url"),
                data.get("quiz_reports_url"),
                data.get("quiz_statistics_url"),
                data.get("message_students_url"),
                data.get("quiz_submission_versions_html_url"),
                data.get("speed_grader_url"),
            )
        )
        if course_id is None:
            return None

        return cls(
            course_id=course_id,
            quiz_id=quiz_id,
            engine=engine,
            title=parse_str(data.get("title")),
        )

    """
    Parse a list of quiz payloads.

    *course_id_by_quiz* allows the caller to supply a pre-built
    mapping from quiz_id to course_id (useful when course_id was
    learned from submission data but is absent from the quiz payload).
    """
    @classmethod
    def list_from_api(
        cls,
        *,
        engine: str,
        payload: Iterable[Dict[str, Any]],
        course_id: int | None = None,
        course_id_by_quiz: dict[int, int] | None = None,
    ) -> List["Quiz"]:
        """
        Parse a list of quiz payloads.

        *course_id_by_quiz* allows the caller to supply a pre-built
        mapping from quiz_id to course_id (useful when course_id was
        learned from submission data but is absent from the quiz payload).
        """
        quizzes: list[Quiz] = []
        for item in payload:
            raw_quiz_id = parse_int(item.get("id"))
<<<<<<< HEAD
            course_id_override = course_id
=======
            course_id_override = course_id  # use the known course_id as default override
>>>>>>> fb079c2a69e95c5965c6116b2ebe628e50ca8d04
            if raw_quiz_id is not None and course_id_by_quiz is not None:
                course_id_override = course_id_by_quiz.get(raw_quiz_id) or course_id_override

            quiz = cls.from_api(
                engine=engine,
                data=item,
                course_id_override=course_id_override,
            )
            if quiz is None:
                continue
            if course_id is not None and quiz.course_id != course_id:
                continue
            quizzes.append(quiz)
        return quizzes

<<<<<<< HEAD

=======
>>>>>>> fb079c2a69e95c5965c6116b2ebe628e50ca8d04
# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------

@dataclass(slots=True, frozen=True)
class User:
<<<<<<< HEAD
    """A Canvas user (student, instructor, etc.)."""

=======
    """A Canvas user (can be a student, instructor, etc., but will eventually naturally be a student)."""
>>>>>>> fb079c2a69e95c5965c6116b2ebe628e50ca8d04
    id: int
    sortable_name: str
    sis_user_id: Optional[str]

    @property
    def key(self) -> int:
        return self.id

    @classmethod
    def from_api(cls, data: dict) -> "User":
        return cls(
            id=int(data.get("id") or None),
            sortable_name=str(data.get("sortable_name") or None),
            sis_user_id=str(data.get("sis_user_id") or None),
        )

    @classmethod
    def list_from_api(cls, payload: Iterable[Dict[str, Any]]) -> List["User"]:
        return [cls.from_api(item) for item in payload]

# ---------------------------------------------------------------------------
# Participant (New Quiz engine only)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Participant (New Quiz engine only)
# ---------------------------------------------------------------------------

@dataclass(slots=True, frozen=True)
class Participant:
    """
    A quiz participant from the New Quizzes API.

    Participants carry enrollment-level accommodation data (extra time,
    timer multiplier) and session linkage. Classic quizzes do not have
    a participant concept — their accommodation data lives on the
    submission instead.

    Session handling:
        Each participant may have zero or more ``participant_sessions``.
        Students are expected to have at most one session per quiz.
<<<<<<< HEAD
        The first session's IDs are captured for submission matching.
    """

=======
        The first session is captured right now for simplicity.
    """
>>>>>>> fb079c2a69e95c5965c6116b2ebe628e50ca8d04
    course_id: int
    quiz_id: int
    user_id: int
    engine: str

    participant_id: int
    extra_attempts: int

    timer_multiplier_enabled: bool
    timer_multiplier_value: float

    extra_time_enabled: bool
    extra_time_in_seconds: int

    participant_session_id: Optional[str]
    quiz_session_id: Optional[str]

    @property
    def key(self) -> tuple[int, int, int]:
        return (self.course_id, self.quiz_id, self.user_id)

    """Composite key for matching participants to submissions by session."""
    @property
    def session_key(self) -> tuple[Optional[str], Optional[str]]:
        """Composite key for matching participants to submissions by session."""
        return (self.participant_session_id, self.quiz_session_id)

    @classmethod
    def from_api(
        cls,
        course_id: int,
        quiz_id: int,
        engine: str,
        data: Dict[str, Any],
    ) -> "Participant | None":
        """
        Parse a single participant payload.

        Accommodation data is nested under the ``enrollment`` key.
        Session data comes from the ``participant_sessions`` array.
        Returns None if user_id or participant_id is missing.
        """
        engine = validate_engine_value(engine)

        user_id = parse_int(data.get("user_id"))
        participant_id = parse_int(data.get("id"))
        if user_id is None or participant_id is None:
            return None

        enrollment = data.get("enrollment", {})
        sessions = data.get("participant_sessions", [])

        # Students are expected to have at most one session per quiz.
        # Extra time is an enrollment-level accommodation, not session-specific.
        first_session = sessions[0] if sessions else {}

        return cls(
            course_id=int(course_id),
            quiz_id=int(quiz_id),
            user_id=int(data.get("user_id") or None),
            engine=engine,
            participant_id=int(data.get("id") or None),
            extra_attempts=int(data.get("extra_attempts") or 0),
            timer_multiplier_enabled=bool(enrollment.get("timer_multiplier_enabled") or False),
            timer_multiplier_value=float(enrollment.get("timer_multiplier_value") or 0),
            extra_time_enabled=bool(enrollment.get("extra_time_enabled") or False),
            extra_time_in_seconds=int(enrollment.get("extra_time_in_seconds") or 0),
            participant_session_id=str(first_session.get("id") or None),
            quiz_session_id=str(first_session.get("quiz_api_quiz_session_id") or None),
        )

    @classmethod
    def list_from_api(
        cls,
        course_id: int,
        quiz_id: int,
        engine: str,
        payload: Iterable[Dict[str, Any]],
    ) -> List["Participant"]:
        """Parse a list of participant payloads, skipping invalid entries."""
        participants: list[Participant] = []
        for item in payload:
            participant = cls.from_api(
                course_id=course_id,
                quiz_id=quiz_id,
                engine=engine,
                data=item,
            )
            if participant is not None:
                participants.append(participant)
        return participants

<<<<<<< HEAD

=======
>>>>>>> fb079c2a69e95c5965c6116b2ebe628e50ca8d04
# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------

@dataclass(slots=True, frozen=True)
class Enrollment:
    """A user's enrollment in a specific course."""
<<<<<<< HEAD

=======
>>>>>>> fb079c2a69e95c5965c6116b2ebe628e50ca8d04
    user_id: int
    course_id: int

    @property
    def key(self) -> tuple[int, int]:
        return (self.user_id, self.course_id)

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> Optional["Enrollment"]:
        return cls(
            user_id=int(data.get("user_id") or None),
            course_id=int(data.get("course_id") or None),
        )

    @classmethod
    def list_from_api(cls, payload: Iterable[Dict[str, Any]]) -> List["Enrollment"]:
        return [cls.from_api(item) for item in payload]

<<<<<<< HEAD

=======
>>>>>>> fb079c2a69e95c5965c6116b2ebe628e50ca8d04
# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------

@dataclass(slots=True, frozen=True)
class Submission:
    """
    A student's quiz submission (classic or new engine).

    Submissions carry per-student accommodation overrides (extra_time,
    extra_attempts) for classic quizzes, and session linkage for new
    quizzes.

    The ``date`` field is derived from ``workflow_state`` and simplified
    to "past" (completed/graded) or "future" (not yet submitted).
    This supports filtering audit results by completion status.
    """
<<<<<<< HEAD

=======
>>>>>>> fb079c2a69e95c5965c6116b2ebe628e50ca8d04
    user_id: int
    course_id: int
    quiz_id: int
    engine: str
    submission_id: int | None
    attempt: int
    extra_attempts: int
    extra_time: int
    date: str
    participant_session_id: str | None
    quiz_session_id: str | None

    @property
    def key(self) -> tuple[int, int, int]:
        return (self.course_id, self.quiz_id, self.user_id)

    """Map Canvas workflow_state to a simplified temporal label."""
    @staticmethod
    def _workflow_to_date(workflow: str) -> str:
        """Map Canvas workflow_state to a simplified temporal label."""
        if workflow in ("complete", "graded"):
            return "past"
        if workflow in ("settings_only", "unsubmitted"):
            return "future"
        return ""

    """
    Extract session IDs from a new-quiz submission's external_tool_url.

    New quiz submissions embed participant_session_id and
    quiz_session_id as query parameters on the external_tool_url.
    These IDs are used to match submissions to participants.
    """
    @staticmethod
    def _parse_new_quiz_session_ids(
        external_tool_url: str | None,
    ) -> tuple[str | None, str | None]:
        """
        Extract session IDs from a new-quiz submission's external_tool_url.

        New quiz submissions embed participant_session_id and
        quiz_session_id as query parameters on the external_tool_url.
        These IDs are used to match submissions to participants.
        """
        if not external_tool_url:
            return None, None

        parsed = urlparse(external_tool_url)
        query = parse_qs(parsed.query)
        return (
            query.get("participant_session_id", [None])[0],
            query.get("quiz_session_id", [None])[0],
        )

    """
    Parse a single submission payload.

    For new-engine submissions, validation is strict (raises on
    missing assignment_id). For classic submissions, validation
    is lenient — some classic payloads lack quiz_id in the body
    and we fall back to URL extraction.

    Returns None if user_id, quiz_id, or course_id cannot be
    determined from any source.
    """
    @classmethod
    def from_api(
        cls,
        *,
        engine: str,
        data: dict,
    ) -> "Submission | None":
        """
        Parse a single submission payload.

        For new-engine submissions, validation is strict (raises on
        missing assignment_id). For classic submissions, validation
        is lenient — some classic payloads lack quiz_id in the body
        and we fall back to URL extraction.

        Returns None if user_id, quiz_id, or course_id cannot be
        determined from any source.
        """
        engine = validate_engine_value(engine)

        if engine == "new":
            validate_payload_for_engine(data, engine)
        else:
            try:
                validate_payload_for_engine(data, engine)
            except (TypeError, ValueError, KeyError):
                pass

        actual_quiz_id = (
            parse_quiz_id_from_submission(data, engine)
            or _parse_quiz_id_from_urls(
                data.get("url"),
                data.get("preview_url"),
                data.get("html_url"),
                data.get("result_url"),
            )
        )
        submission_id = parse_submission_id(data, engine)
        participant_session_id, quiz_session_id = cls._parse_new_quiz_session_ids(
            parse_str(data.get("external_tool_url"), default="") or None
        )

        user_id = parse_int(data.get("user_id"))

        course_id = (
            parse_int(data.get("course_id"))
            or _parse_course_id_from_urls(
                data.get("url"),
                data.get("preview_url"),
                data.get("html_url"),
                data.get("result_url"),
            )
        )

        if actual_quiz_id is None or user_id is None or course_id is None:
            return None

        workflow_state = parse_str(data.get("workflow_state"))
        date = cls._workflow_to_date(workflow_state)

        return cls(
            user_id=user_id,
            course_id=course_id,
            quiz_id=actual_quiz_id,
            engine=engine,
            submission_id=submission_id,
            attempt=parse_int(data.get("attempt"), 0) or 0,
            extra_attempts=parse_int(data.get("extra_attempts"), 0) or 0,
            extra_time=parse_int(data.get("extra_time"), 0) or 0,
            date=date,
            participant_session_id=participant_session_id,
            quiz_session_id=quiz_session_id,
        )

    """
    Parse a list of submission payloads.
    Handles the ``{"quiz_submissions": [...]}`` wrapper that
    classic quiz submission endpoints return. Optional *course_id*
    and *quiz_id* filters let the caller scope results.
    """
    @classmethod
    def list_from_api(
        cls,
        *,
        engine: str,
        payload: dict | list[dict],
        course_id: int | None = None,
        quiz_id: int | None = None,
    ) -> list["Submission"]:
        """
        Parse a list of submission payloads.

        Handles the ``{"quiz_submissions": [...]}`` wrapper that
        classic quiz submission endpoints return. Optional *course_id*
        and *quiz_id* filters let the caller scope results.
        """
        engine = validate_engine_value(engine)

        if isinstance(payload, dict) and "quiz_submissions" in payload:
            payload = payload["quiz_submissions"]

        if not isinstance(payload, list):
            return []

        submissions: list[Submission] = []
        for item in payload:
            submission = cls.from_api(engine=engine, data=item)
            if submission is None:
                continue
            if course_id is not None and submission.course_id != course_id:
                continue
            if quiz_id is not None and submission.quiz_id != quiz_id:
                continue
            submissions.append(submission)

        return submissions
<<<<<<< HEAD


=======
    
>>>>>>> fb079c2a69e95c5965c6116b2ebe628e50ca8d04
# ---------------------------------------------------------------------------
# NewQuizItem
# ---------------------------------------------------------------------------

@dataclass(slots=True, frozen=True)
class NewQuizItem:
    """
    A single question/item within a New Quiz.

    Used primarily for spell-check auditing: each essay-type item
    may have spell-check independently enabled or disabled.

    Only meaningful for new-engine quizzes; classic quizzes do not
    expose per-item configuration via the API.
    """
<<<<<<< HEAD

=======
>>>>>>> fb079c2a69e95c5965c6116b2ebe628e50ca8d04
    course_id: int
    quiz_id: int
    engine: str
    item_id: int
    position: int
    interaction_type_slug: str
    essay_spell_check_enabled: Optional[bool]

    @property
    def key(self) -> tuple[int, int, int]:
        return (self.course_id, self.quiz_id, self.item_id)

    """
    Parse a single quiz item payload.

    Spell-check status is only extracted for essay-type items.
    For all other item types, ``essay_spell_check_enabled`` is None.

    Note:
        The spell-check flag is read from ``entry.interaction_data``
        rather than ``entry.properties`` because the two can
        disagree — interaction_data is the authoritative source.
    """
    @classmethod
    def from_api(cls, course_id: int, quiz_id: int, engine: str, data: Dict[str, Any]) -> "NewQuizItem":
        """
        Parse a single quiz item payload.

        Spell-check status is only extracted for essay-type items.
        For all other item types, ``essay_spell_check_enabled`` is None.

        Note:
            The spell-check flag is read from ``entry.interaction_data``
            rather than ``entry.properties`` because the two can
            disagree — interaction_data is the authoritative source.
        """
        entry = data.get("entry") or {}
        slug = str(entry.get("interaction_type_slug") or "")

        spell_check: Optional[bool] = None
        if slug == "essay":
            interaction_data = entry.get("interaction_data") or {}
            spell_check = bool(interaction_data.get("spell_check") or False)

        return cls(
            course_id=int(course_id),
            quiz_id=int(quiz_id),
            engine=engine,
            item_id=int(data.get("id") or 0),
            position=int(data.get("position") or 0),
            interaction_type_slug=slug,
            essay_spell_check_enabled=spell_check,
        )

    """Parse a list of quiz item payloads."""
    @classmethod
    def list_from_api(
        cls,
        course_id: int,
        quiz_id: int,
        engine: str,
        payload: Iterable[Dict[str, Any]],
    ) -> List["NewQuizItem"]:
        """Parse a list of quiz item payloads."""
        return [cls.from_api(course_id=course_id, quiz_id=quiz_id, engine=engine, data=item) for item in payload]
