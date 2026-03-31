"""
LTI session management for the New Quiz service.

The New Quiz service (franciscan.quiz-lti-pdx-prod.instructure.com) uses a
short-lived Bearer token that is issued during the LTI launch handshake. This
token cannot be obtained from the Canvas API — it must be extracted from the
HTML response of the LTI launch POST, which is only triggered when a browser
navigates to a Canvas assignment page that uses the New Quiz external tool.

Auth flow
---------
1. Playwright opens a browser window and navigates to Canvas login.
2. The user logs in via the Canvas backdoor (username/password, no 2FA).
3. Playwright navigates to a Canvas assignment page for each uncached quiz.
   - Canvas embeds an iframe that POSTs the LTI launch to the LTI service.
   - Playwright intercepts the LTI launch response and extracts:
       a. access_token  — the Bearer token (account-scoped, reusable)
       b. launch_url    — contains the LTI assignment ID
4. The browser context is kept open across all navigations so the LTI
   session cookie persists without re-authentication.
5. The browser closes after all uncached IDs are discovered.

Token lifetime
--------------
The token appears to be session-scoped with no explicit expiry in the payload.
We conservatively treat it as valid for 2 hours. If a 401 is received during
an API call, the caller should re-acquire the session via acquire_lti_session().

LTI ID mapping
--------------
Canvas assignment IDs (e.g. 189437) differ from LTI service assignment IDs
(e.g. 10199). The mapping is discovered via the launch_url in launch_params
and persisted by LtiIdCache so Playwright only runs for new quizzes.

Exceptions raised
-----------------
LtiSessionError    Playwright login failed or token could not be extracted.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from playwright.async_api import async_playwright, Response

from audit.cache.lti_id_cache import LtiIdCache
from audit.config import settings
from audit.exceptions import LtiSessionError

logger = logging.getLogger(__name__)

# Regex to extract the LTI assignment ID from the launch_url.
# launch_url looks like:
#   https://franciscan.quiz-lti-pdx-prod.instructure.com/build/10199?
_LAUNCH_URL_RE = re.compile(r'/build/(\d+)')

# Regex to extract the access_token from the raw HTML response body.
# We use regex rather than JSON parsing because the token is embedded
# in a JavaScript variable inside the HTML page.
_ACCESS_TOKEN_RE = re.compile(r'"access_token"\s*:\s*"([^"]+)"')

# Conservative token TTL. The LTI service does not expose expiry in the token.
_TOKEN_TTL_SECONDS = 7200


@dataclass
class LtiSession:
    """
    Holds the Bearer token and LTI ID mappings acquired during a Playwright
    session. Shared across all API calls for the duration of an audit run.

    Attributes
    ----------
    access_token:
        Bearer token for the LTI service API.
    lti_id_by_canvas_id:
        Maps Canvas assignment IDs to LTI service assignment IDs.
    acquired_at:
        UTC timestamp when the session was created.
    ttl_seconds:
        Conservative token lifetime. The LTI service does not expose
        expiry; 2 hours is a safe default.
    """

    access_token: str
    lti_id_by_canvas_id: dict[int, int]
    acquired_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ttl_seconds: int = _TOKEN_TTL_SECONDS

    @property
    def is_expired(self) -> bool:
        """True if the token has likely expired based on its TTL."""
        age = datetime.now(timezone.utc) - self.acquired_at
        return age > timedelta(seconds=self.ttl_seconds)

    @property
    def auth_header(self) -> str:
        """Authorization header value for LTI service API requests."""
        return f"Bearer {self.access_token}"


async def acquire_lti_session(
    *,
    canvas_base_url: str,
    course_id: int,
    canvas_assignment_ids: list[int],
    lti_id_cache: LtiIdCache,
) -> LtiSession:
    """
    Launch a visible browser, log in to Canvas, then discover LTI IDs
    for all uncached assignments and extract the Bearer token.

    Parameters
    ----------
    canvas_base_url:
        Base URL of the Canvas instance.
    course_id:
        Canvas course ID used to construct assignment URLs.
    canvas_assignment_ids:
        All Canvas assignment IDs that need participants data in this run.
    lti_id_cache:
        Persistent cache. Already-cached IDs are skipped; newly discovered
        IDs are written back to the cache before returning.

    Returns
    -------
    LtiSession with the Bearer token and a complete lti_id_by_canvas_id
    mapping for all requested assignment IDs (cached + newly discovered).

    Raises
    ------
    LtiSessionError
        If Playwright fails to log in or cannot extract an access token.
    """
    # Build the complete mapping from cache first.
    lti_id_by_canvas_id: dict[int, int] = {}
    for cid in canvas_assignment_ids:
        cached = lti_id_cache.get(cid)
        if cached is not None:
            lti_id_by_canvas_id[cid] = cached

    uncached = lti_id_cache.missing(canvas_assignment_ids)

    access_token: str | None = None

    if uncached:
        logger.info(
            "LTI session: %d assignment(s) not in cache, launching browser",
            len(uncached),
        )
        discovered, access_token = await _run_playwright(
            canvas_base_url=canvas_base_url,
            course_id=course_id,
            canvas_assignment_ids=uncached,
        )
        lti_id_by_canvas_id.update(discovered)
        lti_id_cache.set_many(discovered)
    else:
        logger.info(
            "LTI session: all %d assignment(s) found in cache",
            len(canvas_assignment_ids),
        )

    if access_token is None:
        # All IDs were cached but we still need a fresh token.
        # Load any one assignment page to get the token.
        logger.info("LTI session: acquiring token from first cached assignment")
        _, access_token = await _run_playwright(
            canvas_base_url=canvas_base_url,
            course_id=course_id,
            canvas_assignment_ids=[canvas_assignment_ids[0]],
            token_only=True,
        )

    if access_token is None:
        raise LtiSessionError(
            "Playwright completed but no access token was extracted. "
            "The LTI launch may not have fired — check that the assignment "
            "is a New Quiz and that the browser reached the assignment page."
        )

    return LtiSession(
        access_token=access_token,
        lti_id_by_canvas_id=lti_id_by_canvas_id,
    )


async def _run_playwright(
    *,
    canvas_base_url: str,
    course_id: int,
    canvas_assignment_ids: list[int],
    token_only: bool = False,
) -> tuple[dict[int, int], str | None]:
    """
    Open a browser, log in via the Canvas backdoor, then navigate to each
    assignment page in parallel tabs to intercept LTI launch responses.

    Returns (lti_id_by_canvas_id, access_token).

    Raises
    ------
    LtiSessionError
        If the Canvas backdoor login fails or times out.
    """
    discovered: dict[int, int] = {}
    access_token: str | None = None

    async with async_playwright() as p:
        # Login is fully automated via env vars — no interactive steps needed.
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()

        # --- Step 1: Login via Canvas backdoor ---
        login_page = await context.new_page()
        logger.info(
            "LTI session: logging in via backdoor as %s",
            settings.canvas_admin_username,
        )

        await login_page.goto(settings.canvas_backdoor_url)
        await login_page.fill(
            'input[name="pseudonym_session[unique_id]"]',
            settings.canvas_admin_username,
        )
        await login_page.fill(
            'input[name="pseudonym_session[password]"]',
            settings.canvas_admin_password,
        )
        await login_page.click('input[type="submit"][value="Log In"]')

        try:
            await login_page.wait_for_url(
                f"{settings.canvas_base_url}/**",
                timeout=30_000,
                wait_until="networkidle",
            )
        except Exception as exc:
            await browser.close()
            raise LtiSessionError(
                f"Canvas backdoor login failed or timed out: {exc}"
            ) from exc

        logger.info("LTI session: login successful")
        await login_page.close()

        # --- Step 2: Navigate to assignment pages in parallel tabs ---
        tasks = [
            _extract_from_assignment_page(
                context=context,
                canvas_base_url=canvas_base_url,
                course_id=course_id,
                canvas_assignment_id=cid,
                token_only=token_only,
            )
            for cid in canvas_assignment_ids
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for cid, result in zip(canvas_assignment_ids, results):
            if isinstance(result, Exception):
                logger.warning(
                    "LTI session: failed to extract from assignment %d: %s",
                    cid,
                    result,
                )
                continue

            lti_id, token = result

            if token is not None and access_token is None:
                access_token = token
                logger.info("LTI session: access token acquired")

            if lti_id is not None and not token_only:
                discovered[cid] = lti_id
                logger.info(
                    "LTI session: canvas_assignment_id %d → lti_id %d",
                    cid,
                    lti_id,
                )

        await browser.close()

    return discovered, access_token


async def _extract_from_assignment_page(
    *,
    context,
    canvas_base_url: str,
    course_id: int,
    canvas_assignment_id: int,
    token_only: bool,
) -> tuple[int | None, str | None]:
    """
    Open a new tab, navigate to the Canvas assignment page, and intercept
    the LTI launch response to extract the LTI assignment ID and token.

    Returns (lti_id, access_token). Either may be None if not found.
    """
    lti_id: int | None = None
    token: str | None = None

    page = await context.new_page()

    async def handle_response(response: Response) -> None:
        nonlocal lti_id, token
        if "quiz-lti-pdx-prod" not in response.url:
            return
        if "/lti/launch" not in response.url:
            return
        if response.status != 200:
            return

        try:
            body = await response.text()
        except Exception as exc:
            logger.debug(
                "LTI session: could not read launch response body: %s", exc
            )
            return

        token_match = _ACCESS_TOKEN_RE.search(body)
        if token_match:
            token = token_match.group(1)

        if not token_only:
            launch_url_match = re.search(r'"launch_url"\s*:\s*"([^"]+)"', body)
            if launch_url_match:
                lti_id_match = _LAUNCH_URL_RE.search(launch_url_match.group(1))
                if lti_id_match:
                    lti_id = int(lti_id_match.group(1))

    page.on("response", handle_response)

    url = (
        f"{canvas_base_url}/courses/{course_id}"
        f"/assignments/{canvas_assignment_id}"
        f"?display=full_width_with_nav"
    )
    logger.debug("LTI session: navigating to %s", url)

    try:
        await page.goto(url, timeout=30_000, wait_until="networkidle")
    except Exception as exc:
        logger.warning(
            "LTI session: navigation to assignment %d timed out or failed: %s",
            canvas_assignment_id,
            exc,
        )
    finally:
        page.remove_listener("response", handle_response)
        await page.close()

    return lti_id, token
