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
        submission_path=DUMPS_DIR / "classic_submissions.json",
        )
    
    svc = AccommodationService(repo)

    # rows = await svc.audit(
    #     AuditRequest(
    #         course_id=12976,
    #         quiz_id=48379,
    #         engine="classic",
    #         accommodation_type=AccommodationType.EXTRA_TIME,
    #     )
    # )

    rows = await svc.audit(
        AuditRequest(
            course_id=12976,
            quiz_id=189407,
            engine="new",
            accommodation_type=AccommodationType.EXTRA_TIME,
        )
    )

    for row in rows:
        print(row)

if __name__ == "__main__":
    asyncio.run(demo())