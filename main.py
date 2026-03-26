"""
CLI entry point for the accommodation audit system.

Creates the full dependency chain (HTTP client → Canvas client →
repository → service) and runs a sample audit. In production this
would accept command-line arguments for term_id, course_id, engine,
and accommodation types.
"""

import asyncio
import httpx
from audit.config import settings
from audit.clients.canvas_client import CanvasClient
from audit.repos.canvas_repo import CanvasRepo

async def main():
    async with httpx.AsyncClient() as http:
        client = CanvasClient(
            base_url=settings.canvas_base_url,
            token=settings.canvas_token,
            http=http,
        )
        repo = CanvasRepo(client, account_id=settings.canvas_account_id)
        courses = await repo.list_courses(term_id=117, engine="new")
        for c in courses:
            print(c)

asyncio.run(main())