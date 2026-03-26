import pytest
import httpx

from audit.config import settings
from audit.clients.canvas_client import CanvasClient
from audit.repos.canvas_repo import CanvasRepo


@pytest.fixture
async def canvas_repo():
    async with httpx.AsyncClient() as http:
        client = CanvasClient(
            base_url=settings.canvas_base_url,
            headers={"Authorization": f"Bearer {settings.canvas_token}"},
            http=http,
        )
        yield CanvasRepo(client, account_id=int(settings.canvas_account_id))
