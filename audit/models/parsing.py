"""
Safe type coercion and validation for Canvas API payloads.

Canvas API responses are inconsistent — IDs may arrive as strings or
integers, optional fields may be null, empty strings, or missing
entirely. This module provides parsing helpers that absorb those
inconsistencies so that model constructors can trust their inputs.

All functions in this module are pure (no I/O, no side effects) and
are designed to fail gracefully by returning a default rather than
raising, unless the caller explicitly needs validation errors.
"""

from typing import Any, Literal

QuizEngine = Literal["classic", "new"]
"""The two Canvas quiz engine types that this system supports."""
<<<<<<< HEAD

=======
>>>>>>> fb079c2a69e95c5965c6116b2ebe628e50ca8d04


"""
Coerce a value to int, returning *default* on failure.

Handles the common Canvas patterns of null, empty string, string
integers ("42"), and actual ints. Returns *default* for anything
that cannot be cleanly converted.
"""
def parse_int(value: Any, default=None) -> int | None:
    """
    Coerce a value to int, returning *default* on failure.

    Handles the common Canvas patterns of null, empty string, string
    integers ("42"), and actual ints. Returns *default* for anything
    that cannot be cleanly converted.
    """
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

<<<<<<< HEAD

=======
"""
Coerce a value to str, returning *default* for None.

Canvas occasionally sends null for optional string fields; this
normalizes them to the caller's preferred default.
"""
>>>>>>> fb079c2a69e95c5965c6116b2ebe628e50ca8d04
def parse_str(value: Any, default: str = "") -> str:
    """
    Coerce a value to str, returning *default* for None.

    Canvas occasionally sends null for optional string fields; this
    normalizes them to the caller's preferred default.
    """
    if value is None:
        return default
    return str(value)

<<<<<<< HEAD

=======
"""
Parse *raw_value* as an int and return it only if it matches *expected*.

Useful for verifying that a payload's embedded ID matches the ID
the caller requested. Returns None on mismatch or parse failure.
"""
>>>>>>> fb079c2a69e95c5965c6116b2ebe628e50ca8d04
def validate_expected_id(raw_value: Any, expected: int) -> int | None:
    """
    Parse *raw_value* as an int and return it only if it matches *expected*.

    Useful for verifying that a payload's embedded ID matches the ID
    the caller requested. Returns None on mismatch or parse failure.
    """
    parsed = parse_int(raw_value)
    return parsed if parsed == expected else None

<<<<<<< HEAD

=======
"""
Extract the quiz identifier from a submission payload.

Classic submissions store this in ``quiz_id``; new-engine submissions
use ``assignment_id`` (since new quizzes are backed by assignments).
"""
>>>>>>> fb079c2a69e95c5965c6116b2ebe628e50ca8d04
def parse_quiz_id_from_submission(data: dict, engine: str) -> int | None:
    """
    Extract the quiz identifier from a submission payload.

    Classic submissions store this in ``quiz_id``; new-engine submissions
    use ``assignment_id`` (since new quizzes are backed by assignments).
    """
    if engine == "classic":
        return parse_int(data.get("quiz_id"))
    return parse_int(data.get("assignment_id"))

<<<<<<< HEAD

=======
"""
Extract the submission's own ID from a payload.

Classic submissions use ``submission_id``; new-engine submissions
use the top-level ``id`` field.
"""
>>>>>>> fb079c2a69e95c5965c6116b2ebe628e50ca8d04
def parse_submission_id(data: dict, engine: str) -> int | None:
    """
    Extract the submission's own ID from a payload.

    Classic submissions use ``submission_id``; new-engine submissions
    use the top-level ``id`` field.
    """
    if engine == "classic":
        return parse_int(data.get("submission_id"))
    return parse_int(data.get("id"))

<<<<<<< HEAD

=======
"""
Validate that *engine* is a recognized quiz engine string.

Raises:
    ValueError: If *engine* is not ``"classic"`` or ``"new"``.
"""
>>>>>>> fb079c2a69e95c5965c6116b2ebe628e50ca8d04
def validate_engine_value(engine: str) -> QuizEngine:
    """
    Validate that *engine* is a recognized quiz engine string.

    Raises:
        ValueError: If *engine* is not ``"classic"`` or ``"new"``.
    """
    if engine not in ("classic", "new"):
        raise ValueError(f"Invalid engine: {engine!r}. Expected 'classic' or 'new'.")
    return engine


"""
Validate that a submission payload has the minimum required fields
for the declared engine type.

Classic submissions must contain ``quiz_id``; new-engine submissions
must contain ``assignment_id``. This catches data/engine mismatches
early, before they propagate into the model layer.

Raises:
    ValueError: If a required field is missing or unparseable.
"""
def validate_payload_for_engine(data: dict, engine: QuizEngine) -> None:
<<<<<<< HEAD
    """
    Validate that a submission payload has the minimum required fields
    for the declared engine type.

    Classic submissions must contain ``quiz_id``; new-engine submissions
    must contain ``assignment_id``. This catches data/engine mismatches
    early, before they propagate into the model layer.

    Raises:
        ValueError: If a required field is missing or unparseable.
    """
=======
>>>>>>> fb079c2a69e95c5965c6116b2ebe628e50ca8d04
    if engine == "classic":
        if parse_int(data.get("quiz_id")) is None:
            raise ValueError("Classic submission payload missing 'quiz_id'.")
        return

    if engine == "new":
        if parse_int(data.get("assignment_id")) is None:
            raise ValueError("New quiz submission payload missing 'assignment_id'.")
        return

    raise ValueError(f"Unsupported engine: {engine!r}")
