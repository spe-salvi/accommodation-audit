"""
Unit tests for audit.enrichment.Enricher.

Uses a lightweight FakeRepo that avoids any network calls. Tests cover:
  - term_name populated from enrollment_term_id
  - user_name and sis_user_id populated from user_id
  - Rows without user_id (spell-check) left unchanged
  - Rows with no enrollment_term_id left unchanged
  - Term fetch failure handled gracefully (term_name stays None)
  - User fetch failure handled gracefully (user fields stay None)
  - Batching: unique user_ids fetched once regardless of row count
  - Empty row list returns immediately
  - In-run cache: second enrich() call reuses already-fetched data
"""

import asyncio
from dataclasses import replace

import pytest

from audit.enrichment import Enricher
from audit.models.audit import AuditRow
from audit.models.canvas import Term, User
from audit.repos.base import AccommodationType


# ---------------------------------------------------------------------------
# Fake repo
# ---------------------------------------------------------------------------

class FakeRepo:
    """
    Minimal repo stub for Enricher tests.

    Injects controllable term and user data without any network calls.
    Tracks call counts so tests can verify batching behaviour.
    """

    def __init__(
        self,
        *,
        terms: list[Term] | None = None,
        users: dict[int, User] | None = None,
        terms_fail: bool = False,
        user_fail_ids: set[int] | None = None,
    ):
        self._terms = terms or []
        self._users = users or {}
        self._terms_fail = terms_fail
        self._user_fail_ids = user_fail_ids or set()
        self.list_terms_call_count = 0
        self.get_user_call_count = 0

    async def list_terms(self) -> list[Term]:
        self.list_terms_call_count += 1
        if self._terms_fail:
            raise RuntimeError("Simulated terms fetch failure")
        return self._terms

    async def get_user(self, user_id: int) -> User | None:
        self.get_user_call_count += 1
        if user_id in self._user_fail_ids:
            raise RuntimeError(f"Simulated user fetch failure for {user_id}")
        return self._users.get(user_id)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TERM_117 = Term(term_id=117, name="2025-2026 - Spring", sis_term_id="spring-2026")
TERM_116 = Term(term_id=116, name="2025-2026 - Fall",   sis_term_id="fall-2025")

USER_5961 = User(id=5961, sortable_name="McCarthy, Patrick", sis_user_id="2621872")
USER_9448 = User(id=9448, sortable_name="Smith, Jane",       sis_user_id="1234567")


def _row(
    *,
    user_id: int | None = 5961,
    enrollment_term_id: int | None = 117,
    accommodation_type=AccommodationType.EXTRA_TIME,
    item_id: int | None = None,
) -> AuditRow:
    return AuditRow(
        course_id=12977,
        quiz_id=48379,
        user_id=user_id,
        item_id=item_id,
        engine="classic",
        accommodation_type=accommodation_type,
        enrollment_term_id=enrollment_term_id,
    )


# ---------------------------------------------------------------------------
# term_name enrichment
# ---------------------------------------------------------------------------

async def test_term_name_populated(tmp_path):
    repo = FakeRepo(terms=[TERM_117, TERM_116])
    enricher = Enricher(repo=repo)
    rows = await enricher.enrich([_row(enrollment_term_id=117)])
    assert rows[0].term_name == "2025-2026 - Spring"


async def test_term_name_correct_for_multiple_terms(tmp_path):
    repo = FakeRepo(terms=[TERM_117, TERM_116])
    enricher = Enricher(repo=repo)
    rows = await enricher.enrich([
        _row(enrollment_term_id=117),
        _row(enrollment_term_id=116),
    ])
    assert rows[0].term_name == "2025-2026 - Spring"
    assert rows[1].term_name == "2025-2026 - Fall"


async def test_term_name_none_when_term_id_missing():
    repo = FakeRepo(terms=[TERM_117])
    enricher = Enricher(repo=repo)
    rows = await enricher.enrich([_row(enrollment_term_id=None)])
    assert rows[0].term_name is None


async def test_term_name_none_when_term_not_in_list():
    repo = FakeRepo(terms=[TERM_117])
    enricher = Enricher(repo=repo)
    rows = await enricher.enrich([_row(enrollment_term_id=999)])
    assert rows[0].term_name is None


async def test_term_fetch_failure_leaves_term_name_none():
    repo = FakeRepo(terms_fail=True)
    enricher = Enricher(repo=repo)
    rows = await enricher.enrich([_row(enrollment_term_id=117)])
    # Should not raise; term_name stays None.
    assert rows[0].term_name is None


