import os
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {key!r}")
    return value


class Settings:
    @property
    def canvas_base_url(self) -> str:
        return _require("CANVAS_BASE_URL")

    @property
    def canvas_token(self) -> str:
        return _require("CANVAS_TOKEN")


settings = Settings()
