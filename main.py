"""
CLI entry point for the accommodation audit system.

Usage examples
--------------
Audit an entire term (both engines):
    python main.py audit --term 117

Audit a single course, classic engine only:
    python main.py audit --course 12977 --engine classic

Audit a specific quiz, extra time only:
    python main.py audit --quiz 48379 --engine new --types extra_time

Save to a custom path with debug logging:
    python main.py audit --term 117 --output ~/reports/sp26.xlsx --debug

Scope flags are mutually exclusive — supply exactly one of
--term, --course, or --quiz.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import click
import httpx

from audit.cache.runtime import RequestCache
from audit.clients.canvas_client import CanvasClient
from audit.config import settings
from audit.logging_setup import setup_logging
from audit.reporting import write_xlsx
from audit.repos.base import AccommodationType
from audit.repos.canvas_repo import CanvasRepo
from audit.services.accommodations import AccommodationService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TYPE_MAP: dict[str, AccommodationType] = {
    "extra_time":    AccommodationType.EXTRA_TIME,
    "extra_attempt": AccommodationType.EXTRA_ATTEMPT,
    "spell_check":   AccommodationType.SPELL_CHECK,
}

_ALL_ENGINES = ["new", "classic"]


def _default_output_path() -> Path:
    """Generate a timestamped default output filename."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return Path(f"audit_{ts}.xlsx")


def _fmt_elapsed(seconds: float) -> str:
    """Format elapsed seconds as a human-readable string, e.g. '2m 14s' or '38s'."""
    seconds = int(seconds)
    if seconds >= 60:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds}s"


async def _run_audit(
    *,
    scope: str,
    scope_id: int,
    engines: list[str],
    types: list[AccommodationType],
    output_path: Path,
    show_progress: bool,
) -> None:
    """
    Build the full dependency chain and run the audit.

    When multiple engines are requested they are fetched concurrently
    via asyncio.gather and merged into a single result set before writing.

    Parameters
    ----------
    show_progress:
        When True, tqdm progress bars are shown for course completion
        (term-scoped audits) and the Excel write step.
    """
    audit_start = time.perf_counter()
    cache = RequestCache()

    async with httpx.AsyncClient() as http:
        client = CanvasClient(
            base_url=settings.canvas_base_url,
            token=settings.canvas_token,
            http=http,
            cache=cache,
        )
        repo = CanvasRepo(client, account_id=settings.canvas_account_id)
        svc = AccommodationService(repo)

        tasks = []
        for eng in engines:
            if scope == "term":
                tasks.append(svc.audit_term(
                    term_id=scope_id,
                    engine=eng,
                    accommodation_types=types,
                    show_progress=show_progress,
                ))
            elif scope == "course":
                tasks.append(svc.audit_course(
                    course_id=scope_id,
                    engine=eng,
                    accommodation_types=types,
                ))
            elif scope == "quiz":
                tasks.append(svc.audit_quiz(
                    course_id=scope_id,
                    quiz_id=scope_id,
                    engine=eng,
                    accommodation_types=types,
                ))

        results = await asyncio.gather(*tasks)
        rows = [row for engine_rows in results for row in engine_rows]

    audit_elapsed = time.perf_counter() - audit_start
    audit_elapsed_str = _fmt_elapsed(audit_elapsed)

    cache.log_stats()

    summary = (
        f"Audit complete — {len(rows):,} rows across "
        f"{len(engines)} engine(s) in {audit_elapsed_str}."
    )
    click.echo(summary)
    logger.info(summary)

    write_start = time.perf_counter()
    write_xlsx(rows, output_path, show_progress=show_progress)
    write_elapsed_str = _fmt_elapsed(time.perf_counter() - write_start)

    write_summary = f"Report written to {output_path} in {write_elapsed_str}."
    click.echo(write_summary)
    logger.info(write_summary)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.group()
def cli() -> None:
    """Accommodation audit tool for Canvas LMS."""


@cli.command()
@click.option(
    "--term",   "term_id",
    type=int, default=None,
    help="Canvas enrollment term ID.",
)
@click.option(
    "--course", "course_id",
    type=int, default=None,
    help="Canvas course ID.",
)
@click.option(
    "--quiz",   "quiz_id",
    type=int, default=None,
    help="Canvas quiz or assignment ID.",
)
@click.option(
    "--engine",
    type=click.Choice(["new", "classic", "all"], case_sensitive=False),
    default="all",
    show_default=True,
    help="Quiz engine to audit. 'all' runs both concurrently.",
)
@click.option(
    "--types",
    multiple=True,
    type=click.Choice(list(_TYPE_MAP.keys()), case_sensitive=False),
    default=list(_TYPE_MAP.keys()),
    show_default=True,
    help="Accommodation type(s) to evaluate. Repeatable.",
)
@click.option(
    "--output",
    type=click.Path(dir_okay=False, writable=True, path_type=Path),
    default=None,
    help="Output .xlsx path. Defaults to audit_<timestamp>.xlsx.",
)
@click.option(
    "--debug",
    is_flag=True,
    default=False,
    help="Enable DEBUG log level.",
)
@click.option(
    "--no-progress",
    is_flag=True,
    default=False,
    help="Disable progress bars (useful for non-interactive environments).",
)
def audit(
    term_id: int | None,
    course_id: int | None,
    quiz_id: int | None,
    engine: str,
    types: tuple[str, ...],
    output: Path | None,
    debug: bool,
    no_progress: bool,
) -> None:
    """
    Run an accommodation audit and write results to Excel.

    Supply exactly one scope flag: --term, --course, or --quiz.

    \b
    Examples:
        python main.py audit --term 117
        python main.py audit --course 12977 --engine classic
        python main.py audit --quiz 48379 --engine new --types extra_time
        python main.py audit --term 117 --types extra_time extra_attempt
    """
    # --- Logging ---
    log_level = logging.DEBUG if debug else logging.WARNING
    log_file = setup_logging(level=log_level)
    if debug:
        click.echo(f"Debug logging → {log_file}")

    # --- Validate scope: exactly one required ---
    scope_args = {"term": term_id, "course": course_id, "quiz": quiz_id}
    provided = {k: v for k, v in scope_args.items() if v is not None}

    if len(provided) == 0:
        raise click.UsageError(
            "Supply exactly one scope flag: --term, --course, or --quiz."
        )
    if len(provided) > 1:
        raise click.UsageError(
            f"Supply exactly one scope flag — got: "
            f"{', '.join(f'--{k}' for k in provided)}."
        )

    scope, scope_id = next(iter(provided.items()))

    # --- Resolve engines ---
    engines = _ALL_ENGINES if engine == "all" else [engine]

    # --- Resolve accommodation types ---
    accommodation_types = [_TYPE_MAP[t] for t in (types or _TYPE_MAP.keys())]

    # --- Resolve output path ---
    output_path = output or _default_output_path()

    # --- Progress bars on by default; suppressed by --no-progress ---
    show_progress = not no_progress

    # --- Announce ---
    click.echo(
        f"Auditing {scope}={scope_id} | "
        f"engine={engine} | "
        f"types={list(types or _TYPE_MAP.keys())} | "
        f"output={output_path}"
    )

    asyncio.run(
        _run_audit(
            scope=scope,
            scope_id=scope_id,
            engines=engines,
            types=accommodation_types,
            output_path=output_path,
            show_progress=show_progress,
        )
    )


if __name__ == "__main__":
    cli()
