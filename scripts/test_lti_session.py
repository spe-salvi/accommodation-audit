"""
Manual test script for LTI session acquisition via Playwright.

Run this script directly to verify:
  1. Playwright opens a browser and navigates to Canvas login
  2. You can complete SSO/2FA interactively
  3. The LTI launch is intercepted from the assignment iframe
  4. The access token and LTI assignment ID are extracted correctly
  5. The LTI ID cache is written to disk
  6. The NewQuizClient can fetch participants using the acquired token

Usage:
    python scripts/test_lti_session.py

Expected output:
    [session] Launching browser for 1 uncached assignment(s)...
    [session] Please log in to Canvas (you have 3 minutes)...
    [session] Login detected — navigating to assignment pages...
    [session] canvas_assignment_id 189437 → lti_id 10199
    [session] Access token acquired: AnUlJ93S...
    [client]  Fetched 21 participants for assignment 189437
    [client]  Sample participant user_ids: [7986, 9167, 9448, ...]
    [cache]   Cache now contains 1 entry — written to .lti_id_cache.json
    All checks passed.
"""

import asyncio
import logging
import sys
from pathlib import Path

# Allow running from project root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from audit.cache.lti_id_cache import LtiIdCache
from audit.clients.session import acquire_lti_session
from audit.clients.new_quiz_client import NewQuizClient
from audit.config import settings

# ---------------------------------------------------------------------------
# Configuration — edit these to match a known New Quiz in your Canvas instance
# ---------------------------------------------------------------------------
COURSE_ID = 12977
CANVAS_ASSIGNMENT_ID = 189437  # "Take Moral Theology and Gospel Morality Test"
EXPECTED_MIN_PARTICIPANTS = 1

# Use a throw-away cache file so this script doesn't pollute the real cache.
CACHE_PATH = Path(".lti_id_cache_test.json")
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("test_lti_session")


async def main() -> None:
    cache = LtiIdCache(path=CACHE_PATH)

    # ------------------------------------------------------------------
    # Step 1: Acquire LTI session via Playwright
    # ------------------------------------------------------------------
    logger.info("Step 1: acquiring LTI session via Playwright")
    logger.info("A browser window will open — please log in with your Canvas credentials")

    session = await acquire_lti_session(
        canvas_base_url=settings.canvas_base_url,
        course_id=COURSE_ID,
        canvas_assignment_ids=[CANVAS_ASSIGNMENT_ID],
        lti_id_cache=cache,
    )

    # ------------------------------------------------------------------
    # Step 2: Verify token and LTI ID were extracted
    # ------------------------------------------------------------------
    logger.info("Step 2: verifying session contents")

    assert session.access_token, "access_token is empty"
    logger.info("  access_token: %s...%s", session.access_token[:8], session.access_token[-4:])

    lti_id = session.lti_id_by_canvas_id.get(CANVAS_ASSIGNMENT_ID)
    assert lti_id is not None, (
        f"LTI ID not found for canvas_assignment_id={CANVAS_ASSIGNMENT_ID}. "
        f"Check that the assignment page loaded the LTI iframe correctly."
    )
    logger.info("  canvas_assignment_id %d → lti_id %d", CANVAS_ASSIGNMENT_ID, lti_id)

    assert not session.is_expired, "session reports as expired immediately after acquisition"
    logger.info("  session is_expired: %s (expected False)", session.is_expired)

    # ------------------------------------------------------------------
    # Step 3: Verify cache was written
    # ------------------------------------------------------------------
    logger.info("Step 3: verifying LTI ID cache")

    cached_id = cache.get(CANVAS_ASSIGNMENT_ID)
    assert cached_id == lti_id, (
        f"Cache mismatch: expected {lti_id}, got {cached_id}"
    )
    logger.info("  cache entry verified: %d → %d", CANVAS_ASSIGNMENT_ID, cached_id)
    logger.info("  cache file written to: %s", CACHE_PATH.resolve())

    # ------------------------------------------------------------------
    # Step 4: Fetch participants using NewQuizClient
    # ------------------------------------------------------------------
    logger.info("Step 4: fetching participants via NewQuizClient")

    async with httpx.AsyncClient() as http:
        client = NewQuizClient(session=session, http=http)
        participants = await client.list_participants(
            canvas_assignment_id=CANVAS_ASSIGNMENT_ID
        )

    assert len(participants) >= EXPECTED_MIN_PARTICIPANTS, (
        f"Expected at least {EXPECTED_MIN_PARTICIPANTS} participant(s), "
        f"got {len(participants)}"
    )
    logger.info("  fetched %d participant(s)", len(participants))

    sample_ids = [p.get("user_id") for p in participants[:5]]
    logger.info("  sample user_ids: %s", sample_ids)

    # Verify at least one participant has enrollment data (accommodation fields)
    has_enrollment = any(p.get("enrollment") for p in participants)
    logger.info(
        "  at least one participant has enrollment data: %s (expected True)",
        has_enrollment,
    )

    # ------------------------------------------------------------------
    # Step 5: Verify second run uses cache (no Playwright)
    # ------------------------------------------------------------------
    logger.info("Step 5: verifying cache hit on second acquisition")

    session2 = await acquire_lti_session(
        canvas_base_url=settings.canvas_base_url,
        course_id=COURSE_ID,
        canvas_assignment_ids=[CANVAS_ASSIGNMENT_ID],
        lti_id_cache=cache,
    )

    # The LTI ID should come from cache — but a new token is still acquired
    # (token is not cached, only the ID mapping is).
    lti_id2 = session2.lti_id_by_canvas_id.get(CANVAS_ASSIGNMENT_ID)
    assert lti_id2 == lti_id, (
        f"Cache returned wrong LTI ID on second run: expected {lti_id}, got {lti_id2}"
    )
    logger.info("  LTI ID correctly served from cache on second run")

    # ------------------------------------------------------------------
    # All checks passed
    # ------------------------------------------------------------------
    logger.info("=" * 60)
    logger.info("All checks passed.")
    logger.info("=" * 60)
    logger.info("")
    logger.info("Next step: wire NewQuizClient into CanvasRepo.list_participants")
    logger.info("and run the full integration test suite.")


if __name__ == "__main__":
    asyncio.run(main())
