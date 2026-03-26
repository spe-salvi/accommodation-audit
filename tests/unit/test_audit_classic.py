import pytest

from audit.services.accommodations import AccommodationType


@pytest.mark.asyncio
async def test_audit_course_classic_all_types_returns_rows(classic_svc):
    rows = await classic_svc.audit_course(
        course_id=12977,
        engine="classic",
    )

    assert len(rows) > 0


@pytest.mark.asyncio
async def test_audit_term_classic_all_types_returns_rows(classic_svc):
    rows = await classic_svc.audit_term(
        term_id=117,
        engine="classic",
    )

    assert len(rows) > 0


@pytest.mark.asyncio
async def test_audit_quiz_classic_returns_rows(classic_svc):
    rows = await classic_svc.audit_quiz(
        course_id=12977,
        quiz_id=48379,
        engine="classic",
    )

    assert len(rows) > 0
    assert all(row.course_id == 12977 for row in rows)
    assert all(row.quiz_id == 48379 for row in rows)
    assert all(row.engine == "classic" for row in rows)


@pytest.mark.asyncio
async def test_classic_extra_time_has_positive_case(classic_svc):
    rows = await classic_svc.audit_quiz(
        course_id=12977,
        quiz_id=48379,
        engine="classic",
        accommodation_types=[AccommodationType.EXTRA_TIME],
    )

    assert len(rows) > 0
    assert any(row.has_accommodation for row in rows)