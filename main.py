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

Audit all quizzes for a specific user across all enrollments:
    python main.py audit --user 99118

Audit a specific user in a specific term:
    python main.py audit --user 99118 --term 117

Audit a specific user in a specific course:
    python main.py audit --user 99118 --course 12977

Force a cache refresh for quizzes before auditing:
    python main.py audit --term 117 --refresh-entity quizzes

Inspect cache stats without running an audit:
    python main.py cache-stats

Scope rules
-----------
Without --user: supply exactly one of --term, --course, or --quiz.
With --user:    --user alone is valid, or combine with one of
                --term, --course, or --quiz.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import click
import httpx

from audit.cache.persistent import CacheEntity, PersistentCache
from audit.cache.runtime import RequestCache
from audit.clients.canvas_client import CanvasClient
from audit.config import settings
from audit.enrichment import Enricher
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

_ENTITY_MAP: dict[str, CacheEntity] = {
    "terms":   CacheEntity.TERM,
    "courses": CacheEntity.COURSE,
    "quizzes": CacheEntity.QUIZ,
    "users":   CacheEntity.USER,
}

_ALL_ENGINES = ["new", "classic"]
_CACHE_DIR = Path(".cache")


def _default_output_path() -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return Path(f"audit_{ts}.xlsx")


def _fmt_elapsed(seconds: float) -> str:
    seconds = int(seconds)
    if seconds >= 60:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds}s"


async def _run_audit(
    *,
    user_id: int | None,
    term_id: int | None,
    course_id: int | None,
    quiz_id: int | None,
    engines: list[str],
    types: list[AccommodationType],
    output_path: Path,
    show_progress: bool,
    persistent_cache: PersistentCache,
) -> None:
    """
    Build the full dependency chain, run the audit, enrich, and write.

    Pipeline:
        1. Audit  — AccommodationService produces raw AuditRow list
        2. Enrich — Enricher fills in term_name, user_name, sis_user_id
        3. Write  — write_xlsx produces the Excel report
    """
    audit_start = time.perf_counter()
    runtime_cache = RequestCache()

    async with httpx.AsyncClient() as http:
        client = CanvasClient(
            base_url=settings.canvas_base_url,
            token=settings.canvas_token,
            http=http,
            cache=runtime_cache,
        )
        repo = CanvasRepo(
            client,
            account_id=settings.canvas_account_id,
            persistent_cache=persistent_cache,
        )
        svc = AccommodationService(repo)
        enricher = Enricher(repo=repo, show_progress=show_progress)

        tasks = []
        for eng in engines:
            if user_id is not None:
                # User-scoped audit: use enrollments to narrow courses
                tasks.append(svc.audit_user(
                    user_id=user_id,
                    engine=eng,
                    accommodation_types=types,
                    term_id=term_id,
                    course_id=course_id,
                    quiz_id=quiz_id,
                    show_progress=show_progress,
                ))
            elif term_id is not None:
                tasks.append(svc.audit_term(
                    term_id=term_id,
                    engine=eng,
                    accommodation_types=types,
                    show_progress=show_progress,
                ))
            elif course_id is not None:
                tasks.append(svc.audit_course(
                    course_id=course_id,
                    engine=eng,
                    accommodation_types=types,
                ))
            elif quiz_id is not None:
                tasks.append(svc.audit_quiz(
                    course_id=quiz_id,   # placeholder until quiz-scoped DAG
                    quiz_id=quiz_id,
                    engine=eng,
                    accommodation_types=types,
                ))

        results = await asyncio.gather(*tasks)
        rows = [row for engine_rows in results for row in engine_rows]

        # Enrich with term names and user display data
        rows = await enricher.enrich(rows)

    audit_elapsed = _fmt_elapsed(time.perf_counter() - audit_start)
    runtime_cache.log_stats()

    summary = (
        f"Audit complete — {len(rows):,} rows across "
        f"{len(engines)} engine(s) in {audit_elapsed}."
    )
    click.echo(summary)
    logger.info(summary)

    write_start = time.perf_counter()
    write_xlsx(rows, output_path, show_progress=show_progress)
    write_elapsed = _fmt_elapsed(time.perf_counter() - write_start)

    write_summary = f"Report written to {output_path} in {write_elapsed}."
    click.echo(write_summary)
    logger.info(write_summary)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.group()
def cli() -> None:
    """Accommodation audit tool for Canvas LMS."""


@cli.command()
@click.option("--term",   "term_id",   type=int, default=None,
              help="Canvas enrollment term ID.")
@click.option("--course", "course_id", type=int, default=None,
              help="Canvas course ID.")
@click.option("--quiz",   "quiz_id",   type=int, default=None,
              help="Canvas quiz or assignment ID.")
@click.option("--user",   "user_id",   type=int, default=None,
              help="Canvas user ID. Can be combined with --term, --course, or --quiz.")