# ---------------------------------------------------------------------------
# user_name / sis_user_id enrichment
# ---------------------------------------------------------------------------

async def test_user_name_and_sis_user_id_populated():
    repo = FakeRepo(terms=[TERM_117], users={5961: USER_5961})
    enricher = Enricher(repo=repo)
    rows = await enricher.enrich([_row(user_id=5961)])
    assert rows[0].user_name == "McCarthy, Patrick"
    assert rows[0].sis_user_id == "2621872"


async def test_user_fields_none_when_user_id_missing():
    repo = FakeRepo(terms=[TERM_117], users={5961: USER_5961})
    enricher = Enricher(repo=repo)
    rows = await enricher.enrich([_row(user_id=None)])
    assert rows[0].user_name is None
    assert rows[0].sis_user_id is None


async def test_user_fields_none_when_user_not_found():
    repo = FakeRepo(terms=[TERM_117], users={})
    enricher = Enricher(repo=repo)
    rows = await enricher.enrich([_row(user_id=5961)])
    assert rows[0].user_name is None
    assert rows[0].sis_user_id is None


async def test_user_fetch_failure_leaves_fields_none():
    repo = FakeRepo(terms=[TERM_117], user_fail_ids={5961})
    enricher = Enricher(repo=repo)
    rows = await enricher.enrich([_row(user_id=5961)])
    assert rows[0].user_name is None


# ---------------------------------------------------------------------------
# Batching — unique user_ids fetched once
# ---------------------------------------------------------------------------

async def test_unique_users_fetched_once():
    """50 rows with 2 unique users should result in exactly 2 get_user calls."""
    repo = FakeRepo(terms=[TERM_117], users={5961: USER_5961, 9448: USER_9448})
    enricher = Enricher(repo=repo)

    rows = (
        [_row(user_id=5961)] * 25
        + [_row(user_id=9448)] * 25
    )
    enriched = await enricher.enrich(rows)

    assert repo.get_user_call_count == 2
    assert all(r.user_name == "McCarthy, Patrick" for r in enriched[:25])
    assert all(r.user_name == "Smith, Jane"       for r in enriched[25:])


async def test_terms_fetched_once_across_multiple_enrich_calls():
    """A second enrich() call should not re-fetch terms."""
    repo = FakeRepo(terms=[TERM_117], users={5961: USER_5961})
    enricher = Enricher(repo=repo)

    await enricher.enrich([_row(user_id=5961)])
    await enricher.enrich([_row(user_id=5961)])

    assert repo.list_terms_call_count == 1


async def test_users_not_refetched_on_second_enrich_call():
    """A second enrich() call for the same users should not hit the repo again."""
    repo = FakeRepo(terms=[TERM_117], users={5961: USER_5961})
    enricher = Enricher(repo=repo)

    await enricher.enrich([_row(user_id=5961)])
    first_count = repo.get_user_call_count

    await enricher.enrich([_row(user_id=5961)])
    assert repo.get_user_call_count == first_count  # no additional calls


# ---------------------------------------------------------------------------
# Spell-check rows (user_id=None) are not mutated
# ---------------------------------------------------------------------------

async def test_spell_check_rows_unchanged():
    """Rows with user_id=None (spell-check) should be returned as-is."""
    repo = FakeRepo(terms=[TERM_117], users={5961: USER_5961})
    enricher = Enricher(repo=repo)

    spell_row = _row(user_id=None, item_id=42, accommodation_type=AccommodationType.SPELL_CHECK)
    rows = await enricher.enrich([spell_row])

    assert rows[0].user_name is None
    assert rows[0].sis_user_id is None
    # term_name should still be populated (enrollment_term_id is set)
    assert rows[0].term_name == "2025-2026 - Spring"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

async def test_empty_rows_returns_empty():
    repo = FakeRepo(terms=[TERM_117])
    enricher = Enricher(repo=repo)
    assert await enricher.enrich([]) == []


async def test_original_rows_not_mutated():
    """enrich() must return new objects — original rows must be unchanged."""
    repo = FakeRepo(terms=[TERM_117], users={5961: USER_5961})
    enricher = Enricher(repo=repo)

    original = _row(user_id=5961, enrollment_term_id=117)
    enriched = await enricher.enrich([original])

    assert original.term_name is None
    assert original.user_name is None
    assert enriched[0].term_name == "2025-2026 - Spring"
    assert enriched[0].user_name == "McCarthy, Patrick"
    assert enriched[0] is not original
