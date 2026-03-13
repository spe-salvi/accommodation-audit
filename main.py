import asyncio
from pathlib import Path
from audit.repos.json_repo import JsonRepo
from audit.services.accommodations import AccommodationService, AccommodationType
from audit.models.audit import AuditRequest

BASE_DIR = Path(__file__).resolve().parent
DUMPS_DIR = BASE_DIR / "dumps"

async def demo():
    repo = JsonRepo(
        participant_path=DUMPS_DIR / "participants.json",
        # submission_path=DUMPS_DIR / "new_submissions.json",
        submission_path=DUMPS_DIR / "classic_submissions.json",
        items_path=DUMPS_DIR / "new_items.json",
        quizzes_path=DUMPS_DIR / "classic_quiz.json",
        # quizzes_path=DUMPS_DIR / "new_quiz.json",
        )
    
    svc = AccommodationService(repo)

    rows = await svc.audit_accommodation(
        AuditRequest(
            course_id=12976,
            quiz_id=48379,
            engine="classic",
            accommodation_type=AccommodationType.EXTRA_ATTEMPT
            # accommodation_type=AccommodationType.EXTRA_TIME
            # accommodation_type=AccommodationType.SPELL_CHECK
        )
    )

    # rows = await svc.audit_accommodation(
    #     AuditRequest(
    #         course_id=12976,
    #         quiz_id=189407,
    #         engine="new",
    #         # accommodation_type=AccommodationType.EXTRA_ATTEMPT,
    #         accommodation_type=AccommodationType.EXTRA_TIME,
    #         # accommodation_type=AccommodationType.SPELL_CHECK,
    #     )
    # )

    # rows = await svc.audit_quiz(
    #     course_id=12976,
    #     quiz_id=189407,
    #     engine="new",
    #     # engine="classic",
    #     accommodation_types=[
    #         AccommodationType.EXTRA_TIME,
    #         AccommodationType.SPELL_CHECK,
    #     ],
    # )

    # rows = await svc.audit_quiz(
    #     course_id=12976,
    #     quiz_id=189407,
    #     # engine="new",
    #     engine="classic",
    # )

    for row in rows:
        print(row)

if __name__ == "__main__":
    asyncio.run(demo())