import pytest

from audit.services.accommodations import AccommodationType


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


async def test_audit_course_new_all_types_returns_rows(new_svc):
    rows = await new_svc.audit_course(
        course_id=12977,
        engine="new",
    )

    assert len(rows) > 0


async def test_audit_term_new_all_types_returns_rows(new_svc):
    rows = await new_svc.audit_term(
        term_id=117,
        engine="new",
    )

    assert len(rows) > 0


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


async def test_extra_attempt_new_uses_submissions_not_participants(new_svc):
    """
    EXTRA_ATTEMPT for new quizzes reads from submissions, not participants.
    This means rows are produced even when participants are available —
    the source data is independent of the LTI client.
    """
    rows = await new_svc.audit_quiz(
        course_id=12977,
        quiz_id=189437,
        engine="new",
        accommodation_types=[AccommodationType.EXTRA_ATTEMPT],
    )

    assert len(rows) > 0
    assert all(row.accommodation_type == AccommodationType.EXTRA_ATTEMPT for row in rows)
    # Rows are keyed by user_id (from submissions), not participant_id
    assert all(row.user_id is not None for row in rows)
    assert all(row.item_id is None for row in rows)


async def test_extra_time_new_uses_participants(new_svc):
    """
    EXTRA_TIME for new quizzes reads from participants (LTI data).
    The JSON fixture includes participants so this produces rows.
    In production without the LTI client, this returns zero rows.
    """
    rows = await new_svc.audit_quiz(
        course_id=12977,
        quiz_id=189437,
        engine="new",
        accommodation_types=[AccommodationType.EXTRA_TIME],
    )

    assert len(rows) > 0
    assert all(row.accommodation_type == AccommodationType.EXTRA_TIME for row in rows)
    assert all(row.user_id is not None for row in rows)


async def test_extra_attempt_and_extra_time_produce_independent_rows(new_svc):
    """
    EXTRA_ATTEMPT iterates submissions; EXTRA_TIME iterates participants.
    Both can be requested together and produce separate row sets.
    """
    rows = await new_svc.audit_quiz(
        course_id=12977,
        quiz_id=189437,
        engine="new",
        accommodation_types=[
            AccommodationType.EXTRA_TIME,
            AccommodationType.EXTRA_ATTEMPT,
        ],
    )

    extra_time_rows = [r for r in rows if r.accommodation_type == AccommodationType.EXTRA_TIME]
    extra_attempt_rows = [r for r in rows if r.accommodation_type == AccommodationType.EXTRA_ATTEMPT]

    assert len(extra_time_rows) > 0
    assert len(extra_attempt_rows) > 0
    # They come from different sources so counts may differ
    assert len(extra_time_rows) != 0
    assert len(extra_attempt_rows) != 0
