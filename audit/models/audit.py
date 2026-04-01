"""
Audit-specific data models.

These dataclasses represent the inputs and outputs of the audit
process itself, as opposed to Canvas domain entities.

``AuditRequest`` captures what the caller wants to audit (which term,
course, quiz, user, engine, and accommodation type).

``AuditRow`` is a single line in the audit output — one per
user-accommodation or item-accommodation combination.

Enrichment fields
-----------------
Bucket 1 fields (course_name, quiz_title, enrollment_term_id, etc.)
are populated by the service layer at row-construction time using
Course and Quiz objects already loaded during the audit. No additional
API calls are needed.

Bucket 2 fields (term_name, user_name, sis_user_id) are populated by
the Enricher class after the audit completes. term_name uses the
terms list cache (1-year TTL). user_name and sis_user_id are batched
by unique user_id and fetched in parallel with a 1-year cache.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict
from audit.repos.base import AccommodationType


@dataclass(frozen=True, slots=True)
class AuditRequest:
    """
    A request to audit a specific accommodation on a specific quiz.

    Captures the full context the service layer needs to perform a
    single-quiz audit: which course, quiz, engine, and accommodation type.
    """
    course_id: int
    quiz_id: int
    engine: str
    accommodation_type: AccommodationType


@dataclass(frozen=True, slots=True)
class AuditRow:
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

    # --- Identity ---
    course_id: int | None = None
    quiz_id: int | None = None
    user_id: int | None = None
    item_id: int | None = None
    engine: str | None = None
    accommodation_type: AccommodationType | None = None

    # --- Audit result ---
    has_accommodation: bool = False
    details: Dict[str, object] = field(default_factory=dict)
    completed: Optional[bool] = None
    attempts_left: int | None = None

    # --- Term context ---
    # enrollment_term_id: Bucket 1 (free from Course object)
    # term_name:          Bucket 2 (Enricher — terms list cache, 1-year TTL)
    enrollment_term_id: int | None = None
    term_name: str | None = None

    # --- Course context (Bucket 1 — from Course model, no extra API calls) ---
    course_name: str | None = None
    course_code: str | None = None
    sis_course_id: str | None = None

    # --- Quiz context (Bucket 1 — from Quiz model, no extra API calls) ---
    quiz_title: str | None = None
    quiz_due_at: str | None = None
    quiz_lock_at: str | None = None

    # --- User context (Bucket 2 — Enricher, batched parallel fetches, 1-year TTL) ---
    user_name: str | None = None
    sis_user_id: str | None = None
