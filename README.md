# Accommodation Audit System

An async Python tool that audits student quiz accommodations across an institution's Canvas LMS instance — verifying that extra time, extra attempts, and spell-check settings are correctly applied at scale.

## The Problem

At most universities, students with approved accommodations (extra time, additional attempts, spell-check access) rely on instructors to manually configure those settings in Canvas for every quiz, in every course, every term. There is no built-in way to verify this was done correctly.

As an Instructional Technologist and LMS administrator, I discovered three compounding issues:

1. **Manual verification doesn't scale.** Checking one student's accommodations across a single course means navigating multiple Canvas screens per quiz. Across hundreds of courses and thousands of quizzes per term, manual auditing is not feasible.
2. **No existing tooling.** Canvas has no built-in accommodation audit report. The data needed to verify accommodations lives across several different API endpoints — some of which behave differently for "classic" vs. "new" quizzes.
3. **Real compliance gaps.** Students were not always receiving the accommodations they were entitled to, and there was no systematic way to catch it before (or after) the fact.

This tool automates the entire audit: it pulls participant, submission, quiz, and item-level data from Canvas, evaluates each student's accommodation status, and produces a structured Excel report that can span a single quiz, a course, a term, or a single student's full enrollment history.

---

## Architecture

The system follows a layered architecture with clear separation of concerns:

```
┌──────────────────────────────────────────────────────────┐
│                      CLI  (main.py)                       │  Entry point
│  Parses flags, builds dependency graph, runs pipeline     │
└──────────────┬───────────────────────────────────────────┘
               │
       ┌───────▼──────────────────┐
       │   AccommodationService   │  Business logic
       │  audit_term / audit_user │  Evaluates accommodations,
       │  audit_course / quiz     │  builds AuditRow objects
       └───────┬──────────────────┘
               │ reads via
┌──────────────▼──────────────────────────────────────────┐
│            AccommodationRepo  (Protocol)                  │  Data access
│  Abstract interface — callers never see the data source  │
├──────────────┬──────────────────────────────────────────┤
│  CanvasRepo  │              JsonRepo                      │  Implementations
│  (live API)  │         (local JSON, dev/test)             │
└──────┬───────┴──────────────────────────────────────────┘
       │
┌──────▼───────────────────────────────────────────────────┐
│  CanvasClient  ·  NewQuizClient  ·  PersistentCache       │  Transport + caching
│  Auth, pagination, retries, TTL cache (terms/courses/     │
│  quizzes/users), LTI session management via Playwright    │
└──────────────────────────────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────────────┐
│               Enricher  →  Reporter  →  Metrics           │  Output pipeline
│  Human-readable fields (term/user names), Excel report,   │
│  per-run summary (API calls, cache hit rates, timings)    │
└──────────────────────────────────────────────────────────┘
```

**Why two repository implementations?** `JsonRepo` loads data from local JSON files and is essential for development and testing without hitting the Canvas API. `CanvasRepo` is the production implementation. Both conform to the same `AccommodationRepo` protocol, so the business logic layer is completely unaware of the data source.

**Why a separate `CanvasClient`?** The Canvas API has its own pagination scheme (RFC 5988 `Link` headers) and response wrapping conventions. `CanvasClient` owns those concerns — auth, pagination, retries, caching — so that `CanvasRepo` can focus purely on mapping API responses to domain models.

