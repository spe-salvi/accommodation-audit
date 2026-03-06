from dataclasses import dataclass
from typing import Optional, Dict
from audit.repos.base import AccommodationType


@dataclass(frozen=True, slots=True)
class AuditRow:
    course_id: int
    quiz_id: int
    user_id: int
    accommodation_type: AccommodationType

    has_accommodation: bool
    completed: Optional[bool]

    reason: str
    details: Dict