"""
Unit tests for audit.metrics.

Tests cover:
  - RunMetrics dataclass: default values, computed properties
  - collect_metrics(): reads correct attributes from each source object,
    handles missing attributes gracefully (getattr default), combines
    values correctly
  - format_metrics(): correct sections appear/absent based on data,
    timing formatting (seconds vs minutes), comma-formatted numbers,
    retries line only shown when non-zero, cache lines only shown when
    there is data
"""

import pytest

from audit.metrics import RunMetrics, collect_metrics, format_metrics


# ---------------------------------------------------------------------------
# Fake source objects
# ---------------------------------------------------------------------------

class FakeClient:
    def __init__(self, requests_made=0, retries_fired=0):
        self.requests_made = requests_made
        self.retries_fired = retries_fired


class FakeRuntimeCache:
    def __init__(self, hits=0, misses=0):
        self.hits = hits
        self.misses = misses


class FakePersistentCache:
    def __init__(self, hits=0, misses=0):
        self.hits = hits
        self.misses = misses


class FakeEnricher:
    def __init__(self, users_fetched=0, terms_fetched=0):
        self.users_fetched = users_fetched
        self.terms_fetched = terms_fetched


def _collect(**overrides) -> RunMetrics:
    """Build a RunMetrics with sensible defaults, overridable per-test."""
    defaults = dict(
        client=FakeClient(),
        runtime_cache=FakeRuntimeCache(),
        persistent_cache=FakePersistentCache(),
        enricher=FakeEnricher(),
        audit_elapsed=0.0,
        enrich_elapsed=0.0,
        write_elapsed=0.0,
        row_count=0,
    )
    defaults.update(overrides)
    return collect_metrics(**defaults)


# ---------------------------------------------------------------------------
# RunMetrics defaults
# ---------------------------------------------------------------------------

def test_default_run_metrics_all_zero():
    m = RunMetrics()
    assert m.row_count == 0
    assert m.api_requests_made == 0
    assert m.api_retries_fired == 0
    assert m.runtime_cache_hits == 0
    assert m.runtime_cache_misses == 0
    assert m.persistent_cache_hits == 0
    assert m.persistent_cache_misses == 0
    assert m.users_fetched == 0
    assert m.terms_fetched == 0
    assert m.audit_elapsed == 0.0
    assert m.enrich_elapsed == 0.0
    assert m.write_elapsed == 0.0


# ---------------------------------------------------------------------------
# RunMetrics computed properties
# ---------------------------------------------------------------------------

def test_total_elapsed_sums_phases():
    m = RunMetrics(audit_elapsed=10.0, enrich_elapsed=5.0, write_elapsed=3.0)
    assert m.total_elapsed == pytest.approx(18.0)


def test_total_elapsed_all_zero():
    assert RunMetrics().total_elapsed == pytest.approx(0.0)


def test_runtime_cache_hit_rate_correct():
    m = RunMetrics(runtime_cache_hits=75, runtime_cache_misses=25)
    assert m.runtime_cache_hit_rate == pytest.approx(75.0)


def test_runtime_cache_hit_rate_zero_when_no_requests():
    assert RunMetrics().runtime_cache_hit_rate == pytest.approx(0.0)


def test_runtime_cache_hit_rate_100_percent():
    m = RunMetrics(runtime_cache_hits=100, runtime_cache_misses=0)
    assert m.runtime_cache_hit_rate == pytest.approx(100.0)


def test_persistent_cache_hit_rate_correct():
    m = RunMetrics(persistent_cache_hits=3000, persistent_cache_misses=1000)
    assert m.persistent_cache_hit_rate == pytest.approx(75.0)


def test_persistent_cache_hit_rate_zero_when_no_requests():
    assert RunMetrics().persistent_cache_hit_rate == pytest.approx(0.0)


def test_persistent_cache_hit_rate_100_percent():
    m = RunMetrics(persistent_cache_hits=500, persistent_cache_misses=0)
    assert m.persistent_cache_hit_rate == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# collect_metrics — reads from source objects
