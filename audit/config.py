"""
Application configuration via environment variables.

Uses python-dotenv to load a ``.env`` file, then exposes required
settings as properties on a singleton ``Settings`` instance. Missing
variables raise immediately at access time with a clear error message,
preventing silent misconfiguration.

Required environment variables:
  - ``CANVAS_BASE_URL``: Canvas instance URL (e.g. https://canvas.university.edu)
  - ``CANVAS_TOKEN``: API access token with read permissions
  - ``CANVAS_ACCOUNT_ID``: Numeric account ID for course listing scope
"""

import os
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    """
    Retrieve a required environment variable or raise immediately.

    Raises:
        RuntimeError: If the variable is unset or empty.
    """
    value = os.environ.get(key)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {key!r}")
    return value


class Settings:
    """
    Lazy-loaded configuration properties.

    Properties are evaluated on access (not at import time) so that
    missing variables only raise when actually needed. This avoids
    import-time crashes in test environments that don't set all
    production variables.
    """

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
