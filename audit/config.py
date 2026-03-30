"""
Environment-based configuration for the accommodation audit system.

All settings are read from environment variables, with support for
``.env`` files via ``python-dotenv``. Missing required variables raise
``AuditConfigError`` at access time rather than at import time, so the
error message clearly identifies which variable is missing.

Required variables
------------------
CANVAS_BASE_URL        Canvas instance URL (e.g. https://canvas.university.edu)
CANVAS_TOKEN           Canvas API access token
CANVAS_ACCOUNT_ID      Root or sub-account ID for course listing
CANVAS_BACKDOOR_URL    Canvas direct login URL for admin backdoor access
CANVAS_ADMIN_USERNAME  Admin username for backdoor login
CANVAS_ADMIN_PASSWORD  Admin password for backdoor login

Usage
-----
    from audit.config import settings

    client = CanvasClient(
        base_url=settings.canvas_base_url,
        token=settings.canvas_token,
        http=http,
    )
"""

import os

from dotenv import load_dotenv

from audit.exceptions import AuditConfigError

load_dotenv()


def _require(key: str) -> str:
    """
    Return the value of an environment variable.

    Raises
    ------
    AuditConfigError
        If the variable is not set or is an empty string.
    """
    value = os.environ.get(key)
    if not value:
        raise AuditConfigError(
            f"Missing required environment variable: {key!r}. "
            f"Add it to your .env file or set it in the environment."
        )
    return value


class Settings:
    """
    Lazy accessor for application configuration.

    Each property reads from the environment on access so that missing
    variables are reported with a clear error at the point of use.
    """

    @property
    def canvas_base_url(self) -> str:
        """Canvas instance base URL, e.g. https://canvas.university.edu"""
        return _require("CANVAS_BASE_URL")

    @property
    def canvas_token(self) -> str:
        """Canvas API Bearer token."""
        return _require("CANVAS_TOKEN")

    @property
    def canvas_account_id(self) -> int:
        """Root or sub-account ID used for account-scoped API calls."""
        return int(_require("CANVAS_ACCOUNT_ID"))

    @property
    def canvas_backdoor_url(self) -> str:
        """Canvas direct login URL for the admin backdoor."""
        return _require("CANVAS_BACKDOOR_URL")

    @property
    def canvas_admin_username(self) -> str:
        """Admin username for Canvas backdoor login."""
        return _require("CANVAS_ADMIN_USERNAME")

    @property
    def canvas_admin_password(self) -> str:
        """Admin password for Canvas backdoor login."""
        return _require("CANVAS_ADMIN_PASSWORD")


settings = Settings()
