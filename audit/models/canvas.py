from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

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


@dataclass(frozen=True)
class Participant:
    course_id: int
    quiz_id: int
    user_id: int

    extra_attempts: int

    disable_timer: bool
    extra_time_enabled: bool
    extra_time_in_seconds: int

    timer_multiplier_enabled: bool
    timer_multiplier_value: float

    reduce_choices_enabled: bool

    @classmethod
    def from_api(cls, *, course_id: int, quiz_id: int, data: Dict[str, Any]) -> Optional["Participant"]:
        uid = data.get("user_id")
        if uid is None:
            return None

        enrollment = data.get("enrollment", {}) or {}
        return cls(
            course_id=int(course_id),
            quiz_id=int(quiz_id),
            user_id=int(uid),

            extra_attempts=int(data.get("extra_attempts") or 0),

            disable_timer=bool(enrollment.get("disable_timer") or False),
            extra_time_enabled=bool(enrollment.get("extra_time_enabled") or False),
            extra_time_in_seconds=int(enrollment.get("extra_time_in_seconds") or 0),

            timer_multiplier_enabled=bool(enrollment.get("timer_multiplier_enabled") or False),
            timer_multiplier_value=float(enrollment.get("timer_multiplier_value") or 1.0),

            reduce_choices_enabled=bool(enrollment.get("reduce_choices_enabled") or False),
        )
    
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

    @staticmethod
    def _workflow_to_date(workflow: str) -> str:
        if workflow in ("complete", "graded"):
            return "past"
        if workflow in ("settings_only", "unsubmitted"):
            return "future"
        return ""

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

        return cls(
            user_id=uid,
            course_id=int(course_id),
            quiz_id=int(quiz_id),
            # extra_time=extra_time,
            extra_attempts=extra_attempts,
            date=cls._workflow_to_date(workflow),
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