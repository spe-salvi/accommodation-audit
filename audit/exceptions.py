"""
Domain exception hierarchy for the accommodation audit system.

All exceptions raised by this application inherit from ``AuditError``,
making it easy for callers to catch all application errors with a
single ``except AuditError`` clause, or to handle specific failure
modes with more targeted catches.

Hierarchy
---------
AuditError
├── AuditConfigError          Missing or invalid configuration
├── CanvasApiError            HTTP failure from the Canvas REST API
│   └── RateLimitError        HTTP 429 — rate limit exceeded
└── LtiError                  Base for New Quiz LTI service errors
    ├── LtiSessionError       Token expired or Playwright login failed
    └── LtiIdNotFoundError    Canvas assignment ID not in LTI cache/session

Usage examples
--------------
Catch all application errors::

    try:
        rows = await svc.audit_quiz(...)
    except AuditError as exc:
        logger.error("Audit failed: %s", exc)

Handle rate limits specifically::

    try:
        data = await client.get_paginated_json(path)
    except RateLimitError as exc:
        logger.warning("Rate limited; retry after %.0fs", exc.retry_after or 0)

Handle LTI token expiry::

    try:
        participants = await nq_client.list_participants(...)
    except LtiSessionError:
        session = await acquire_lti_session(...)
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class AuditError(Exception):
    """
    Base class for all application-level errors.

    Catching ``AuditError`` is sufficient to handle any error raised
    deliberately by this application. Low-level library exceptions
    (e.g. ``httpx.TransportError`` after all retries are exhausted)
    are wrapped in a subclass before propagating to callers.
    """


# ---------------------------------------------------------------------------
# Configuration errors
# ---------------------------------------------------------------------------

class AuditConfigError(AuditError):
    """
    Raised when required configuration is missing or invalid.

    Typical causes:
        - A required environment variable is not set (e.g. CANVAS_TOKEN)
        - An environment variable contains an invalid value
        - Conflicting configuration options are supplied

    Example::

        raise AuditConfigError("Missing required environment variable: 'CANVAS_TOKEN'")
    """


# ---------------------------------------------------------------------------
# Canvas API errors
# ---------------------------------------------------------------------------

class CanvasApiError(AuditError):
    """
    Raised when a Canvas REST API call fails after all retries are exhausted.

    Wraps the underlying ``httpx.HTTPStatusError`` so callers do not
    need to depend on httpx directly.

    Attributes
    ----------
    status_code:
        The HTTP status code returned by Canvas.
    url:
        The URL that was requested.
    """

    def __init__(self, message: str, *, status_code: int, url: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.url = url

    def __str__(self) -> str:
        return f"{super().__str__()} (HTTP {self.status_code} — {self.url})"


class RateLimitError(CanvasApiError):
    """
    Raised when Canvas returns HTTP 429 Too Many Requests and all
    retry attempts have been exhausted.

    Attributes
    ----------
    retry_after:
        The number of seconds Canvas requested we wait before retrying,
        parsed from the ``Retry-After`` header. None if the header was
        absent.
    """

    def __init__(
        self,
        message: str,
        *,
        url: str,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, status_code=429, url=url)
        self.retry_after = retry_after

    def __str__(self) -> str:
        base = super().__str__()
        if self.retry_after is not None:
            return f"{base} — retry after {self.retry_after:.0f}s"
        return base


# ---------------------------------------------------------------------------
# LTI errors
# ---------------------------------------------------------------------------

class LtiError(AuditError):
    """Base class for errors related to the New Quiz LTI service."""


class LtiSessionError(LtiError):
    """
    Raised when the LTI session cannot be established or has expired.

    Typical causes:
        - Playwright failed to log in to Canvas
        - The LTI launch handshake did not return an access token
        - The LTI service returned HTTP 401 (token expired mid-audit)

    Recovery:
        Call ``acquire_lti_session()`` to obtain a fresh session.
    """


class LtiIdNotFoundError(LtiError):
    """
    Raised when a Canvas assignment ID has no corresponding LTI
    assignment ID in the current session or persistent cache.

    This means the assignment was not included in the Playwright
    discovery run that built the current ``LtiSession``.

    Attributes
    ----------
    canvas_assignment_id:
        The Canvas assignment ID that could not be resolved.
    """

    def __init__(self, canvas_assignment_id: int) -> None:
        super().__init__(
            f"No LTI assignment ID found for canvas_assignment_id="
            f"{canvas_assignment_id}. "
            f"Ensure this assignment was included in the Playwright "
            f"discovery run."
        )
        self.canvas_assignment_id = canvas_assignment_id
