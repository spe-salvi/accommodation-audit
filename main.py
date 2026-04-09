"""
CLI entry point for the accommodation audit system.

Usage examples
--------------
Audit by Canvas ID (original behaviour, unchanged):
    python main.py audit --term 117
    python main.py audit --course 12977 --engine classic
    python main.py audit --user 99118

Audit by name (fuzzy search):
    python main.py audit --term "Spring 2026"
    python main.py audit --term "Spring" --course "Moral Principles"
    python main.py audit --user "McCarthy"
    python main.py audit --user "2621872"           # SIS user ID
    python main.py audit --term 117 --course "CHM-115"
    python main.py audit --course 12977 --quiz "Midterm"

Combined name + ID:
    python main.py audit --term 117 --user "Smith"  # all Smiths in term

Scope rules
-----------
Without --user: supply exactly one of --term, --course, or --quiz.
With --user:    --user alone is valid, or combine with one optional
                scope flag (--term, --course, or --quiz).

Course name/code search requires --term (Canvas course names are not
globally unique across all terms).

Quiz title search requires --course (Canvas has no account-level quiz search).
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
from audit.metrics import collect_metrics, format_metrics
from audit.planner import AuditPlanner, AuditScope
from audit.reporting import write_xlsx
from audit.repos.base import AccommodationType
from audit.repos.canvas_repo import CanvasRepo
from audit.resolver import ResolveError
from audit.services.accommodations import AccommodationService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_output_path() -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return Path(f"audit_{ts}.xlsx")


def _parse_id_or_query(value: str | None) -> tuple[int | None, str | None]:
    """
    Split a CLI value into (int_id, query_string).

    If the value looks like a plain integer, return (int, None).
    Otherwise treat it as a name query and return (None, str).
    Returns (None, None) if value is None.
    """
    if value is None:
        return None, None
    try:
        return int(value), None
    except (ValueError, TypeError):
        return None, value


def _build_scope(
    *,
    engine: str,
    types: list[AccommodationType],
    term: str | None,
    course: str | None,
    quiz: str | None,
    user: str | None,
) -> AuditScope:
    """
    Parse CLI flag values into an AuditScope, routing each value to
    either an ID field or a query field depending on whether it parses
    as an integer.
    """
    term_id,   term_query   = _parse_id_or_query(term)
    course_id, course_query = _parse_id_or_query(course)
    quiz_id,   quiz_query   = _parse_id_or_query(quiz)
    user_id,   user_query   = _parse_id_or_query(user)

    return AuditScope(
        engine=engine,
        accommodation_types=types,
        term_id=term_id,
        term_query=term_query,
        course_id=course_id,
        course_query=course_query,
        quiz_id=quiz_id,
        quiz_query=quiz_query,
        user_id=user_id,
        user_query=user_query,
    )


def _scope_desc(scope: AuditScope) -> str:
    """Human-readable description of the scope for the CLI announce line."""
    parts = []
    if scope.user_id is not None:
        parts.append(f"user={scope.user_id}")
    if scope.user_query is not None:
        parts.append(f"user={scope.user_query!r}")
    if scope.term_id is not None:
        parts.append(f"term={scope.term_id}")
    if scope.term_query is not None:
        parts.append(f"term={scope.term_query!r}")
    if scope.course_id is not None:
        parts.append(f"course={scope.course_id}")
    if scope.course_query is not None:
        parts.append(f"course={scope.course_query!r}")
    if scope.quiz_id is not None:
        parts.append(f"quiz={scope.quiz_id}")
    if scope.quiz_query is not None:
        parts.append(f"quiz={scope.quiz_query!r}")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Audit pipeline
# ---------------------------------------------------------------------------

async def _run_audit(
    *,
    scope_template: AuditScope,
    engines: list[str],
    output_path: Path,
    show_progress: bool,
    persistent_cache: PersistentCache,
) -> None:
    """
    Build the full dependency chain, plan, audit, enrich, and write.

    Pipeline
    --------
    1. Plan   — AuditPlanner resolves scope (including name queries) into steps
    2. Audit  — AccommodationService evaluates each step
    3. Enrich — Enricher fills in term/user display data
    4. Write  — write_xlsx produces the Excel report
    5. Metrics — collect and display run summary
    """
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

        # --- Phase 1 + 2: Plan and Audit ---
        audit_start = time.perf_counter()

        from dataclasses import replace as _dc_replace
        scopes = [
            _dc_replace(scope_template, engine=eng)
            for eng in engines
        ]

        planner = AuditPlanner(repo)

        try:
            plans = await asyncio.gather(*[planner.build(s) for s in scopes])
        except ResolveError as exc:
            raise click.ClickException(str(exc)) from exc

        engine_results = await asyncio.gather(*[
            plan.execute(svc, semaphore=svc._semaphore, show_progress=show_progress)
            for plan in plans
        ])

        rows = [row for engine_rows in engine_results for row in engine_rows]
        audit_elapsed = time.perf_counter() - audit_start

        # --- Phase 3: Enrich ---
        enrich_start = time.perf_counter()
        rows = await enricher.enrich(rows)
        enrich_elapsed = time.perf_counter() - enrich_start

    # --- Phase 4: Write ---
    write_start = time.perf_counter()
    write_xlsx(rows, output_path, show_progress=show_progress)
    write_elapsed = time.perf_counter() - write_start

    # --- Phase 5: Metrics ---
    metrics = collect_metrics(
        client=client,
        runtime_cache=runtime_cache,
        persistent_cache=persistent_cache,
        enricher=enricher,
        audit_elapsed=audit_elapsed,
        enrich_elapsed=enrich_elapsed,
        write_elapsed=write_elapsed,
        row_count=len(rows),
    )
    summary = format_metrics(metrics)
    click.echo(f"\nReport written to {output_path}")
    click.echo(summary)
    logger.info("Run complete. Output: %s\n%s", output_path, summary)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.group()
def cli() -> None:
    """Accommodation audit tool for Canvas LMS."""


@cli.command()
@click.option("--term",   "term",   type=str, default=None,
              help="Term ID or name (e.g. 117 or 'Spring 2026').")
@click.option("--course", "course", type=str, default=None,
              help="Course ID, name, code, or SIS ID. Requires --term for name search.")
@click.option("--quiz",   "quiz",   type=str, default=None,
              help="Quiz ID or title. Requires --course for title search.")
@click.option("--user",   "user",   type=str, default=None,
              help="User ID, name, or SIS user ID.")
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
    term: str | None,
    course: str | None,
    quiz: str | None,
    user: str | None,
    engine: str,
    types: tuple[str, ...],
    output: Path | None,
    refresh_entity: str | None,
    debug: bool,
    no_progress: bool,
) -> None:
    """
    Run an accommodation audit and write results to Excel.

    --term, --course, --quiz, and --user each accept either a Canvas ID
    (integer) or a name/search string. When a name matches multiple
    entities, all matches are audited.

    Without --user: supply exactly one of --term, --course, or --quiz.
    With --user:    --user alone is valid, or combine with one optional
                    scope flag (--term, --course, or --quiz).

    \b
    Examples:
        python main.py audit --term 117
        python main.py audit --term "Spring 2026"
        python main.py audit --term "Spring" --course "Moral Principles"
        python main.py audit --user "McCarthy"
        python main.py audit --user 99118 --term 117
        python main.py audit --term 117 --refresh-entity quizzes
    """
    # --- Logging ---
    log_level = logging.DEBUG if debug else logging.WARNING
    log_file = setup_logging(level=log_level)
    if debug:
        click.echo(f"Debug logging → {log_file}")

    # --- Validate scope ---
    provided = {k: v for k, v in {"term": term, "course": course, "quiz": quiz}.items()
                if v is not None}

    if user is not None:
        if len(provided) > 1:
            raise click.UsageError(
                f"--user can be combined with at most one scope flag — got: "
                f"{', '.join(f'--{k}' for k in provided)}."
            )
        if quiz is not None and course is None:
            raise click.UsageError("--quiz requires --course when used with --user.")
    else:
        if len(provided) == 0:
            raise click.UsageError(
                "Supply exactly one scope flag: --term, --course, or --quiz. "
                "Or use --user to audit a specific student."
            )
        if len(provided) > 1:
            raise click.UsageError(
                f"Supply exactly one scope flag — got: "
                f"{', '.join(f'--{k}' for k in provided)}."
            )

    # --- Validate name-search context requirements ---
    _, course_q = _parse_id_or_query(course)
    _, quiz_q   = _parse_id_or_query(quiz)
    if course_q is not None and term is None:
        raise click.UsageError(
            f"Course name search ({course!r}) requires --term to scope the search. "
            f"Add --term <id or name>."
        )
    if quiz_q is not None and course is None:
        raise click.UsageError(
            f"Quiz title search ({quiz!r}) requires --course. "
            f"Add --course <id or name>."
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

    # Build a template scope (engine overridden per-engine inside _run_audit)
    scope_template = _build_scope(
        engine=engines[0],
        types=accommodation_types,
        term=term,
        course=course,
        quiz=quiz,
        user=user,
    )

    click.echo(
        f"Auditing {_scope_desc(scope_template)} | "
        f"engine={engine} | "
        f"types={list(types or _TYPE_MAP.keys())}"
    )

    asyncio.run(_run_audit(
        scope_template=scope_template,
        engines=engines,
        output_path=output_path,
        show_progress=show_progress,
        persistent_cache=persistent_cache,
    ))


@cli.command("cache-stats")
def cache_stats() -> None:
    """Show persistent cache statistics (entry counts, TTLs, expired entries)."""
    cache = PersistentCache(_CACHE_DIR)
    stats = cache.stats()

    click.echo(f"\nCache directory: {_CACHE_DIR.resolve()}\n")
    click.echo(f"{'Entity':<10} {'Total':>7} {'Valid':>7} {'Expired':>9} {'TTL':>10}")
    click.echo("-" * 48)
    for entity, info in stats.items():
        ttl_hours = info["ttl_hours"]
        ttl_str = (
            f"{int(ttl_hours)}h" if ttl_hours < 24
            else f"{int(ttl_hours // 24)}d"
        )
        click.echo(
            f"{entity:<10} {info['total']:>7} {info['valid']:>7} "
            f"{info['expired']:>9} {ttl_str:>10}"
        )
    click.echo()


if __name__ == "__main__":
    cli()
