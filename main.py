"""
CLI entry point for the accommodation audit system.

Creates the full dependency chain (HTTP client → Canvas client →
repository → service) and runs a sample audit. In production this
would accept command-line arguments for term_id, course_id, engine,
and accommodation types.
"""

import asyncio
import httpx

from audit.clients.canvas_client import CanvasClient
from audit.config import settings
from audit.repos.canvas_repo import CanvasRepo
from audit.services.accommodations import AccommodationService, AccommodationType


async def demo() -> None:
    async with httpx.AsyncClient() as http:
        client = CanvasClient(
            base_url=settings.canvas_base_url,
            token=settings.canvas_token,
            http=http,
        )
        repo = CanvasRepo(client, account_id=int(settings.canvas_account_id))
        svc = AccommodationService(repo)

        rows = await svc.audit_quiz(
            course_id=12977,
            quiz_id=48379,
            engine="classic",
            accommodation_types=[AccommodationType.EXTRA_TIME],
        )
        print(f"rows: {len(rows)}")
        for row in rows[:5]:
            print(row)


if __name__ == "__main__":
    asyncio.run(demo())
