import asyncio
from pathlib import Path
from audit.repos.json_repo import JsonRepo
from audit.services.accommodations import AccommodationService, AccommodationType


BASE_DIR = Path(__file__).resolve().parent
DUMPS_DIR = BASE_DIR / "dumps"

async def demo():
    repo = JsonRepo(participant_path=DUMPS_DIR / "participants.json")
    svc = AccommodationService(repo)

    rows = await svc.audit_course_quiz(
        course_id=12976,
        quiz_id=189407,
        accommodation_type=AccommodationType.EXTRA_TIME,
    )

    for r in rows:
        print(r)

if __name__ == "__main__":
    asyncio.run(demo())