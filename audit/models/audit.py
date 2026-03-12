from dataclasses import dataclass
from typing import Optional, Dict
from audit.repos.base import AccommodationType


@dataclass(frozen=True, slots=True)
class AuditRow:
    course_id: int
    quiz_id: int
    user_id: int
    item_id: int
    engine: str
    accommodation_type: AccommodationType
    has_accommodation: bool
    details: Dict[str, object]
    completed: Optional[bool]

@dataclass(frozen=True, slots=True)
class AuditRequest:
    term_id: int | None = None
    course_id: int | None = None
    quiz_id: int | None = None
    user_id: int | None = None
    item_id: int | None = None
    engine: str | None = None
    accommodation_type: AccommodationType | None = None