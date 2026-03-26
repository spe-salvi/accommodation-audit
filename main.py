"""
CLI entry point for the accommodation audit system.

Creates the full dependency chain (HTTP client → Canvas client →
repository → service) and runs a sample audit. In production this
would accept command-line arguments for term_id, course_id, engine,
and accommodation types.
"""

"""
CLI entry point for the accommodation audit system.

Creates the full dependency chain (HTTP client → Canvas client →
repository → service) and runs a sample audit. In production this
would accept command-line arguments for term_id, course_id, engine,
and accommodation types.
"""

import asyncio
import httpx
from audit.config import Settings
from audit.clients.canvas_client import CanvasClient
from audit.repos.canvas_repo import CanvasRepo
from audit.services.accommodations import AccommodationService, AccommodationType
from audit.models.canvas import Submission, Quiz



async def demo() -> None:
    return
    # settings = Settings.from_env()
    # client = CanvasClient(
    #     CanvasConfig(
    #         base_url=settings.canvas_base_url,
    #         token=settings.canvas_token,
    #     )
    # )

    # try:
    #     repo = CanvasRepo(client)
    #     svc = AccommodationService(repo)

    #     rows = await svc.audit_quiz(
    #         course_id=12977,
    #         quiz_id=48379,
    #         engine="classic",
    #         accommodation_types=[AccommodationType.EXTRA_TIME],
    #     )
    #     print(f"rows: {len(rows)}")
    #     for row in rows[:5]:
    #         print(row)
    # finally:
    #     await client.aclose()


if __name__ == "__main__":
    asyncio.run(demo())