@click.option(
    "--engine",
    type=click.Choice(["new", "classic", "all"], case_sensitive=False),
    default="all", show_default=True,
    help="Quiz engine to audit. 'all' runs both concurrently.",
)
@click.option(
    "--types",
    multiple=True,
    type=click.Choice(list(_TYPE_MAP.keys()), case_sensitive=False),
    default=list(_TYPE_MAP.keys()), show_default=True,
    help="Accommodation type(s) to evaluate. Repeatable.",
)
@click.option(
    "--output",
    type=click.Path(dir_okay=False, writable=True, path_type=Path),
    default=None,
    help="Output .xlsx path. Defaults to audit_<timestamp>.xlsx.",
)
@click.option(
    "--refresh-entity",
    "refresh_entity",
    type=click.Choice(list(_ENTITY_MAP.keys()), case_sensitive=False),
    default=None,
    help="Invalidate a cache entity type before running (forces re-fetch).",
)
@click.option("--debug",       is_flag=True, default=False,
              help="Enable DEBUG log level.")
@click.option("--no-progress", is_flag=True, default=False,
              help="Disable progress bars.")
def audit(
    term_id: int | None,
    course_id: int | None,
    quiz_id: int | None,
    user_id: int | None,
    engine: str,
    types: tuple[str, ...],
    output: Path | None,
    refresh_entity: str | None,
    debug: bool,
    no_progress: bool,
) -> None:
    """
    Run an accommodation audit and write results to Excel.

    Without --user: supply exactly one of --term, --course, or --quiz.
    With --user:    --user alone is valid, or combine with one optional
                    scope flag (--term, --course, or --quiz).

    \b
    Examples:
        python main.py audit --term 117
        python main.py audit --course 12977 --engine classic
        python main.py audit --user 99118
        python main.py audit --user 99118 --term 117
        python main.py audit --user 99118 --course 12977
        python main.py audit --term 117 --refresh-entity quizzes
    """
    log_level = logging.DEBUG if debug else logging.WARNING
    log_file = setup_logging(level=log_level)
    if debug:
        click.echo(f"Debug logging → {log_file}")

    # --- Validate scope ---
    scope_args = {
        "term":   term_id,
        "course": course_id,
        "quiz":   quiz_id,
    }
    provided_scopes = {k: v for k, v in scope_args.items() if v is not None}

    if user_id is not None:
        # With --user: zero or one scope modifier is valid
        if len(provided_scopes) > 1:
            raise click.UsageError(
                f"--user can be combined with at most one scope flag — got: "
                f"{', '.join(f'--{k}' for k in provided_scopes)}."
            )
        if quiz_id is not None and course_id is None:
            raise click.UsageError(
                "--quiz requires --course when used with --user."
            )
    else:
        # Without --user: exactly one scope required
        if len(provided_scopes) == 0:
            raise click.UsageError(
                "Supply exactly one scope flag: --term, --course, or --quiz. "
                "Or use --user to audit a specific student."
            )
        if len(provided_scopes) > 1:
            raise click.UsageError(
                f"Supply exactly one scope flag — got: "
                f"{', '.join(f'--{k}' for k in provided_scopes)}."
            )

    # --- Persistent cache ---
    persistent_cache = PersistentCache(_CACHE_DIR)

    if refresh_entity is not None:
        entity = _ENTITY_MAP[refresh_entity]
        count = persistent_cache.invalidate(entity)
        click.echo(f"Cache invalidated: {refresh_entity} ({count} entries reset).")

    # --- Resolve options ---
    engines = _ALL_ENGINES if engine == "all" else [engine]
    accommodation_types = [_TYPE_MAP[t] for t in (types or _TYPE_MAP.keys())]
    output_path = output or _default_output_path()
    show_progress = not no_progress

    # --- Announce ---
    scope_desc = (
        f"user={user_id}"
        + (f" term={term_id}" if term_id else "")
        + (f" course={course_id}" if course_id else "")
        + (f" quiz={quiz_id}" if quiz_id else "")
        if user_id is not None
        else next(f"{k}={v}" for k, v in provided_scopes.items())
    )
    click.echo(
        f"Auditing {scope_desc} | "
        f"engine={engine} | "
        f"types={list(types or _TYPE_MAP.keys())} | "
        f"output={output_path}"
    )

    asyncio.run(
        _run_audit(
            user_id=user_id,
            term_id=term_id,
            course_id=course_id,
            quiz_id=quiz_id,
            engines=engines,
            types=accommodation_types,
            output_path=output_path,
            show_progress=show_progress,
            persistent_cache=persistent_cache,
        )
    )


@cli.command("cache-stats")
def cache_stats() -> None:
    """Show persistent cache statistics (entry counts, TTLs, expired entries)."""
    cache = PersistentCache(_CACHE_DIR)
    stats = cache.stats()

    click.echo(f"\nCache directory: {_CACHE_DIR.resolve()}\n")
    click.echo(
        f"{'Entity':<10} {'Total':>7} {'Valid':>7} {'Expired':>9} {'TTL':>10}"
    )
    click.echo("-" * 48)
    for entity, info in stats.items():
        ttl_hours = info["ttl_hours"]
        ttl_str = (
            f"{int(ttl_hours)}h"
            if ttl_hours < 24
            else f"{int(ttl_hours // 24)}d"
        )
        click.echo(
            f"{entity:<10} {info['total']:>7} {info['valid']:>7} "
            f"{info['expired']:>9} {ttl_str:>10}"
        )
    click.echo()


if __name__ == "__main__":
    cli()
