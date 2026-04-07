"""
Canvas LMS async HTTP client.

Provides authenticated access to the Canvas REST API with automatic
pagination handling (RFC 5988 Link headers), response unwrapping,
retry on transient failures, and optional in-memory response caching.

This module owns transport concerns only. Domain exceptions from
``audit.exceptions`` are raised so callers never need to import httpx.

Metrics
-------
``requests_made`` counts actual HTTP requests (cache misses only).
``retries_fired`` counts how many retry attempts were triggered across
all requests in this client's lifetime. Both are queryable at any time
and used by ``audit.metrics.collect_metrics()`` at run end.

Caching
-------
An optional ``RequestCache`` can be injected at construction. When
present, successful responses are stored by (url, params) and served
from cache on subsequent identical requests.

Retry behaviour
---------------
Each individual HTTP request is retried automatically on transient
failures (429, 5xx, network errors) using exponential backoff with
full jitter. Cache hits bypass the network entirely and are never
retried.

Exceptions raised
-----------------
RateLimitError    HTTP 429 after all retries exhausted
CanvasApiError    Any other non-2xx HTTP response after retries
AuditError        Wraps unexpected transport-level failures
"""
from __future__ import annotations

import logging

import httpx

from audit.cache.runtime import RequestCache
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
      - Optional response caching via ``RequestCache``
      - Domain exception wrapping so callers stay httpx-free
      - ``requests_made`` and ``retries_fired`` counters for metrics

    Parameters
    ----------
    base_url:
        Canvas instance base URL.
    token:
        Canvas API Bearer token.
    http:
        Shared httpx async client.
    cache:
        Optional request cache. When provided, identical requests within
        a run are served from memory without hitting the network.
    """

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        http: httpx.AsyncClient,
        cache: RequestCache | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._http = http
        self._cache = cache
        # Metrics counters — queryable at run end via collect_metrics().
        self.requests_made: int = 0
        self.retries_fired: int = 0

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
        url = f"{self._base_url}{path}"

        if self._cache is not None:
            cached = self._cache.get(url, params)
            if cached is not None:
                return cached

        response = await self._fetch(url, params=params)
        data = response.json()

        if self._cache is not None:
            self._cache.set(url, params, data)

        return data

    async def get_paginated_json(
        self, path: str, *, params: dict | None = None
    ) -> list:
        """
        Fetch all pages for a paginated Canvas endpoint.

        The full result list (all pages combined) is cached as a single
        entry keyed by the first page URL + params. On a cache hit the
        entire result is returned immediately without any network calls.

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
        first_url = f"{self._base_url}{path}"

        # Check cache before making any network calls.
        if self._cache is not None:
            cached = self._cache.get(first_url, params)
            if cached is not None:
                return cached

        results: list = []
        url: str | None = first_url

        while url:
            response = await self._fetch(url, params=params)
            results.extend(self._unwrap(response.json()))
            params = None
            url = self._next_link(response.headers.get("link", ""))

        # Cache the full combined result under the first URL.
        if self._cache is not None:
            self._cache.set(first_url, params, results)

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

        Increments ``requests_made`` on every actual HTTP call.
        Increments ``retries_fired`` when a retryable error is caught —
        this fires before the delay, so it counts attempts not completions.

        Wraps httpx exceptions in domain exceptions after all retries
        are exhausted so callers never need to import httpx.
        """
        self.requests_made += 1
        logger.debug("GET %s (request #%d)", url, self.requests_made)
        try:
            response = await self._http.get(url, headers=self._headers, params=params)
            response.raise_for_status()
            logger.debug("GET %s → %d", url, response.status_code)
            return response
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 429:
                self.retries_fired += 1
                retry_after_raw = exc.response.headers.get("retry-after")
                retry_after = float(retry_after_raw) if retry_after_raw else None
                raise RateLimitError(
                    "Canvas rate limit exceeded",
                    url=url,
                    retry_after=retry_after,
                ) from exc
            if status in (500, 502, 503, 504):
                self.retries_fired += 1
            raise CanvasApiError(
                "Canvas API request failed",
                status_code=status,
                url=url,
            ) from exc
        except httpx.TransportError as exc:
            self.retries_fired += 1
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
