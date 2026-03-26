"""
Canvas LMS async HTTP client.

Provides authenticated access to the Canvas REST API with automatic
pagination handling (RFC 5988 Link headers) and response unwrapping.

This module owns transport concerns only — authentication, pagination,
and response shape normalization. It does not contain any domain logic
or model parsing; that responsibility belongs to the repository layer.

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

import httpx


class CanvasClient:
    """
    Thin async HTTP client for the Canvas REST API.

    Owns:
      - Auth headers (Bearer token)
      - Single-object GET  (get_json)
      - Paginated GET      (get_paginated_json) via Link-header traversal

    The caller is responsible for creating and closing the httpx.AsyncClient,
    which makes the client easy to inject in tests and easy to share across
    requests in production.

    Design note:
        Accepting httpx.AsyncClient via dependency injection (rather than
        creating one internally) keeps this class testable — tests can
        pass a mock transport without hitting the network.
    """

    def __init__(self, *, base_url: str, headers: dict[str, str], http: httpx.AsyncClient) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = headers
        self._http = http

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    """
    Fetch a single JSON object from a Canvas endpoint.

    Use this for endpoints that return a single resource (e.g., a
    specific course or quiz). 

    Raises:
        httpx.HTTPStatusError: On any non-2xx response.
    """
    async def get_json(self, path: str, *, params: dict | None = None) -> dict:
        response = await self._get(path, params=params)
        return response.json()

    """
    Fetch all pages for a paginated Canvas endpoint.

    Canvas uses RFC 5988 Link headers for pagination. This method
    follows the ``rel="next"`` link until no more pages remain,
    collecting all results into a single flat list.

    Canvas responses come in two shapes:
        - A bare JSON array (most endpoints)
        - A wrapped dict with one list-valued key,
        e.g. ``{"quiz_submissions": [...]}`` (classic quiz submissions)

    Both shapes are unwrapped transparently — the caller always
    receives a flat ``list[dict]``.

    Note:
        Query params are only sent with the first request. Subsequent
        page URLs are fully-formed by Canvas and must not have params
        appended again.

    Raises:
        httpx.HTTPStatusError: On any non-2xx response during traversal.
    """
    async def get_paginated_json(
        self, path: str, *, params: dict | None = None
    ) -> list:
        results: list = []
        url: str | None = f"{self._base_url}{path}"

        while url:
            response = await self._http.get(url, headers=self._headers, params=params)
            response.raise_for_status()

            data = response.json()
            results.extend(self._unwrap(data))

            # Only the first request uses caller-supplied params; Canvas
            # bakes pagination state into subsequent URLs.
            params = None
            url = self._next_link(response.headers.get("link", ""))

        return results

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    """Issue a single GET request and raise on non-2xx."""
    async def _get(self, path: str, *, params: dict | None = None) -> httpx.Response:
        url = f"{self._base_url}{path}"
        response = await self._http.get(url, headers=self._headers, params=params)
        response.raise_for_status()
        return response

    """
    Normalize a Canvas response into a plain list.

    Canvas wraps some endpoints in a dict with a single list-valued
    key (e.g. ``{"quiz_submissions": [...]}``) while others return
    a bare list. This method handles both so upstream code never
    needs to check.
    """
    @staticmethod
    def _unwrap(data: list | dict) -> list:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for value in data.values():
                if isinstance(value, list):
                    return value
        return []

    @staticmethod
    def _next_link(link_header: str) -> str | None:
        """
        Extract the 'next' URL from an RFC 5988 Link header.

        Returns None if there is no next page, signaling the end of
        pagination.

        Example header value::

            <https://canvas.example.com/api/v1/courses?page=2>; rel="next",
            <https://canvas.example.com/api/v1/courses?page=5>; rel="last"
        """
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
