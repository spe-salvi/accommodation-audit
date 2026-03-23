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

    Typical production use:
        async with httpx.AsyncClient() as http:
            client = CanvasClient(
                base_url=settings.canvas_base_url,
                token=settings.canvas_token,
                http=http,
            )
            data = await client.get_paginated_json("/api/v1/courses")
    """

    def __init__(self, *, base_url: str, token: str, http: httpx.AsyncClient) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._http = http

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def get_json(self, path: str, *, params: dict | None = None) -> dict:
        """Fetch a single JSON object. Raises on non-2xx."""
        response = await self._get(path, params=params)
        return response.json()

    async def get_paginated_json(
        self, path: str, *, params: dict | None = None
    ) -> list:
        """
        Fetch all pages for a paginated endpoint, following Link headers.

        Canvas always returns either:
          - a JSON array  (most endpoints)
          - a wrapped dict, e.g. {"quiz_submissions": [...]}  (classic quiz submissions)

        Both shapes are unwrapped into a flat list before returning.
        The caller never sees pagination or wrapping.
        """
        results: list = []
        url: str | None = f"{self._base_url}{path}"

        while url:
            response = await self._http.get(url, headers=self._headers, params=params)
            response.raise_for_status()

            data = response.json()
            results.extend(self._unwrap(data))

            # params only apply to the first request; subsequent URLs are
            # fully-formed by Canvas and must not have params appended again.
            params = None
            url = self._next_link(response.headers.get("link", ""))

        return results

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _get(self, path: str, *, params: dict | None = None) -> httpx.Response:
        url = f"{self._base_url}{path}"
        response = await self._http.get(url, headers=self._headers, params=params)
        response.raise_for_status()
        return response

    @staticmethod
    def _unwrap(data: list | dict) -> list:
        """
        Return the list payload regardless of whether Canvas wrapped it.
        For dicts, we take the first list-valued key (Canvas only ever wraps
        one collection per response, e.g. "quiz_submissions").
        """
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
        Parse the RFC 5988 Link header and return the 'next' URL, or None.

        Example header value:
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
