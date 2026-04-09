"""
Pydantic models for the FastAPI layer.

These are the API contract types — separate from the internal audit
domain models to keep the HTTP layer decoupled from business logic.
"""

from __future__ import annotations

from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class EngineChoice(str, Enum):
    new     = "new"
    classic = "classic"
    all     = "all"


class AccommodationTypeChoice(str, Enum):
    extra_time    = "extra_time"
    extra_attempt = "extra_attempt"
    spell_check   = "spell_check"


class AuditRequest(BaseModel):
    """Scope parameters for a new audit run."""
    term:   str | None = Field(None, description="Term ID or name")
    course: str | None = Field(None, description="Course ID, name, code, or SIS ID")
    quiz:   str | None = Field(None, description="Quiz ID or title (requires course)")
    user:   str | None = Field(None, description="User ID, name, or SIS user ID")
    engine: EngineChoice = EngineChoice.all
    types:  list[AccommodationTypeChoice] = Field(
        default_factory=lambda: list(AccommodationTypeChoice),
        description="Accommodation types to evaluate",
    )


class JobCreated(BaseModel):
    job_id: str
    message: str = "Audit job started"


class JobStatus(str, Enum):
    pending   = "pending"
    running   = "running"
    complete  = "complete"
    error     = "error"


class AuditRowResponse(BaseModel):
    """A single row in the audit output, safe for JSON serialisation."""
    course_id:           int | None
    quiz_id:             int | None
    user_id:             int | None
    item_id:             int | None
    engine:              str | None
    accommodation_type:  str | None
    has_accommodation:   bool
    details:             dict[str, Any]
    completed:           bool | None
    attempts_left:       int | None
    enrollment_term_id:  int | None
    term_name:           str | None
    course_name:         str | None
    course_code:         str | None
    sis_course_id:       str | None
    quiz_title:          str | None
    quiz_due_at:         str | None
    quiz_lock_at:        str | None
    user_name:           str | None
    sis_user_id:         str | None


class CacheEntityStats(BaseModel):
    total:    int
    valid:    int
    expired:  int
    ttl_hours: float


class CacheStatsResponse(BaseModel):
    stats: dict[str, CacheEntityStats]


class InvalidateCacheResponse(BaseModel):
    entity:  str
    count:   int
    message: str
