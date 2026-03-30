"""
HTTP client for the New Quiz LTI service.

Handles communication with franciscan.quiz-lti-pdx-prod.instructure.com.
Requires an LtiSession obtained via audit.clients.session.acquire_lti_session().

Exceptions raised
-----------------
LtiIdNotFoundError    Canvas assignment ID not in session/cache
LtiSessionError       HTTP 401 — token expired, re-acquire session
RateLimitError        HTTP 429 after all retries exhausted
CanvasApiError        Other non-2xx HTTP responses after retries
"""

from __future__ import annotations

import logging

import httpx

from audit.clients.retry import retryable
from audit.clients.session import LtiSession
from audit.exceptions import (
    AuditError,
    CanvasApiError,
    LtiIdNotFoundError,
    LtiSessionError,
    RateLimitError,
)

logger = logging.getLogger(__name__)

_LTI_BASE_URL = "https://franciscan.quiz-lti-pdx-prod.instructure.com"


class NewQuizClient:
    """
    Async HTTP client for the New Quiz LTI service.

    Usage
    -----
        async with httpx.AsyncClient() as http:
            client = NewQuizClient(session=lti_session, http=http)
            participants = await client.list_participants(
                canvas_assignment_id=189437
            )
    """

    def __init__(self, *, session: LtiSession, http: httpx.AsyncClient) -> None:
        self._session = session
        self._http = http

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def list_participants(self, *, canvas_assignment_id: int) -> list[dict]:
        """
        Fetch all participants for a New Quiz assignment.

        Parameters
        ----------
        canvas_assignment_id:
            The Canvas assignment ID (e.g. 189437). The LTI assignment ID
            is looked up from the session's lti_id_by_canvas_id map.

        Returns
        -------
        List of raw participant dicts from the LTI service.

        Raises
        ------
        LtiIdNotFoundError
            Assignment was not included in the Playwright discovery run.
        LtiSessionError
            The LTI token has expired (HTTP 401).
        RateLimitError
            HTTP 429 after all retries exhausted.
        CanvasApiError
            Other non-2xx response after retries.
        """
        lti_assignment_id = self._session.lti_id_by_canvas_id.get(canvas_assignment_id)
        if lti_assignment_id is None:
            raise LtiIdNotFoundError(canvas_assignment_id)

        path = f"/api/assignments/{lti_assignment_id}/participants"
        logger.debug(
            "NewQuizClient: fetching participants for canvas_assignment_id=%d "
            "(lti_assignment_id=%d)",
            canvas_assignment_id,
            lti_assignment_id,
        )
        return await self._get_paginated(path)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _get_paginated(self, path: str) -> list:
        """Fetch all pages from a paginated LTI service endpoint."""
        results: list = []
        url: str | None = f"{_LTI_BASE_URL}{path}"

        while url:
            response = await self._fetch(url)
            data = response.json()

            if isinstance(data, list):
                results.extend(data)
            elif isinstance(data, dict):
                for value in data.values():
                    if isinstance(value, list):
                        results.extend(value)
                        break

            url = self._next_link(response.headers.get("link", ""))

        logger.debug("NewQuizClient: %s → %d records total", path, len(results))
        return results

    @retryable
    async def _fetch(self, url: str) -> httpx.Response:
        """
        Issue a single GET request to the LTI service.

        Note: HTTP 401 is NOT retried — it signals token expiry and
        requires a new LtiSession. It is raised as LtiSessionError
        so the caller can re-acquire the session and retry the operation.
        """
        try:
            headers = {"Authorization": self._session.auth_header}
            response = await self._http.get(url, headers=headers)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401:
                raise LtiSessionError(
                    "LTI token has expired or is invalid. "
                    "Re-acquire the session via acquire_lti_session()."
                ) from exc
            if status == 429:
                retry_after_raw = exc.response.headers.get("retry-after")
                retry_after = float(retry_after_raw) if retry_after_raw else None
                raise RateLimitError(
                    "LTI service rate limit exceeded",
                    url=url,
                    retry_after=retry_after,
                ) from exc
            raise CanvasApiError(
                "LTI service request failed",
                status_code=status,
                url=url,
            ) from exc
        except httpx.TransportError as exc:
            raise AuditError(
                f"Network error reaching LTI service: {exc}"
            ) from exc

    @staticmethod
    def _next_link(link_header: str) -> str | None:
        """Parse the 'next' URL from an RFC 5988 Link header, or None."""
        if not link_header:
            return None
        for part in link_header.split(","):
            segments = part.strip().split(";")
            if len(segments) < 2:
                continue
            url_part = segments[0].strip().strip("<>")
            for attr in segments[1:]:
                if attr.strip() == 'rel="next"':
                    return url_part
        return None
