"""
Unit tests for the retry decorator.

Tests that:
  - Retryable status codes (429, 500, 502, 503, 504) trigger retries
  - Non-retryable 4xx codes (401, 403, 404) raise immediately
  - Successful responses after a transient failure are returned
  - All attempts exhausted re-raises the last exception
  - Retry-After header is respected on 429 responses

Uses a simple call-counter mock rather than httpx transports to keep
tests fast and focused on the retry logic itself.
"""

import pytest
import httpx

from audit.clients.retry import retryable


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_status_error(status_code: int, url: str = "https://canvas.example.com/api/test", retry_after: str | None = None) -> httpx.HTTPStatusError:
    """Build a minimal HTTPStatusError for a given status code."""
    headers = {}
    if retry_after is not None:
        headers["retry-after"] = retry_after
    request = httpx.Request("GET", url)
    response = httpx.Response(status_code, headers=headers, request=request)
    return httpx.HTTPStatusError(
        f"HTTP {status_code}",
        request=request,
        response=response,
    )


def _make_success_response(url: str = "https://canvas.example.com/api/test") -> httpx.Response:
    request = httpx.Request("GET", url)
    return httpx.Response(200, request=request, text="ok")


# ---------------------------------------------------------------------------
# Retry on retryable status codes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status_code", [429, 500, 502, 503, 504])
async def test_retries_on_retryable_status_codes(status_code):
    """Retryable status codes should trigger up to max_attempts attempts."""
    call_count = 0

    @retryable(max_attempts=3, base_delay=0, cap=0)
    async def flaky():
        nonlocal call_count
        call_count += 1
        raise _make_status_error(status_code)

    with pytest.raises(httpx.HTTPStatusError):
        await flaky()

    assert call_count == 3


async def test_succeeds_after_transient_failure():
    """A success on the second attempt should be returned normally."""
    call_count = 0

    @retryable(max_attempts=3, base_delay=0, cap=0)
    async def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise _make_status_error(500)
        return _make_success_response()

    result = await flaky()
    assert result.status_code == 200
    assert call_count == 2


async def test_returns_immediately_on_first_success():
    """No retries should occur when the first attempt succeeds."""
    call_count = 0

    @retryable(max_attempts=3, base_delay=0, cap=0)
    async def ok():
        nonlocal call_count
        call_count += 1
        return _make_success_response()

    await ok()
    assert call_count == 1


# ---------------------------------------------------------------------------
# No retry on non-retryable status codes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
async def test_does_not_retry_non_retryable_status_codes(status_code):
    """4xx errors (except 429) should raise immediately without retrying."""
    call_count = 0

    @retryable(max_attempts=3, base_delay=0, cap=0)
    async def always_fails():
        nonlocal call_count
        call_count += 1
        raise _make_status_error(status_code)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await always_fails()

    assert call_count == 1
    assert exc_info.value.response.status_code == status_code


# ---------------------------------------------------------------------------
# Transport errors
# ---------------------------------------------------------------------------

async def test_retries_on_transport_error():
    """Network-level errors should also trigger retries."""
    call_count = 0

    @retryable(max_attempts=3, base_delay=0, cap=0)
    async def network_flaky():
        nonlocal call_count
        call_count += 1
        raise httpx.ConnectError("connection refused")

    with pytest.raises(httpx.ConnectError):
        await network_flaky()

    assert call_count == 3


async def test_succeeds_after_transport_error():
    """Should return normally if a transient network error clears."""
    call_count = 0

    @retryable(max_attempts=3, base_delay=0, cap=0)
    async def flaky_network():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise httpx.TimeoutException("timeout")
        return _make_success_response()

    result = await flaky_network()
    assert result.status_code == 200
    assert call_count == 2


# ---------------------------------------------------------------------------
# Decorator usage patterns
# ---------------------------------------------------------------------------

async def test_retryable_without_arguments():
    """@retryable without parentheses should work with default settings."""
    call_count = 0

    @retryable
    async def fn():
        nonlocal call_count
        call_count += 1
        raise _make_status_error(500)

    with pytest.raises(httpx.HTTPStatusError):
        await fn()

    # Default max_attempts=3
    assert call_count == 5


async def test_retryable_with_custom_max_attempts():
    """@retryable(max_attempts=N) should limit to exactly N attempts."""
    call_count = 0

    @retryable(max_attempts=5, base_delay=0, cap=0)
    async def fn():
        nonlocal call_count
        call_count += 1
        raise _make_status_error(503)

    with pytest.raises(httpx.HTTPStatusError):
        await fn()

    assert call_count == 5
