"""
Audit-specific data models.

These dataclasses represent the inputs and outputs of the audit
process itself, as opposed to Canvas domain entities.

``AuditRequest`` captures what the caller wants to audit (which term,
course, quiz, user, engine, and accommodation type).

``AuditRow`` is a single line in the audit output — one per
user-accommodation or item-accommodation combination.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict
from audit.repos.base import AccommodationType


"""
A request to audit a specific accommodation on a specific quiz.

Captures the full context the service layer needs to perform a
single-quiz audit: which course, quiz, engine, and accommodation type.
"""
@dataclass(frozen=True, slots=True)
class AuditRequest:
    course_id: int
    quiz_id: int
    engine: str
    accommodation_type: AccommodationType


"""
A single audit result.

Each row represents one of two shapes:
    - **Per-user:** Indicates whether a specific student has a specific
    accommodation (extra time, extra attempts) on a given quiz.
    ``user_id`` is set; ``item_id`` is None.
    - **Per-item:** Indicates whether a specific quiz question has a
    configuration-level accommodation (spell-check). ``item_id`` is
    set; ``user_id`` is None.

The ``details`` dict carries accommodation-specific data (e.g.,
``{"extra_time_in_seconds": 600}``) for downstream reporting.
"""
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