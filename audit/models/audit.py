from dataclasses import dataclass, field
from typing import Optional, Dict
from audit.repos.base import AccommodationType


@dataclass(frozen=True, slots=True)
class AuditRow:
    course_id: int | None = None
    quiz_id: int | None = None
    user_id: int | None = None
    item_id: int | None = None
    engine: str | None = None
    accommodation_type: AccommodationType | None = None
    has_accommodation: bool = False
    details: Dict[str, object] = field(default_factory=dict)
    completed: Optional[bool] = None

@dataclass(frozen=True, slots=True)
class AuditRequest:
    # term_id: int | None = None
    course_id: int | None = None
    quiz_id: int | None = None
    # user_id: int | None = None
    # item_id: int | None = None
    engine: str | None = None
    accommodation_type: AccommodationType | None = None