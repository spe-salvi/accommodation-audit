"""
Run metrics collection and formatting for the accommodation audit system.

Metrics are collected at the end of each run by querying the objects
that already own the relevant data — no shared mutable state is threaded
through the call stack.

Data sources
------------
- CanvasClient:       requests_made, retries_fired
- RequestCache:       hits, misses  (runtime in-memory cache)
- PersistentCache:    hits, misses  (file-backed TTL cache)
- Enricher:           users_fetched, terms_fetched

Usage
-----
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
    click.echo(format_metrics(metrics))
    logger.info("Run metrics: %s", metrics)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RunMetrics:
    """
    Snapshot of metrics collected at the end of a single audit run.

    All counts reflect the current run only — not cumulative totals
    across multiple runs.
    """

    # --- Row output ---
    row_count: int = 0

    # --- Phase timings (seconds) ---
    audit_elapsed: float = 0.0
    enrich_elapsed: float = 0.0
    write_elapsed: float = 0.0

    # --- Canvas API ---
    api_requests_made: int = 0     # actual HTTP requests (cache misses)
    api_retries_fired: int = 0     # total retry attempts across all requests

    # --- Runtime cache (in-memory, per-run) ---
    runtime_cache_hits: int = 0
    runtime_cache_misses: int = 0

    # --- Persistent cache (file-backed, cross-run) ---
    persistent_cache_hits: int = 0
    persistent_cache_misses: int = 0

    # --- Enricher ---
    users_fetched: int = 0         # user profiles resolved (hit or miss)
    terms_fetched: int = 0         # terms list resolved (hit or miss)

    @property
    def total_elapsed(self) -> float:
        return self.audit_elapsed + self.enrich_elapsed + self.write_elapsed

    @property
    def runtime_cache_hit_rate(self) -> float:
        total = self.runtime_cache_hits + self.runtime_cache_misses
        return (self.runtime_cache_hits / total * 100) if total else 0.0

    @property
    def persistent_cache_hit_rate(self) -> float:
        total = self.persistent_cache_hits + self.persistent_cache_misses
        return (self.persistent_cache_hits / total * 100) if total else 0.0


def collect_metrics(
    *,
    client,
    runtime_cache,
    persistent_cache,
    enricher,
    audit_elapsed: float,
    enrich_elapsed: float,
    write_elapsed: float,
    row_count: int,
) -> RunMetrics:
    """
    Build a ``RunMetrics`` snapshot from the objects that own the data.

    Parameters
    ----------
    client:
        ``CanvasClient`` instance — provides ``requests_made`` and
        ``retries_fired``.
    runtime_cache:
        ``RequestCache`` instance — provides ``hits`` and ``misses``.
    persistent_cache:
        ``PersistentCache`` instance — provides ``hits`` and ``misses``
        accumulated across all ``get()`` / ``get_list()`` calls this run.
    enricher:
        ``Enricher`` instance — provides ``users_fetched`` and
        ``terms_fetched``.
    audit_elapsed, enrich_elapsed, write_elapsed:
        Phase durations in seconds from ``time.perf_counter()``.
    row_count:
        Number of ``AuditRow`` objects in the final output.
    """
    return RunMetrics(
        row_count=row_count,
        audit_elapsed=audit_elapsed,
        enrich_elapsed=enrich_elapsed,
        write_elapsed=write_elapsed,
        api_requests_made=getattr(client,           "requests_made", 0),
        api_retries_fired=getattr(client,           "retries_fired",  0),
        runtime_cache_hits=getattr(runtime_cache,   "hits",           0),
        runtime_cache_misses=getattr(runtime_cache, "misses",         0),
        persistent_cache_hits=getattr(persistent_cache,   "hits",   0),
        persistent_cache_misses=getattr(persistent_cache, "misses", 0),
        users_fetched=getattr(enricher, "users_fetched", 0),
        terms_fetched=getattr(enricher, "terms_fetched", 0),
    )


def format_metrics(m: RunMetrics) -> str:
    """
    Format a ``RunMetrics`` snapshot as a human-readable summary string.

    Printed to the CLI at the end of a run and written to the log at
    INFO level.
    """
    def _fmt(seconds: float) -> str:
        s = int(seconds)
        return f"{s // 60}m {s % 60}s" if s >= 60 else f"{s}s"

    lines = [
        "─" * 48,
        "Run summary",
        f"  Rows:      {m.row_count:,}",
        "",
        f"  Audit:     {_fmt(m.audit_elapsed)}",
        f"  Enrich:    {_fmt(m.enrich_elapsed)}",
        f"  Write:     {_fmt(m.write_elapsed)}",
        f"  Total:     {_fmt(m.total_elapsed)}",
        "",
        f"  API calls: {m.api_requests_made:,}",
    ]

    if m.api_retries_fired:
        lines.append(f"  Retries:   {m.api_retries_fired:,}")

    # Persistent cache line (cross-run, most valuable)
    p_total = m.persistent_cache_hits + m.persistent_cache_misses
    if p_total:
        lines.append(
            f"  P-cache:   {m.persistent_cache_hits:,} hits / "
            f"{m.persistent_cache_misses:,} misses "
            f"({m.persistent_cache_hit_rate:.0f}%)"
        )

    # Runtime cache line (within a single run)
    r_total = m.runtime_cache_hits + m.runtime_cache_misses
    if r_total:
        lines.append(
            f"  RT cache:  {m.runtime_cache_hits:,} hits / "
            f"{m.runtime_cache_misses:,} misses "
            f"({m.runtime_cache_hit_rate:.0f}%)"
        )

    # Enrichment summary
    if m.users_fetched:
        lines.append(f"  Users:     {m.users_fetched:,} resolved")
    if m.terms_fetched:
        lines.append(f"  Terms:     {m.terms_fetched:,} resolved")

    lines.append("─" * 48)
    return "\n".join(lines)
