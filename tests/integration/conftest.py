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
            token=settings.canvas_token,
            http=http,
        )
        yield CanvasRepo(client, account_id=settings.canvas_account_id)