# ---------------------------------------------------------------------------

def test_collect_reads_api_requests_from_client():
    m = _collect(client=FakeClient(requests_made=1142, retries_fired=3))
    assert m.api_requests_made == 1142
    assert m.api_retries_fired == 3


def test_collect_reads_runtime_cache_counters():
    m = _collect(runtime_cache=FakeRuntimeCache(hits=847, misses=295))
    assert m.runtime_cache_hits == 847
    assert m.runtime_cache_misses == 295


def test_collect_reads_persistent_cache_counters():
    m = _collect(persistent_cache=FakePersistentCache(hits=3000, misses=4))
    assert m.persistent_cache_hits == 3000
    assert m.persistent_cache_misses == 4


def test_collect_reads_enricher_counters():
    m = _collect(enricher=FakeEnricher(users_fetched=487, terms_fetched=1))
    assert m.users_fetched == 487
    assert m.terms_fetched == 1


def test_collect_reads_timings():
    m = _collect(audit_elapsed=126.4, enrich_elapsed=12.1, write_elapsed=7.3)
    assert m.audit_elapsed == pytest.approx(126.4)
    assert m.enrich_elapsed == pytest.approx(12.1)
    assert m.write_elapsed == pytest.approx(7.3)


def test_collect_reads_row_count():
    m = _collect(row_count=54_844)
    assert m.row_count == 54_844


def test_collect_handles_missing_attribute_gracefully():
    """collect_metrics uses getattr with defaults — objects missing attributes
    should not raise."""
    class Minimal:
        pass

    m = collect_metrics(
        client=Minimal(),
        runtime_cache=Minimal(),
        persistent_cache=Minimal(),
        enricher=Minimal(),
        audit_elapsed=1.0,
        enrich_elapsed=0.0,
        write_elapsed=0.0,
        row_count=0,
    )
    assert m.api_requests_made == 0
    assert m.api_retries_fired == 0
    assert m.runtime_cache_hits == 0
    assert m.persistent_cache_hits == 0
    assert m.users_fetched == 0


def test_collect_all_zeros_produces_zero_metrics():
    m = _collect()
    assert m.row_count == 0
    assert m.api_requests_made == 0
    assert m.total_elapsed == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# format_metrics — always-present sections
# ---------------------------------------------------------------------------

def test_format_contains_separator_lines():
    output = format_metrics(RunMetrics())
    lines = output.splitlines()
    assert lines[0].startswith("─")
    assert lines[-1].startswith("─")


def test_format_contains_run_summary_header():
    assert "Run summary" in format_metrics(RunMetrics())


def test_format_contains_row_count():
    output = format_metrics(RunMetrics(row_count=54_844))
    assert "54,844" in output


def test_format_contains_api_calls():
    output = format_metrics(RunMetrics(api_requests_made=1142))
    assert "1,142" in output


def test_format_contains_all_phase_timings():
    m = RunMetrics(audit_elapsed=126.0, enrich_elapsed=12.0, write_elapsed=7.0)
    output = format_metrics(m)
    assert "Audit:" in output
    assert "Enrich:" in output
    assert "Write:" in output
    assert "Total:" in output


# ---------------------------------------------------------------------------
# format_metrics — timing formatting
# ---------------------------------------------------------------------------

def test_format_timing_seconds_only():
    m = RunMetrics(audit_elapsed=45.0)
    assert "45s" in format_metrics(m)


def test_format_timing_minutes_and_seconds():
    m = RunMetrics(audit_elapsed=126.0)
    output = format_metrics(m)
    assert "2m 6s" in output


def test_format_timing_exactly_one_minute():
    m = RunMetrics(audit_elapsed=60.0)
    assert "1m 0s" in format_metrics(m)


def test_format_timing_zero_seconds():
    m = RunMetrics(audit_elapsed=0.0)
    assert "0s" in format_metrics(m)


