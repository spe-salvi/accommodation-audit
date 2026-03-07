from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse, parse_qs

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
            id=int(data["id"]),
            name=str(data.get("name") or ""),
            sis_term_id=data.get("sis_term_id"),
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
    def from_api(cls, data: dict) -> "Course":
        return cls(
            id=data["id"],
            name=data.get("name"),
            course_code=data.get("course_code"),
            sis_course_id=data.get("sis_course_id"),
        )

    @classmethod
    def list_from_api(cls, payload: Iterable[Dict[str, Any]]) -> List["Course"]:
        return [cls.from_api(item) for item in payload]
    

@dataclass(frozen=True)
class Quiz:
    course_id: int
    id: int
    name: str

    @property
    def key(self) -> int:
        return (self.course_id, self.id)

    @classmethod
    def from_api(cls, course_id: int, data: Dict[str, Any]) -> "Quiz":
        return cls(
            course_id=int(course_id),
            id=int(data["id"]),
            title=str(data.get("title") or ""),
        )

    @classmethod
    def list_from_api(
        cls,
        course_id: int,
        payload: Iterable[Dict[str, Any]],
    ) -> List["Quiz"]:
        return [cls.from_api(course_id, item) for item in payload]

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
            id=data["id"],
            sortable_name=data.get("sortable_name", ""),
            sis_user_id=data.get("sis_user_id"),
        )
    
    @classmethod
    def list_from_api(cls, payload: Iterable[Dict[str, Any]]) -> List["User"]:
        return [cls.from_api(item) for item in payload]


@dataclass(frozen=True, slots=True)
class Participant:
    course_id: int
    quiz_id: int
    user_id: int

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
    def from_api(cls, course_id: int, quiz_id: int, data: Dict[str, Any]) -> "Participant":
        enrollment = data.get("enrollment") or {}
        sessions = data.get("participant_sessions") or []

        first_session = sessions[0] if sessions else {}

        return cls(
            course_id=int(course_id),
            quiz_id=int(quiz_id),
            user_id=int(data["user_id"]),
            participant_id=int(data["id"]),
            extra_attempts=int(data.get("extra_attempts") or 0),

            timer_multiplier_enabled=bool(enrollment.get("timer_multiplier_enabled") or False),
            timer_multiplier_value=float(enrollment.get("timer_multiplier_value") or 0),

            extra_time_enabled=bool(enrollment.get("extra_time_enabled") or False),
            extra_time_in_seconds=int(enrollment.get("extra_time_in_seconds") or 0),

            participant_session_id=first_session.get("id"),
            quiz_session_id=first_session.get("quiz_api_quiz_session_id"),
        )

    @classmethod
    def list_from_api(
        cls,
        course_id: int,
        quiz_id: int,
        payload: Iterable[Dict[str, Any]],
    ) -> List["Participant"]:
        return [cls.from_api(course_id, quiz_id, item) for item in payload]
    
@dataclass(slots=True)
class Enrollment:
    user_id: int
    course_id: int

    @property
    def key(self) -> tuple[int, int]:
        return (self.user_id, self.course_id)

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> Optional["Enrollment"]:
        user_id = data.get("user_id")
        course_id = data.get("course_id")

        if user_id is None or course_id is None:
            return None

        return cls(
            user_id=int(user_id),
            course_id=int(course_id),
        )
    
    @classmethod
    def list_from_api(cls, payload: Iterable[Dict[str, Any]]) -> List["Enrollment"]:
        return [cls.from_api(item) for item in payload]


@dataclass(slots=True)
class Submission:
    user_id: int
    course_id: int
    quiz_id: int
    # extra_time: int
    extra_attempts: int
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
    def parse_new_quiz_session_ids(external_tool_url: str | None) -> tuple[str | None, str | None]:
        if not external_tool_url:
            return None, None

        parsed = urlparse(external_tool_url)
        query = parse_qs(parsed.query)

        participant_session_id = query.get("participant_session_id", [None])[0]
        quiz_session_id = query.get("quiz_session_id", [None])[0]

        return participant_session_id, quiz_session_id

    @classmethod
    def from_api(
        cls,
        course_id: int,
        quiz_id: int,
        data: Dict[str, Any],
    ) -> Optional["Submission"]:

        raw_uid = data.get("user_id")
        if raw_uid is None:
            return None

        try:
            uid = int(raw_uid)
        except (TypeError, ValueError):
            return None

        workflow = str(data.get("workflow_state") or "")

        # extra_time = int(data.get("extra_time") or 0)
        extra_attempts = int(data.get("extra_attempts") or 0)

        participant_session_id, quiz_session_id_value = cls.parse_new_quiz_session_ids(
            data.get("external_tool_url")
        )

        return cls(
            user_id=uid,
            course_id=int(course_id),
            quiz_id=int(quiz_id),
            # extra_time=extra_time,
            extra_attempts=extra_attempts,
            date=cls._workflow_to_date(workflow),
            participant_session_id=participant_session_id,
            quiz_session_id=quiz_session_id_value,
        )

@dataclass(slots=True)
class NewQuizItem:
    course_id: int
    quiz_id: int
    item_id: int
    position: int
    interaction_type_slug: str
    essay_spell_check_enabled: Optional[bool]

    @property
    def key(self) -> tuple[int, int, int]:
        # (course, quiz, item) uniquely identifies an item in your world
        return (self.course_id, self.quiz_id, self.item_id)

    @classmethod
    def from_api(cls, course_id: int, quiz_id: int, data: Dict[str, Any]) -> "NewQuizItem":
        entry = data.get("entry") or {}
        slug = str(entry.get("interaction_type_slug") or "")

        spell_check: Optional[bool] = None
        if slug == "essay":
            interaction_data = entry.get("interaction_data") or {}
            # use interaction_data; properties.spell_check can disagree
            spell_check = bool(interaction_data.get("spell_check", False))

        return cls(
            course_id=int(course_id),
            quiz_id=int(quiz_id),
            item_id=int(data["id"]),          # item id comes as a string
            position=int(data.get("position") or 0),
            interaction_type_slug=slug,
            essay_spell_check_enabled=spell_check,
        )

    @classmethod
    def list_from_api(
        cls,
        course_id: int,
        quiz_id: int,
        payload: Iterable[Dict[str, Any]],
    ) -> List["NewQuizItem"]:
        return [cls.from_api(course_id, quiz_id, item) for item in payload]