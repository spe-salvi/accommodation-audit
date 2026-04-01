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

Batching strategy
-----------------
Rather than fetching one user per row (which would duplicate calls for
students appearing on multiple quizzes), the Enricher:
  1. Collects all unique user_ids across the entire row list
  2. Subtracts any already in the in-run cache
  3. Fetches the remainder in parallel via asyncio.gather
  4. Fills all rows in a single O(n) pass using a dict lookup

For a term audit with 54k rows and ~500 unique students, this means
at most 500 API calls on the first run, zero on subsequent runs.

Usage
-----
    enricher = Enricher(repo=repo)
    enriched_rows = await enricher.enrich(rows)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace

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
    """

    def __init__(self, repo: CanvasRepo) -> None:
        self._repo = repo
        # In-run caches to avoid redundant calls across engines.
        self._term_name_by_id: dict[int, str] | None = None
        self._user_by_id: dict[int, User] = {}

    async def enrich(self, rows: list[AuditRow]) -> list[AuditRow]:
        """
        Return a new list of ``AuditRow`` objects with enrichment fields
        filled in. The original rows are never mutated.

        Populates:
            - ``term_name``   (from enrollment_term_id via Terms API)
            - ``user_name``   (from user_id via Users API, batched)
            - ``sis_user_id`` (from same user fetch as user_name)

        Parameters
        ----------
        rows:
            Raw audit rows from ``AccommodationService``.

        Returns
        -------
        list[AuditRow]
            New list with enrichment fields populated where data is available.
        """
        if not rows:
            return rows

        # Both enrichments run concurrently — terms fetch and user batch
        # fetch are independent, so we can gather them.
        term_map, user_map = await asyncio.gather(
            self._get_term_map(),
            self._get_user_map(rows),
        )

        return [self._enrich_row(row, term_map, user_map) for row in rows]

    # ------------------------------------------------------------------
    # Term enrichment
    # ------------------------------------------------------------------

    async def _get_term_map(self) -> dict[int, str]:
        """
        Build a ``{term_id: term_name}`` dict, fetching terms once per
        Enricher instance. The underlying ``list_terms()`` call is backed
        by the persistent cache (1-year TTL).
        """
        if self._term_name_by_id is not None:
            return self._term_name_by_id

        try:
            terms = await self._repo.list_terms()
            self._term_name_by_id = {
                t.term_id: t.name
                for t in terms
                if t.name is not None
            }
            logger.debug("Enricher: loaded %d term names", len(self._term_name_by_id))
        except Exception as exc:
            logger.warning(
                "Enricher: could not load terms (%s) — term_name will be empty", exc
            )
            self._term_name_by_id = {}

        return self._term_name_by_id

    # ------------------------------------------------------------------
    # User enrichment
    # ------------------------------------------------------------------

    async def _get_user_map(self, rows: list[AuditRow]) -> dict[int, User]:
        """
        Build a ``{user_id: User}`` dict for all unique user IDs in *rows*.

        Users already in the in-run cache are skipped. The remainder are
        fetched in parallel via asyncio.gather — one call per unique user.
        All fetched users are added to the in-run cache for reuse if
        enrich() is called again (e.g. for a second engine's rows).
        """
        # Collect unique user IDs not yet in the in-run cache.
        needed = {
            row.user_id
            for row in rows
            if row.user_id is not None and row.user_id not in self._user_by_id
        }

        if needed:
            logger.debug(
                "Enricher: fetching %d unique user(s) (persistent cache will absorb most)",
                len(needed),
            )
            results = await asyncio.gather(
                *[self._fetch_user(uid) for uid in needed],
                return_exceptions=True,
            )
            for user_id, result in zip(needed, results):
                if isinstance(result, Exception):
                    logger.warning(
                        "Enricher: could not fetch user_id=%d (%s) — fields will be empty",
                        user_id, result,
                    )
                elif result is not None:
                    self._user_by_id[user_id] = result

        return self._user_by_id

    async def _fetch_user(self, user_id: int) -> User | None:
        """Fetch a single user via the repo (which checks persistent cache first)."""
        try:
            return await self._repo.get_user(user_id)
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
        Return a new ``AuditRow`` with term_name, user_name, and
        sis_user_id filled in where data is available.

        Uses ``dataclasses.replace`` so the frozen dataclass is never
        mutated — a new object is returned for each row.
        """
        updates: dict = {}

        # Term name
        if row.enrollment_term_id is not None:
            term_name = term_map.get(row.enrollment_term_id)
            if term_name is not None:
                updates["term_name"] = term_name

        # User name and SIS user ID
        if row.user_id is not None:
            user = user_map.get(row.user_id)
            if user is not None:
                updates["user_name"] = user.sortable_name
                updates["sis_user_id"] = user.sis_user_id

        if not updates:
            return row

        return replace(row, **updates)