def test_format_total_timing_correct():
    m = RunMetrics(audit_elapsed=126.0, enrich_elapsed=12.0, write_elapsed=7.0)
    output = format_metrics(m)
    # Total = 145s = 2m 25s
    assert "2m 25s" in output


# ---------------------------------------------------------------------------
# format_metrics — conditional sections
# ---------------------------------------------------------------------------

def test_format_retries_line_absent_when_zero():
    output = format_metrics(RunMetrics(api_retries_fired=0))
    assert "Retries" not in output


def test_format_retries_line_present_when_nonzero():
    output = format_metrics(RunMetrics(api_retries_fired=3))
    assert "Retries" in output
    assert "3" in output


def test_format_persistent_cache_line_absent_when_no_activity():
    output = format_metrics(RunMetrics(
        persistent_cache_hits=0, persistent_cache_misses=0,
    ))
    assert "P-cache" not in output


def test_format_persistent_cache_line_present_when_hits():
    output = format_metrics(RunMetrics(
        persistent_cache_hits=3000, persistent_cache_misses=4,
    ))
    assert "P-cache" in output
    assert "3,000" in output
    assert "100%" in output


def test_format_persistent_cache_line_present_when_only_misses():
    output = format_metrics(RunMetrics(
        persistent_cache_hits=0, persistent_cache_misses=100,
    ))
    assert "P-cache" in output
    assert "0%" in output


def test_format_runtime_cache_line_absent_when_no_activity():
    output = format_metrics(RunMetrics(
        runtime_cache_hits=0, runtime_cache_misses=0,
    ))
    assert "RT cache" not in output


def test_format_runtime_cache_line_present_when_active():
    output = format_metrics(RunMetrics(
        runtime_cache_hits=847, runtime_cache_misses=295,
    ))
    assert "RT cache" in output
    assert "847" in output


def test_format_users_line_absent_when_zero():
    assert "Users" not in format_metrics(RunMetrics(users_fetched=0))


def test_format_users_line_present_when_nonzero():
    output = format_metrics(RunMetrics(users_fetched=487))
    assert "Users" in output
    assert "487" in output


def test_format_terms_line_absent_when_zero():
    assert "Terms" not in format_metrics(RunMetrics(terms_fetched=0))


def test_format_terms_line_present_when_nonzero():
    output = format_metrics(RunMetrics(terms_fetched=1))
    assert "Terms" in output


# ---------------------------------------------------------------------------
# format_metrics — full warm-cache run (realistic scenario)
# ---------------------------------------------------------------------------

def test_format_warm_cache_run():
    """Second run: most data served from persistent cache, no retries."""
    m = RunMetrics(
        row_count=58_174,
        audit_elapsed=119.0,
        enrich_elapsed=0.5,
        write_elapsed=15.0,
        api_requests_made=1335,
        api_retries_fired=0,
        runtime_cache_hits=0,
        runtime_cache_misses=1335,
        persistent_cache_hits=3004,
        persistent_cache_misses=0,
        users_fetched=2911,
        terms_fetched=1,
    )
    output = format_metrics(m)

    assert "58,174" in output
    assert "1,335" in output
    assert "P-cache" in output
    assert "3,004" in output
    assert "100%" in output
    assert "Retries" not in output
    assert "Users" in output
    assert "Terms" in output


def test_format_cold_run():
    """First run: nothing cached, retries present."""
    m = RunMetrics(
        row_count=58_168,
        audit_elapsed=845.0,
        enrich_elapsed=12.0,
        write_elapsed=15.0,
        api_requests_made=6514,
        api_retries_fired=2,
        runtime_cache_hits=0,
        runtime_cache_misses=4145,
        persistent_cache_hits=0,
        persistent_cache_misses=4145,
        users_fetched=2911,
        terms_fetched=1,
    )
    output = format_metrics(m)

    assert "58,168" in output
    assert "6,514" in output
    assert "Retries" in output
    assert "2" in output
    assert "P-cache" in output
    assert "0%" in output
    assert "14m 5s" in output
