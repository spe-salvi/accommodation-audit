"""
Canvas LMS async HTTP client.

Provides authenticated access to the Canvas REST API with automatic
pagination handling (RFC 5988 Link headers), response unwrapping,
and retry on transient failures.

This module owns transport concerns only. Domain exceptions from
``audit.exceptions`` are raised so callers never need to import httpx.

Retry behaviour
---------------
Each individual HTTP request is retried automatically on transient
failures (429, 5xx, network errors) using exponential backoff with
full jitter. Retries are applied per-request so that only the failing
page of a paginated sequence is retried, not the whole collection.

Exceptions raised
-----------------
RateLimitError    HTTP 429 after all retries exhausted
CanvasApiError    Any other non-2xx HTTP response after retries
AuditError        Wraps unexpected transport-level failures

Usage:
    async with httpx.AsyncClient() as http:
        client = CanvasClient(
            base_url="https://canvas.university.edu",
            token="your_api_token",
            http=http,
        )
        courses = await client.get_paginated_json("/api/v1/courses")
"""
from __future__ import annotations

import logging

import httpx

from audit.clients.retry import retryable
from audit.exceptions import AuditError, CanvasApiError, RateLimitError

logger = logging.getLogger(__name__)


class CanvasClient:
    """
    Thin async HTTP client for the Canvas REST API.

    Owns:
      - Auth headers (Bearer token)
      - Single-object GET  (get_json)
      - Paginated GET      (get_paginated_json) via Link-header traversal
      - Automatic retry on transient failures via ``@retryable``
      - Domain exception wrapping so callers stay httpx-free
    """

    def __init__(self, *, base_url: str, token: str, http: httpx.AsyncClient) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._http = http

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def get_json(self, path: str, *, params: dict | None = None) -> dict:
        """
        Fetch a single JSON object from a Canvas endpoint.

        Raises
        ------
        RateLimitError
            HTTP 429 after all retries exhausted.
        CanvasApiError
            Any other non-2xx response after retries.
        """
        response = await self._get(path, params=params)
        return response.json()

    async def get_paginated_json(
        self, path: str, *, params: dict | None = None
    ) -> list:
        """
        Fetch all pages for a paginated Canvas endpoint.

        Follows RFC 5988 Link headers until no next page remains.
        Each page request is independently retried on transient failures.
        Both bare-array and wrapped-dict response shapes are normalised
        into a flat list before returning.

        Raises
        ------
        RateLimitError
            HTTP 429 after all retries exhausted on any page.
        CanvasApiError
            Any other non-2xx response after retries.
        """
        results: list = []
        url: str | None = f"{self._base_url}{path}"

        while url:
            response = await self._fetch(url, params=params)
            results.extend(self._unwrap(response.json()))
            params = None
            url = self._next_link(response.headers.get("link", ""))

        return results

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _get(self, path: str, *, params: dict | None = None) -> httpx.Response:
        """Issue a single GET request by path."""
        url = f"{self._base_url}{path}"
        return await self._fetch(url, params=params)

    @retryable
    async def _fetch(
        self, url: str, *, params: dict | None = None
    ) -> httpx.Response:
        """
        Issue a single GET request by full URL with retry on transient failures.

        Wraps httpx exceptions in domain exceptions after all retries
        are exhausted so callers never need to import httpx.
        """
        try:
            response = await self._http.get(url, headers=self._headers, params=params)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 429:
                retry_after_raw = exc.response.headers.get("retry-after")
                retry_after = float(retry_after_raw) if retry_after_raw else None
                raise RateLimitError(
                    f"Canvas rate limit exceeded",
                    url=url,
                    retry_after=retry_after,
                ) from exc
            raise CanvasApiError(
                f"Canvas API request failed",
                status_code=status,
                url=url,
            ) from exc
        except httpx.TransportError as exc:
            raise AuditError(
                f"Network error reaching Canvas: {exc}"
            ) from exc

    @staticmethod
    def _unwrap(data: list | dict) -> list:
        """Normalise a Canvas response to a flat list."""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for value in data.values():
                if isinstance(value, list):
                    return value
        return []

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
