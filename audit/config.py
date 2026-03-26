import os
from dotenv import load_dotenv

load_dotenv()

"""
Retrieve a required environment variable or raise immediately.

Raises:
    RuntimeError: If the variable is unset or empty.
"""
def _require(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {key!r}")
    return value



"""
Configuration properties.

Properties are evaluated on access (not at import time) so that
missing variables only raise when actually needed. This avoids
import-time crashes in test environments that don't set all
production variables.
"""
class Settings:
    @property
    def canvas_base_url(self) -> str:
        return _require("CANVAS_BASE_URL")

    @property
    def canvas_token(self) -> str:
        return _require("CANVAS_TOKEN")

    @property
    def canvas_account_id(self) -> int:
        return int(_require("CANVAS_ACCOUNT_ID"))


settings = Settings()
