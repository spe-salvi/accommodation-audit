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
        repo = CanvasRepo(client)
        submissions = await repo.list_submissions(
            course_id=12977, quiz_id=189437, engine="new"
        )
        for s in submissions:
            print(s)

asyncio.run(main())