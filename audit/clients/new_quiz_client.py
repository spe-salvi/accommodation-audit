"""
HTTP client for the New Quiz LTI service.

Handles communication with franciscan.quiz-lti-pdx-prod.instructure.com.
Requires an LtiSession obtained via audit.clients.session.acquire_lti_session().

Token refresh
-------------
If the LTI service returns HTTP 401 (token expired), the client
automatically re-acquires a fresh session via Playwright and retries
the request once. If the refreshed token also returns 401, LtiSessionError
is raised and propagates to the caller.

This means a mid-audit token expiry is handled transparently — callers
only see a failure if the browser login itself fails or the service
is genuinely rejecting the credentials.

Exceptions raised
-----------------
LtiIdNotFoundError    Canvas assignment ID not in session/cache
LtiSessionError       Token refresh failed or second 401 received
RateLimitError        HTTP 429 after all retries exhausted
CanvasApiError        Other non-2xx HTTP responses after retries
"""

from __future__ import annotations

import logging

import httpx

from audit.cache.lti_id_cache import LtiIdCache
from audit.clients.retry import retryable
from audit.clients.session import LtiSession, acquire_lti_session
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

    Transparently refreshes the LTI session on 401 and retries once.

    Parameters
    ----------
    session:
        Initial LTI session. Replaced automatically on token expiry.
    http:
        Shared httpx async client.
    canvas_base_url:
        Canvas instance URL, used during session refresh to navigate
        to assignment pages and extract a new token.
    course_id:
        Canvas course ID, used during session refresh.
    lti_id_cache:
        Persistent LTI ID cache, passed through to acquire_lti_session()
        during refresh so already-discovered IDs are not re-discovered.

    Usage
    -----
        async with httpx.AsyncClient() as http:
            client = NewQuizClient(
                session=lti_session,
                http=http,
                canvas_base_url="https://franciscan.instructure.com",
                course_id=12977,
                lti_id_cache=cache,
            )
            participants = await client.list_participants(
                canvas_assignment_id=189437
            )
    """

    def __init__(
        self,
        *,
        session: LtiSession,
        http: httpx.AsyncClient,
        canvas_base_url: str,
        course_id: int,
        lti_id_cache: LtiIdCache,
    ) -> None:
        self._session = session
        self._http = http
        self._canvas_base_url = canvas_base_url
        self._course_id = course_id
        self._lti_id_cache = lti_id_cache

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
            Token refresh failed, or second 401 received after refresh.
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
    async def _fetch(self, url: str, *, _refreshed: bool = False) -> httpx.Response:
        """
        Issue a single GET request to the LTI service.

        On HTTP 401, refreshes the session via Playwright and retries
        once. If the refreshed token also returns 401, raises
        LtiSessionError hard — something is genuinely wrong.

        The ``_refreshed`` flag prevents infinite refresh loops: it is
        set to True on the recursive retry call so a second 401 raises
        immediately rather than triggering another browser session.

        Note: 401 is excluded from ``@retryable`` retry logic — it is
        handled here explicitly because it requires a session swap, not
        just a wait-and-retry.
        """
        try:
            headers = {"Authorization": self._session.auth_header}
            response = await self._http.get(url, headers=headers)
            response.raise_for_status()
            return response

        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code

            if status == 401:
                if _refreshed:
                    # Second 401 after a fresh token — give up.
                    raise LtiSessionError(
                        "LTI token refresh failed: received 401 again after "
                        "re-acquiring session. Check Canvas login credentials."
                    ) from exc

                # First 401 — try to refresh the session.
                logger.warning(
                    "NewQuizClient: 401 received, refreshing LTI session (url=%s)",
                    url,
                )
                await self._refresh_session()
                logger.info("NewQuizClient: session refreshed, retrying request")
                return await self._fetch(url, _refreshed=True)

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

    async def _refresh_session(self) -> None:
        """
        Re-acquire the LTI session via Playwright and swap self._session.

        Passes the canvas assignment IDs already known in the current
        session so all LTI IDs that were previously discovered are
        available in the refreshed session. Since they're all in the
        cache, Playwright only needs to load one page to get a fresh
        token — it won't re-discover IDs that are already cached.

        Raises
        ------
        LtiSessionError
            If Playwright fails to log in or cannot extract a new token.
        """
        canvas_assignment_ids = list(self._session.lti_id_by_canvas_id.keys())

        if not canvas_assignment_ids:
            raise LtiSessionError(
                "Cannot refresh LTI session: no assignment IDs in current session."
            )

        self._session = await acquire_lti_session(
            canvas_base_url=self._canvas_base_url,
            course_id=self._course_id,
            canvas_assignment_ids=canvas_assignment_ids,
            lti_id_cache=self._lti_id_cache,
        )

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
