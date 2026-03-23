from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(slots=True, frozen=True)
class CanvasConfig:
    base_url: str
    token: str
    timeout_seconds: float = 30.0


class CanvasClient:
    def __init__(self, config: CanvasConfig):
        self._config = config
        self._client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/"),
            timeout=config.timeout_seconds,
            headers={
                "Authorization": f"Bearer {config.token}",
                "Accept": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        response = await self._client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    async def get_paginated_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        next_url: str | None = path
        next_params = dict(params or {})

        while next_url is not None:
            response = await self._client.get(next_url, params=next_params)
            response.raise_for_status()

            payload = response.json()
            if isinstance(payload, list):
                results.extend(payload)
            else:
                break

            next_url = response.links.get("next", {}).get("url")
            next_params = None

        return results