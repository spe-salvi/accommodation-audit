import asyncio
from pathlib import Path
from audit.repos.json_repo import JsonRepo
from audit.services.accommodations import AccommodationService, AccommodationType


BASE_DIR = Path(__file__).resolve().parent
DUMPS_DIR = BASE_DIR / "dumps"

async def demo():
    repo = JsonRepo(participant_path=DUMPS_DIR / "participant.json")
    svc = AccommodationService(repo)

    result = await svc.evaluate(
        course_id=12976,
        quiz_id=189407,
        user_id=7653,
        accommodation_type=AccommodationType.EXTRA_TIME,
    )
    print(result)

if __name__ == "__main__":
    asyncio.run(demo())