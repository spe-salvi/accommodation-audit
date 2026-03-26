import pytest

from audit.services.accommodations import AccommodationType


@pytest.mark.asyncio
async def test_audit_course_new_specific_types_returns_rows(new_svc):
    rows = await new_svc.audit_course(
        course_id=12977,
        engine="new",
        accommodation_types=[
            AccommodationType.EXTRA_TIME,
            AccommodationType.SPELL_CHECK,
        ],
    )

    assert len(rows) > 0
    assert {row.accommodation_type for row in rows} <= {
        AccommodationType.EXTRA_TIME,
        AccommodationType.SPELL_CHECK,
    }


@pytest.mark.asyncio
async def test_audit_course_new_all_types_returns_rows(new_svc):
    rows = await new_svc.audit_course(
        course_id=12977,
        engine="new",
    )

    assert len(rows) > 0


@pytest.mark.asyncio
async def test_audit_term_new_all_types_returns_rows(new_svc):
    rows = await new_svc.audit_term(
        term_id=117,
        engine="new",
    )

    assert len(rows) > 0


@pytest.mark.asyncio
async def test_audit_quiz_new_returns_rows(new_svc):
    rows = await new_svc.audit_quiz(
        course_id=12977,
        quiz_id=189437,
        engine="new",
    )

    assert len(rows) > 0
    assert all(row.course_id == 12977 for row in rows)
    assert all(row.quiz_id == 189437 for row in rows)
    assert all(row.engine == "new" for row in rows)