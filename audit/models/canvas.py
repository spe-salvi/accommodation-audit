from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse, parse_qs
from audit.models.parsing import parse_int, parse_str, validate_expected_id, parse_quiz_id, parse_submission_id, validate_engine_value, validate_payload_for_engine

@dataclass(slots=True)
class Term:
    id: int
    name: str
    sis_term_id: Optional[str]

    @property
    def key(self) -> int:
        return self.id

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "Term":
        return cls(
            id=int(data.get("id") or None),
            name=str(data.get("name") or None),
            sis_term_id=str(data.get("sis_term_id") or None),
        )

    @classmethod
    def list_from_api(cls, payload: Dict[str, Any] | Iterable[Dict[str, Any]]) -> List["Term"]:
        # Handle wrapped response
        if isinstance(payload, dict) and "enrollment_terms" in payload:
            payload = payload["enrollment_terms"]

        return [cls.from_api(item) for item in payload]
    
@dataclass
class Course:
    id: int
    name: str
    course_code: Optional[str]
    sis_course_id: Optional[str]

    @property
    def key(self) -> int:
        return self.id
    
    @classmethod
    def from_api(cls, data: dict, course_id: int | None = None) -> "Course | None":
        raw_course_id = parse_int(data.get("id"))
        if validate_expected_id(course_id, raw_course_id) is None:
            return None

        return cls(
            id=course_id,
            name=parse_str(data.get("name")),
            course_code=parse_str(data.get("course_code"), default="") or None,
            sis_course_id=parse_str(data.get("sis_course_id"), default="") or None,
        )

    @classmethod
    def list_from_api(cls, payload: Iterable[Dict[str, Any]]) -> List["Course"]:
        return [cls.from_api(item) for item in payload]
    

@dataclass(frozen=True)
class Quiz:
    course_id: int
    id: int
    engine: str
    title: str

    @property
    def key(self) -> tuple[int, int]:
        return (self.course_id, self.id)

    @classmethod
    def from_api(cls, course_id: int, engine: str, data: Dict[str, Any]) -> "Quiz":
        return cls(
            course_id=int(course_id),
            id=int(data.get("id") or None),
            engine=engine,
            title=str(data.get("title") or ""),
        )

    @classmethod
    def list_from_api(
        cls,
        course_id: int,
        engine: str,
        payload: Iterable[Dict[str, Any]],
    ) -> List["Quiz"]:
        return [cls.from_api(course_id=course_id, engine=engine, data=item) for item in payload]

@dataclass
class User:
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


@dataclass(frozen=True, slots=True)
class Participant:
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

    @property
    def session_key(self) -> tuple[Optional[str], Optional[str]]:
        return (self.participant_session_id, self.quiz_session_id)
    
    @classmethod
    def from_api(cls, course_id: int, quiz_id: int, engine: str, data: Dict[str, Any]) -> "Participant":
        enrollment = data.get("enrollment", {})
        sessions = data.get("participant_sessions", [])

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
        return [cls.from_api(course_id=course_id, quiz_id=quiz_id, engine=engine, data=item) for item in payload]
    
@dataclass(slots=True)
class Enrollment:
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



@dataclass(slots=True)
class Submission:
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

    @staticmethod
    def _workflow_to_date(workflow: str) -> str:
        if workflow in ("complete", "graded"):
            return "past"
        if workflow in ("settings_only", "unsubmitted"):
            return "future"
        return ""

    @staticmethod
    def _parse_new_quiz_session_ids(
        external_tool_url: str | None,
    ) -> tuple[str | None, str | None]:
        if not external_tool_url:
            return None, None

        parsed = urlparse(external_tool_url)
        query = parse_qs(parsed.query)
        return (
            query.get("participant_session_id", [None])[0],
            query.get("quiz_session_id", [None])[0],
        )

    @classmethod
    def from_api(
        cls,
        course_id: int,
        quiz_id: int,
        engine: str,
        data: dict,
    ) -> "Submission | None":
        engine = validate_engine_value(engine)
        validate_payload_for_engine(data, engine)

        actual_quiz_id = validate_expected_id(
            parse_quiz_id(data, engine),
            expected=quiz_id,
        )
        user_id = parse_int(data.get("user_id"))
        submission_id = parse_submission_id(data, engine)

        if actual_quiz_id is None or user_id is None:
            return None

        workflow_state = parse_str(data.get("workflow_state"))
        date = cls._workflow_to_date(workflow_state)

        participant_session_id, quiz_session_id = cls._parse_new_quiz_session_ids(
            parse_str(data.get("external_tool_url"), default="") or None
        )

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

    @classmethod
    def list_from_api(
        cls,
        course_id: int,
        quiz_id: int,
        engine: str,
        payload: dict | list[dict],
    ) -> list["Submission"]:
        engine = validate_engine_value(engine)

        if isinstance(payload, dict) and "quiz_submissions" in payload:
            payload = payload["quiz_submissions"]

        if not isinstance(payload, list):
            return []

        submissions: list[Submission] = []
        for item in payload:
            submission = cls.from_api(
                course_id=course_id,
                quiz_id=quiz_id,
                engine=engine,
                data=item,
            )
            if submission is not None:
                submissions.append(submission)

        return submissions
    

@dataclass(slots=True)
class NewQuizItem:
    course_id: int
    quiz_id: int
    engine: str
    item_id: int
    position: int
    interaction_type_slug: str
    essay_spell_check_enabled: Optional[bool]

    @property
    def key(self) -> tuple[int, int, int]:
        # (course, quiz, item) uniquely identifies an item in your world
        return (self.course_id, self.quiz_id, self.item_id)

    @classmethod
    def from_api(cls, course_id: int, quiz_id: int, engine: str, data: Dict[str, Any]) -> "NewQuizItem":
        entry = data.get("entry") or {}
        slug = str(entry.get("interaction_type_slug") or "")

        spell_check: Optional[bool] = None
        if slug == "essay":
            interaction_data = entry.get("interaction_data") or {}
            # use interaction_data; properties.spell_check can disagree
            spell_check = bool(interaction_data.get("spell_check") or False)

        return cls(
            course_id=int(course_id),
            quiz_id=int(quiz_id),
            engine=engine,
            item_id=int(data.get("id") or 0),          # item id comes as a string
            position=int(data.get("position") or 0),
            interaction_type_slug=slug,
            essay_spell_check_enabled=spell_check,
        )

    @classmethod
    def list_from_api(
        cls,
        course_id: int,
        quiz_id: int,
        engine: str,
        payload: Iterable[Dict[str, Any]],
    ) -> List["NewQuizItem"]:
        return [cls.from_api(course_id=course_id, quiz_id=quiz_id, engine=engine, data=item) for item in payload]