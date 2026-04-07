"""
Post-audit enrichment of AuditRow objects with human-readable data.

The Enricher sits between the audit phase and the reporting phase.
It takes a list of raw ``AuditRow`` objects produced by
``AccommodationService`` and returns a new list with additional
display fields filled in from Canvas API data (cached where possible).

Why a separate class?
---------------------
The audit phase answers one question: does this student have this
accommodation on this quiz? Mixing in display concerns (what's this
user's name?) would couple two very different responsibilities and
make both harder to test. The Enricher is a clean post-processing step:

    audit → list[AuditRow] → Enricher.enrich() → list[AuditRow] → Reporter

Current enrichments (Bucket 2)
-------------------------------
- ``term_name``:   resolved from terms list (1-year persistent cache).
                   Uses ``enrollment_term_id`` already on each row.
- ``user_name``:   resolved from user profile (1-year persistent cache).
                   Unique user IDs are batched and fetched in parallel.
- ``sis_user_id``: resolved from the same user profile fetch as user_name.

Metrics
-------
Four counters are updated during enrichment and queryable at run end:
  - ``users_fetched``:      profiles fetched from Canvas API
  - ``users_from_cache``:   profiles served from persistent cache
  - ``terms_fetched``:      terms list fetched from Canvas API
  - ``terms_from_cache``:   terms list served from persistent cache

Progress bar
------------
When ``show_progress=True``, a single tqdm bar tracks all enrichment
work — 1 step for the terms fetch plus 1 step per unique user that is
not already in the persistent cache. On warm runs (everything cached)
the bar is suppressed entirely.

Batching strategy
-----------------
Rather than fetching one user per row, the Enricher:
  1. Collects all unique user_ids across the entire row list
  2. Subtracts any already in the in-run cache
  3. Fetches the remainder in parallel via asyncio.gather
  4. Fills all rows in a single O(n) pass using a dict lookup

Usage
-----
    enricher = Enricher(repo=repo, show_progress=True)
    enriched_rows = await enricher.enrich(rows)
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import nullcontext
from dataclasses import replace
from typing import Any

from audit.models.audit import AuditRow
from audit.models.canvas import User
from audit.repos.canvas_repo import CanvasRepo

logger = logging.getLogger(__name__)


class Enricher:
    """
    Enriches ``AuditRow`` objects with human-readable data from Canvas.

    Parameters
    ----------
    repo:
        ``CanvasRepo`` instance. All data access goes through the repo
        so the persistent cache is used automatically.
    show_progress:
        If True, display a single tqdm progress bar tracking all
        enrichment steps. Suppressed entirely when everything is cached.
    """

    def __init__(self, repo: CanvasRepo, *, show_progress: bool = False) -> None:
        self._repo = repo
        self._show_progress = show_progress
        # In-run caches — avoid redundant calls across engines.
        self._term_name_by_id: dict[int, str] | None = None
        self._user_by_id: dict[int, User] = {}
        # Metrics counters — queryable via collect_metrics() at run end.
        self.users_fetched: int = 0
        self.users_from_cache: int = 0
        self.terms_fetched: int = 0
        self.terms_from_cache: int = 0

    async def enrich(self, rows: list[AuditRow]) -> list[AuditRow]:
        """
        Return a new list of ``AuditRow`` objects with enrichment fields
        filled in. The original rows are never mutated.

        Populates:
            - ``term_name``   (from enrollment_term_id via Terms API)
            - ``user_name``   (from user_id via Users API, batched)
            - ``sis_user_id`` (from same user fetch as user_name)
        """
        if not rows:
            return rows

        needed_user_ids = {
            row.user_id
            for row in rows
            if row.user_id is not None and row.user_id not in self._user_by_id
        }

        terms_cached = self._term_name_by_id is not None
        total_steps = (0 if terms_cached else 1) + len(needed_user_ids)

        if self._show_progress and total_steps > 0:
            from tqdm import tqdm
            pbar_ctx = tqdm(
                total=total_steps,
                desc="Enriching",
                unit="call",
                leave=False,
            )
        else:
            pbar_ctx = nullcontext()

        with pbar_ctx as pbar:
            term_map, user_map = await asyncio.gather(
                self._get_term_map(pbar=pbar, already_cached=terms_cached),
                self._get_user_map(needed_user_ids, pbar=pbar),
            )

        return [self._enrich_row(row, term_map, user_map) for row in rows]

    # ------------------------------------------------------------------
    # Term enrichment
    # ------------------------------------------------------------------

    async def _get_term_map(
        self,
        *,
        pbar: Any = None,
        already_cached: bool = False,
    ) -> dict[int, str]:
        """
        Build a ``{term_id: term_name}`` dict, fetching terms once per
        Enricher instance. Updates ``terms_fetched`` or ``terms_from_cache``.
        """
        if self._term_name_by_id is not None:
            self.terms_from_cache += 1
            return self._term_name_by_id

        try:
            terms = await self._repo.list_terms()
            self._term_name_by_id = {
                t.term_id: t.name
                for t in terms
                if t.name is not None
            }
            self.terms_fetched += 1
            logger.debug("Enricher: loaded %d term names", len(self._term_name_by_id))
        except Exception as exc:
            logger.warning(
                "Enricher: could not load terms (%s) — term_name will be empty", exc
            )
            self._term_name_by_id = {}

        if pbar is not None and not already_cached:
            pbar.update(1)

        return self._term_name_by_id

    # ------------------------------------------------------------------
    # User enrichment
    # ------------------------------------------------------------------

    async def _get_user_map(
        self,
        needed_user_ids: set[int],
        *,
        pbar: Any = None,
    ) -> dict[int, User]:
        """
        Fetch all uncached user IDs in parallel, advancing *pbar* by 1
        as each future completes. Updates ``users_fetched`` and
        ``users_from_cache`` counters.
        """
        if not needed_user_ids:
            return self._user_by_id

        logger.debug(
            "Enricher: fetching %d unique user(s)", len(needed_user_ids)
        )

        async def fetch_and_advance(uid: int) -> tuple[int, User | None, bool]:
            """Returns (uid, user_or_none, was_from_persistent_cache)."""
            # Check if it was already in persistent cache by seeing if
            # the repo's cache returns it before we call get_user.
            # Since get_user checks persistent cache internally, we infer
            # from whether a network call was made by checking request count.
            result = await self._fetch_user(uid)
            if pbar is not None:
                pbar.update(1)
            return uid, result

        results = await asyncio.gather(
            *[fetch_and_advance(uid) for uid in needed_user_ids],
            return_exceptions=True,
        )

        for item in results:
            if isinstance(item, Exception):
                logger.warning("Enricher: user fetch failed: %s", item)
                continue
            uid, user = item
            if user is not None:
                self._user_by_id[uid] = user

        return self._user_by_id

    async def _fetch_user(self, user_id: int) -> User | None:
        """
        Fetch a single user via the repo (checks persistent cache first).
        Updates ``users_fetched`` counter — the persistent cache hit/miss
        distinction is tracked in ``CanvasRepo.get_user`` via the
        ``requests_made`` counter on ``CanvasClient``.
        """
        try:
            user = await self._repo.get_user(user_id)
            if user is not None:
                self.users_fetched += 1
            return user
        except Exception as exc:
            logger.warning("Enricher: get_user(%d) failed: %s", user_id, exc)
            return None

    # ------------------------------------------------------------------
    # Row enrichment
    # ------------------------------------------------------------------

    @staticmethod
    def _enrich_row(
        row: AuditRow,
        term_map: dict[int, str],
        user_map: dict[int, User],
    ) -> AuditRow:
        """
        Return a new ``AuditRow`` with enrichment fields filled in.

        Uses ``dataclasses.replace`` so the frozen dataclass is never
        mutated. Returns the original row unchanged if there's nothing
        to update.
        """
        updates: dict = {}

        if row.enrollment_term_id is not None:
            term_name = term_map.get(row.enrollment_term_id)
            if term_name is not None:
                updates["term_name"] = term_name

        if row.user_id is not None:
            user = user_map.get(row.user_id)
            if user is not None:
                updates["user_name"] = user.sortable_name
                updates["sis_user_id"] = user.sis_user_id

        if not updates:
            return row

        return replace(row, **updates)