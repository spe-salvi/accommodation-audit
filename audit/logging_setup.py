"""
Logging configuration for the accommodation audit system.

Writes log records to a rotating file. The default level is WARNING
so normal runs are silent unless something goes wrong. Pass
``level=logging.DEBUG`` (or use the ``--debug`` CLI flag) to see
API calls and retry attempts.

Log format
----------
    2026-03-31 14:22:01,543 WARNING  audit.clients.retry — HTTP 429 on attempt 1/3 ...
    2026-03-31 14:22:03,102 WARNING  audit.repos.canvas_repo — list_participants: LTI error ...

Usage
-----
Call once at application startup, before any audit work begins::

    from audit.logging_setup import setup_logging
    import logging

    setup_logging()                          # WARNING+ to file
    setup_logging(level=logging.DEBUG)       # DEBUG+ to file (verbose)
    setup_logging(log_dir="logs")            # custom directory
"""

from __future__ import annotations

import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path


_DEFAULT_LOG_DIR = "logs"
_DEFAULT_LEVEL = logging.WARNING
_FORMAT = "%(asctime)s %(levelname)-8s %(name)s — %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Rotate at midnight, keep 14 days of history.
_BACKUP_COUNT = 14


def setup_logging(
    *,
    level: int = _DEFAULT_LEVEL,
    log_dir: str | Path = _DEFAULT_LOG_DIR,
) -> Path:
    """
    Configure file-based logging for the audit system.

    Creates ``log_dir`` if it does not exist, then attaches a
    ``TimedRotatingFileHandler`` that writes to
    ``<log_dir>/audit_<YYYY-MM-DD>.log`` and rotates at midnight.

    Parameters
    ----------
    level:
        Minimum log level to record. Defaults to ``logging.WARNING``.
        Pass ``logging.DEBUG`` to capture API calls and retry attempts.
    log_dir:
        Directory where log files are written. Created if absent.
        Defaults to ``"logs"`` relative to the working directory.

    Returns
    -------
    Path
        The path to the active log file.
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = log_dir / f"audit_{today}.log"

    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)

    handler = logging.handlers.TimedRotatingFileHandler(
        filename=log_file,
        when="midnight",
        utc=True,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(formatter)

    # Configure the root "audit" logger so all submodule loggers
    # (audit.clients.retry, audit.repos.canvas_repo, etc.) inherit it.
    root_logger = logging.getLogger("audit")
    root_logger.setLevel(level)

    # Avoid adding duplicate handlers if setup_logging() is called twice.
    if not any(isinstance(h, logging.handlers.TimedRotatingFileHandler)
               for h in root_logger.handlers):
        root_logger.addHandler(handler)

    # Prevent log records from propagating to the root Python logger,
    # which would otherwise print to stderr by default.
    root_logger.propagate = False

    root_logger.info(
        "Logging initialised — level=%s file=%s",
        logging.getLevelName(level),
        log_file,
    )

    return log_file