**Why an `Enricher`?** The audit phase answers one question: does this student have this accommodation? Display concerns (what is this user's name, what term is this?) are a separate responsibility. The `Enricher` runs after the audit, resolves human-readable fields from cached API data, and never touches audit evaluation logic.

---

## Accommodation Data Sources

Different accommodation types pull from different API surfaces. The service layer routes automatically:

| Type            | Engine  | Source                             | API Required              |
|-----------------|---------|------------------------------------|---------------------------|
| `EXTRA_TIME`    | new     | Participant enrollment fields      | LTI API (Playwright)      |
| `EXTRA_TIME`    | classic | Submission `extra_time` field      | Canvas REST API           |
| `EXTRA_ATTEMPT` | new     | Submission `extra_attempts` field  | Canvas REST API           |
| `EXTRA_ATTEMPT` | classic | Submission `extra_attempts` field  | Canvas REST API           |
| `SPELL_CHECK`   | new     | Quiz item `interaction_data`       | Canvas REST API           |
| `SPELL_CHECK`   | classic | N/A — not supported in classic     | —                         |

Only `EXTRA_TIME` on new quizzes requires the LTI session. All other types use the standard Canvas REST API.

---

## LTI Session Management

The New Quizzes service uses a Bearer token issued during the LTI launch handshake that cannot be obtained via the Canvas REST API. The system acquires it automatically using Playwright:

1. A headless browser logs into Canvas via the admin backdoor.
2. Playwright navigates to each New Quiz assignment page to trigger the LTI launch.
3. The Bearer token and LTI assignment ID are extracted from the launch response.
4. The token is held in memory for the run; LTI ID mappings are persisted to `.lti_id_cache.json` so Playwright only runs for new (unseen) quizzes on subsequent runs.

The token is account-scoped — one login session serves all quizzes across the institution. If the token expires mid-audit (HTTP 401), the client re-acquires it and retries once automatically.

---

## Caching

Two cache layers minimize Canvas API calls:

**Runtime cache** (`RequestCache`) — in-memory, per-run. Identical HTTP requests within a single audit run are served from memory. Automatically deduplicates calls when the same course or quiz data is needed for multiple accommodation types.

**Persistent cache** (`PersistentCache`) — file-backed JSON under `.cache/`, survives across runs:

| Entity   | TTL     | Rationale                                   |
|----------|---------|---------------------------------------------|
| Terms    | 1 year  | Essentially immutable in Canvas             |
| Courses  | 30 days | Stable within a term                        |
| Quizzes  | 1 day   | Instructors may edit quizzes during a term  |
| Users    | 1 year  | Name/SIS ID changes are rare                |

Submissions, participants, and quiz items are intentionally not cached — they must always reflect the current state of Canvas.

On a warm cache, a full term audit (1,142 courses) that takes ~7 minutes on first run completes in ~2 minutes on subsequent runs, with the remaining time spent on submission fetches that cannot be cached.

---

## Installation

```bash
git clone https://github.com/your-username/accommodation-audit.git
cd accommodation-audit

python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Required for new quiz extra-time audits only
playwright install chromium

cp .example.env .env
# Edit .env with your Canvas credentials
```

### Required Environment Variables

| Variable                | Description                                                              |
|-------------------------|--------------------------------------------------------------------------|
| `CANVAS_BASE_URL`       | Canvas instance URL (e.g. `https://canvas.university.edu`)               |
| `CANVAS_TOKEN`          | Canvas API access token with read permissions                            |
| `CANVAS_ACCOUNT_ID`     | Root or sub-account ID for course listing                                |
| `CANVAS_BACKDOOR_URL`   | Canvas direct login URL (e.g. `https://canvas.university.edu/login/canvas`) |
| `CANVAS_ADMIN_USERNAME` | Admin username for backdoor login                                        |
| `CANVAS_ADMIN_PASSWORD` | Admin password for backdoor login                                        |

---

## Usage

```bash
# Audit an entire term (both engines, all accommodation types)
python main.py audit --term 117

# Audit a single course, classic engine only
python main.py audit --course 12977 --engine classic

# Audit a specific quiz, extra time only
python main.py audit --quiz 48379 --engine new --types extra_time

# Audit a specific student across all their active enrollments
python main.py audit --user 99118

# Audit a specific student in a specific term
python main.py audit --user 99118 --term 117

# Audit a specific student in a specific course
python main.py audit --user 99118 --course 12977

# Force re-fetch of quiz data before auditing
python main.py audit --term 117 --refresh-entity quizzes

# Save to a specific path, disable progress bars (e.g. for cron)
python main.py audit --term 117 --output /reports/sp26.xlsx --no-progress

# View persistent cache statistics
python main.py cache-stats
```

### Scope Rules

Without `--user`: supply exactly one of `--term`, `--course`, or `--quiz`.

With `--user`: `--user` alone is valid (audits all active enrollments), or combine with one optional scope flag:

| Combination              | Behaviour                                                   |
|--------------------------|-------------------------------------------------------------|
| `--user`                 | All quizzes across all active enrollments                   |
| `--user --term`          | All quizzes in that term for that student                   |
| `--user --course`        | All quizzes in that course for that student                 |
| `--user --course --quiz` | One quiz for that student                                   |

User-scoped audits use the Canvas enrollments endpoint to discover only the courses the student is actually enrolled in — avoiding a full-term scan.

---

## Output

Results are written to an Excel workbook (`.xlsx`) with one row per user-accommodation-quiz combination. Rows where `has_accommodation=True` are highlighted green; `False` rows are highlighted yellow.

| Column group | Columns |
|---|---|
| Term    | `enrollment_term_id`, `term_name` |
| Course  | `course_id`, `course_name`, `course_code`, `sis_course_id` |
| Quiz    | `quiz_id`, `quiz_title`, `quiz_due_at`, `quiz_lock_at` |
| Identity | `engine`, `accommodation_type`, `user_id`, `user_name`, `sis_user_id`, `item_id` |
| Result  | `has_accommodation`, `completed`, `attempts_left` |
| Details | `extra_time`, `extra_time_in_seconds`, `timer_multiplier_value`, `extra_attempts`, `spell_check`, `position` |

Every run also prints a summary to the terminal and log file:

```
────────────────────────────────────────────────
Run summary
  Rows:      54,844

  Audit:     2m 6s
  Enrich:    12s
  Write:     7s
  Total:     2m 25s

  API calls: 1,142
  RT cache:  847 hits / 1,989 misses (30%)
  Users:     487 fetched, 0 from cache
  Terms:     served from cache
────────────────────────────────────────────────
```

---

## Project Structure

```
accommodation-audit/
├── .cache/                         ← gitignored; persistent cache files
├── audit/
│   ├── cache/
│   │   ├── lti_id_cache.py         Persistent Canvas → LTI assignment ID mapping
│   │   ├── persistent.py           TTL-based file cache (terms, courses, quizzes, users)
│   │   └── runtime.py              In-memory per-run request cache
│   ├── clients/
│   │   ├── canvas_client.py        Canvas REST client (auth, pagination, retries, metrics)
│   │   ├── new_quiz_client.py      LTI service client with automatic token refresh
│   │   ├── retry.py                @retryable decorator (exponential backoff + full jitter)
│   │   └── session.py              Playwright-based LTI session acquisition
│   ├── models/
│   │   ├── audit.py                AuditRow and AuditRequest dataclasses
│   │   ├── canvas.py               Canvas domain models (Course, Quiz, Submission, …)
│   │   └── parsing.py              Safe type coercion for inconsistent API payloads
│   ├── repos/
│   │   ├── base.py                 AccommodationRepo protocol + AccommodationType enum
│   │   ├── canvas_repo.py          Live Canvas API implementation (with persistent cache)
│   │   └── json_repo.py            Local JSON implementation (development + testing)
│   ├── services/
│   │   └── accommodations.py       Evaluation logic + audit_term/course/quiz/user
│   ├── config.py                   Environment-based settings (python-dotenv)
│   ├── enrichment.py               Post-audit: term names, user display data
│   ├── exceptions.py               Domain exception hierarchy (AuditError tree)
│   ├── logging_setup.py            Rotating file log configuration
│   ├── metrics.py                  RunMetrics collection and formatted summary
│   └── reporting.py                Excel report writer (pandas + xlsxwriter)
├── dumps/                          Sample Canvas API response fixtures (for testing)
├── logs/                           ← gitignored; rotating audit log files
├── scripts/
│   └── test_lti_session.py         Manual LTI session verification script
├── tests/
│   ├── integration/                Live Canvas API tests (require .env credentials)
│   │   ├── conftest.py
│   │   └── test_canvas_repo.py
│   └── unit/                       Business logic tests (no network required)
│       ├── conftest.py
│       ├── test_audit_classic.py
│       ├── test_audit_new.py
│       ├── test_audit_user.py
│       ├── test_cache.py
│       ├── test_concurrency.py
│       ├── test_enrichment.py
│       ├── test_persistent_cache.py
│       ├── test_repo_catalog.py
│       └── test_retry.py
├── main.py                         CLI entry point (click)
├── pyproject.toml
├── requirements.txt
├── README.md
└── TODO.md
```

---

## Testing

```bash
# Unit tests — no credentials required
python -m pytest -m "not integration" -v

# Integration tests — requires .env with Canvas credentials
python -m pytest -m integration -v

# All tests
python -m pytest
```

The unit test suite covers:

- Accommodation evaluation for both quiz engines and all types
- Hierarchical auditing (quiz → course → term → user-scoped)
- Bounded concurrency (semaphore limits, correct results under parallelism)
- Runtime cache (hit/miss counting, key isolation, stats)
- Persistent cache (TTL expiry, invalidation, corrupt file recovery, version mismatch)
- Enricher (term/user lookup, batching, graceful failure, immutability of rows)
- Retry decorator (retryable status codes, full jitter backoff, transport errors)
- Repository catalog (quiz and submission indexing in JsonRepo)

---

## Development History

The project was built iteratively, starting from the narrowest possible scope and broadening only after each layer was solid.

**Phase 1 — Local JSON proof of concept.** One course, one user, one quiz, one accommodation type (extra time on a new quiz). Data from JSON files saved from Canvas API responses. This let the domain model take shape without network concerns.

**Phase 2 — Broadening scope.** All users in a course, then additional accommodation types (extra attempts, classic quiz support, spell-check), then all quizzes in a course, then all courses in a term.

**Phase 3 — Live Canvas API.** `CanvasClient` and `CanvasRepo` provide the production data layer. `NewQuizClient` handles the LTI participants endpoint via Playwright. The same business logic that passed tests against `JsonRepo` worked against the live API with zero changes.

**Phase 4 — Stability.** Exception hierarchy, structured logging, retry decorator with exponential backoff and full jitter, integration test suite.

**Phase 5 — Performance.** Runtime cache eliminates duplicate API calls within a run. Bounded concurrency via semaphores (10 courses in parallel) keeps Canvas rate limits from being exhausted. A term audit of 1,142 courses runs in ~2 minutes.

**Phase 6 — CLI and reporting.** `click`-based CLI with `--term`, `--course`, `--quiz`, `--user`, `--engine`, `--types`, `--refresh-entity` flags. Excel report via `pandas` + `xlsxwriter`. `tqdm` progress bars for course auditing, enrichment, and file writing.

**Phase 7 — Persistent cache.** TTL-based file cache for terms, courses, quizzes, and users. Warm cache cuts repeated term audits from 7 minutes to ~2 minutes.

**Phase 8 — Fuller reporting.** `AuditRow` enriched with course name, quiz title, due/lock dates, attempts left (Bucket 1 — free from already-loaded objects). Term name and user display data added via the `Enricher` post-processing layer with batched parallel API calls and 1-year persistent caching (Bucket 2).

**Phase 9 — User-scoped auditing.** `--user` CLI flag. Uses the Canvas enrollments endpoint to discover only the courses a student is enrolled in, avoiding full-term scans. Supports `user`, `user+term`, `user+course`, and `user+course+quiz` scope combinations.

**Phase 10 — Metrics.** Per-run summary showing API calls made, runtime cache hit rate, retry count, enrichment stats, and phase-by-phase timing. Collected at run end by querying existing objects — no shared mutable state threaded through the call stack.

---

## Key Design Decisions

**Accommodation data source routing.** Different accommodation types pull from different APIs. `EXTRA_TIME` for new quizzes requires the LTI participants endpoint; `EXTRA_ATTEMPT` reads from Canvas submissions. This distinction is handled transparently by the service layer.

**`AuditRow` carries two shapes.** A row can represent a per-user result (extra time, extra attempts) or a per-item result (spell-check on a specific question). A single row type was kept rather than splitting into two for simplicity; this is a candidate for future refactoring.

**Enricher as a separate post-processing layer.** Audit logic and display logic are different concerns. Keeping them separate makes the service layer independently testable and means the enrichment step can be skipped entirely in programmatic contexts where human-readable names aren't needed.

**Semaphore at the course level, not the quiz level.** Early versions gated concurrency per quiz, which flooded Canvas with simultaneous requests across hundreds of courses. Moving the semaphore to gate entire course audits (10 at a time) keeps API request volume predictable.

**Async from the start, even for synchronous repos.** `JsonRepo` methods are `async` even though they do synchronous file reads. This meant the entire service layer was written with `await` from the beginning — when `CanvasRepo` arrived, zero changes were needed upstream.

**Canvas's API inconsistencies are the real complexity.** The business logic for evaluating accommodations is straightforward. The hard part is normalizing API responses: IDs that are sometimes strings and sometimes ints, `course_id` fields absent from some endpoints and parsed from embedded URLs, two completely different API surfaces for classic vs. new quizzes.

---

## Roadmap

See [TODO.md](TODO.md) for the full phased roadmap. Upcoming work includes a DAG-based planner for smarter traversal, SIS name search (fuzzy matching for user lookup), and broader integration test coverage.

---

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
