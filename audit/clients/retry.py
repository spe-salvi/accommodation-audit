"""
Async retry decorator with exponential backoff and jitter.

Designed for use with httpx-based API clients that may encounter
transient failures from rate limiting (429), server errors (5xx),
or network-level issues (timeouts, connection resets).

Backoff strategy
----------------
Full jitter exponential backoff — on each attempt, the delay is
chosen uniformly at random from [0, min(cap, base * 2^attempt)].

Full jitter is preferred over pure exponential backoff for API clients
because it spreads retry storms across time when multiple coroutines
hit a rate limit simultaneously, reducing thundering-herd effects.

Retry conditions
----------------
- HTTP 429 Too Many Requests (with optional Retry-After header support)
- HTTP 5xx Server Error (transient server-side failures)
- httpx.TransportError subclasses (timeout, connection reset, etc.)

HTTP 4xx errors other than 429 are NOT retried — they indicate a
client-side problem (bad request, unauthorized, not found) that
will not resolve on its own.

Usage
-----
    from audit.clients.retry import retryable

    class MyClient:
        @retryable
        async def _get(self, url: str) -> httpx.Response:
            response = await self._http.get(url)
            response.raise_for_status()
            return response

Configuration
-------------
Override defaults by passing arguments to the decorator:

    @retryable(max_attempts=5, base_delay=2.0, cap=30.0)
    async def _get(self, ...): ...
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
from typing import Any, Callable, TypeVar

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_MAX_ATTEMPTS = 5   # 1 original + 4 retries — Canvas 429s need room
_DEFAULT_BASE_DELAY = 1.0   # seconds — starting point for backoff
_DEFAULT_CAP = 15.0         # seconds — maximum delay between retries

# HTTP status codes that are safe to retry.
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

F = TypeVar("F", bound=Callable[..., Any])


# ---------------------------------------------------------------------------
# Public decorator
# ---------------------------------------------------------------------------

def retryable(
    func: F | None = None,
    *,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    base_delay: float = _DEFAULT_BASE_DELAY,
    cap: float = _DEFAULT_CAP,
) -> Any:
    """
    Decorator that retries an async function on transient HTTP failures.

    Can be used with or without arguments::

        @retryable
        async def my_func(): ...

        @retryable(max_attempts=5, cap=30.0)
        async def my_func(): ...

    Parameters
    ----------
    max_attempts:
        Total number of attempts (including the first). Defaults to 5.
    base_delay:
        Base delay in seconds for the backoff calculation. Defaults to 1.0.
    cap:
        Maximum delay in seconds between retries. Defaults to 15.0.
    """
    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None

            for attempt in range(max_attempts):
                try:
                    return await fn(*args, **kwargs)

                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code

                    if status not in _RETRYABLE_STATUS_CODES:
                        # Non-retryable client error (401, 403, 404, etc.)
                        raise

                    last_exc = exc
                    delay = _compute_delay(
                        attempt=attempt,
                        base=base_delay,
                        cap=cap,
                        retry_after=_parse_retry_after(exc.response),
                    )
                    logger.warning(
                        "HTTP %d on attempt %d/%d — retrying in %.1fs (%s %s)",
                        status,
                        attempt + 1,
                        max_attempts,
                        delay,
                        exc.request.method,
                        exc.request.url,
                    )

                except httpx.TransportError as exc:
                    # Covers: TimeoutException, ConnectError, ReadError, etc.
                    last_exc = exc
                    delay = _compute_delay(
                        attempt=attempt,
                        base=base_delay,
                        cap=cap,
                    )
                    logger.warning(
                        "Transport error on attempt %d/%d — retrying in %.1fs (%s)",
                        attempt + 1,
                        max_attempts,
                        delay,
                        exc,
                    )

                if attempt < max_attempts - 1:
                    await asyncio.sleep(delay)

            # All attempts exhausted — re-raise the last exception.
            raise last_exc  # type: ignore[misc]

        return wrapper  # type: ignore[return-value]

    # Support both @retryable and @retryable(...)
    if func is not None:
        return decorator(func)
    return decorator


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _compute_delay(
    *,
    attempt: int,
    base: float,
    cap: float,
    retry_after: float | None = None,
) -> float:
    """
    Compute the delay before the next retry.

    If the server supplied a Retry-After value, use that (clamped to cap).
    Otherwise use full-jitter exponential backoff:
        delay = random(0, min(cap, base * 2^attempt))
    """
    if retry_after is not None:
        return min(retry_after, cap)

    ceiling = min(cap, base * (2 ** attempt))
    return random.uniform(0, ceiling)


def _parse_retry_after(response: httpx.Response) -> float | None:
    """
    Parse the Retry-After header from a 429 response, if present.

    Canvas sends this as an integer number of seconds.
    Returns None if the header is absent or unparseable.
    """
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None
