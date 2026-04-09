# Accommodation Audit System

An async Python tool and web application that audits student quiz accommodations across an institution's Canvas LMS instance — verifying that extra time, extra attempts, and spell-check settings are correctly applied at scale.

## Acknowledgments

This project was developed with the assistance of Claude Sonnet 4.6 for ideation, debugging, and code refinement.  
All architecture, design decisions, and final implementations were reviewed and validated by the author.

## The Problem

At most universities, students with approved accommodations (extra time, additional attempts, spell-check access) rely on instructors to manually configure those settings in Canvas for every quiz, in every course, every term. There is no built-in way to verify this was done correctly.

As an Instructional Technologist and LMS administrator, I discovered three compounding issues:

1. **Manual verification doesn't scale.** Checking one student's accommodations across a single course means navigating multiple Canvas screens per quiz. Across hundreds of courses and thousands of quizzes per term, manual auditing is not feasible.
2. **No existing tooling.** Canvas has no built-in accommodation audit report. The data needed to verify accommodations lives across several different API endpoints — some of which behave differently for "classic" vs. "new" quizzes.
3. **Real compliance gaps.** Students were not always receiving the accommodations they were entitled to, and there was no systematic way to catch it before (or after) the fact.

This tool automates the entire audit: it pulls participant, submission, quiz, and item-level data from Canvas, evaluates each student's accommodation status, and produces a structured Excel report. It is accessible as both a command-line tool and a hosted web application.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│           CLI (main.py)          │      Web API (api/main.py)         │  Entry points
│  click flags → AuditScope        │  FastAPI + SSE → AuditScope        │
└──────────────────┬───────────────┴──────────────────┬────────────────┘
                   │                                   │
           ┌───────▼───────────────────────────────────▼──────┐
           │              AuditPlanner (audit/planner.py)      │  Orchestration
           │  Resolves scope → AuditPlan → executes steps      │
           │  Fuzzy search via Resolver (audit/resolver.py)    │
           └───────────────────┬───────────────────────────────┘
                               │ delegates to
                       ┌───────▼──────────────────┐
                       │   AccommodationService   │  Business logic
                       │  audit_course / quiz     │  Evaluates accommodations,
                       │  (evaluation primitives) │  builds AuditRow objects
                       └───────┬──────────────────┘
                               │ reads via
        ┌──────────────────────▼──────────────────────────────┐
        │            AccommodationRepo  (Protocol)              │  Data access
        ├──────────────────┬──────────────────────────────────┤
        │    CanvasRepo    │            JsonRepo               │  Implementations
        │    (live API)    │       (local JSON, dev/test)      │
        └──────┬───────────┴──────────────────────────────────┘
               │
        ┌──────▼───────────────────────────────────────────────┐
        │  CanvasClient · NewQuizClient · PersistentCache       │  Transport + caching
        └──────────────────────────────────────────────────────┘
               │
        ┌──────▼───────────────────────────────────────────────┐
        │               Enricher → Reporter → Metrics           │  Output pipeline
        └──────────────────────────────────────────────────────┘
```

**Why two entry points?** The CLI and web API both import from the same `audit/` package. The web layer adds SSE-based progress streaming and a React frontend; the CLI remains fully functional for scripted or scheduled use.

**Why a Planner?** `AuditPlanner` owns traversal — what to fetch and in what order — leaving `AccommodationService` focused purely on evaluation. The planner resolves a scope (term/course/quiz/user, by ID or name) into a deduplicated list of `AuditStep` objects and executes them concurrently.

**Why a Resolver?** Fuzzy search requires resolution logic separate from the planner. `Resolver` routes to Canvas's `search_term` API for courses and users, and filters locally for terms and quiz titles (no Canvas endpoint exists for those).

**Why an Enricher?** The audit phase answers one question: does this student have this accommodation? Display concerns (term name, user name) are a separate responsibility handled after the fact with aggressive caching.

---

## Accommodation Data Sources

| Type            | Engine  | Source                             | API Required              |
|-----------------|---------|------------------------------------|---------------------------|
| `EXTRA_TIME`    | new     | Participant enrollment fields      | LTI API (Playwright)      |
| `EXTRA_TIME`    | classic | Submission `extra_time` field      | Canvas REST API           |
| `EXTRA_ATTEMPT` | new     | Submission `extra_attempts` field  | Canvas REST API           |
| `EXTRA_ATTEMPT` | classic | Submission `extra_attempts` field  | Canvas REST API           |
| `SPELL_CHECK`   | new     | Quiz item `interaction_data`       | Canvas REST API           |
| `SPELL_CHECK`   | classic | N/A — not supported in classic     | —                         |

Only `EXTRA_TIME` on new quizzes requires the LTI session.

---

## LTI Session Management

The New Quizzes service uses a Bearer token issued during the LTI launch handshake that cannot be obtained via the Canvas REST API. The system acquires it automatically using Playwright:

1. A headless browser logs into Canvas via the admin backdoor.
2. Playwright navigates to each New Quiz assignment page to trigger the LTI launch.
3. The Bearer token and LTI assignment ID are extracted from the launch response.
4. The token is held in memory; LTI ID mappings are persisted to `.lti_id_cache.json` so Playwright only runs for new (unseen) quizzes on subsequent runs.

The token is account-scoped — one login session serves all quizzes across the institution. HTTP 401 mid-audit triggers automatic token refresh and one retry.

---

## Caching

**Runtime cache** (`RequestCache`) — in-memory, per-run. Deduplicates identical HTTP requests within a single run.

**Persistent cache** (`PersistentCache`) — file-backed JSON under `.cache/`, survives across runs:

| Entity   | TTL     | Rationale                                   |
|----------|---------|---------------------------------------------|
| Terms    | 1 year  | Essentially immutable in Canvas             |
| Courses  | 30 days | Stable within a term                        |
| Quizzes  | 1 day   | Instructors may edit quizzes during a term  |
| Users    | 1 year  | Name/SIS ID changes are rare                |

Submissions, participants, and quiz items are intentionally not cached. On a warm cache, a full term audit (1,142 courses) completes in ~2 minutes vs. ~7 minutes cold.

---

## Installation

```bash
git clone https://github.com/your-username/accommodation-audit.git
cd accommodation-audit

python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-api.txt   # web app only

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

### CLI

```bash
# Audit by Canvas ID
python main.py audit --term 117
python main.py audit --course 12977 --engine classic
python main.py audit --user 99118

# Audit by name (fuzzy search)
python main.py audit --term "Spring 2026"
python main.py audit --term "Spring" --course "Moral Principles"
python main.py audit --user "McCarthy"
python main.py audit --user "2621872"           # SIS user ID

# Combined scope
python main.py audit --user 99118 --term 117
python main.py audit --course 12977 --quiz "Midterm"

# Cache and output options
python main.py audit --term 117 --refresh-entity quizzes
python main.py audit --term 117 --output /reports/sp26.xlsx --no-progress
python main.py cache-stats
```

`--term`, `--course`, `--quiz`, and `--user` each accept either a Canvas integer ID or a name/search string. When a name matches multiple entities, all matches are audited. Course name search requires `--term`; quiz title search requires `--course`.

### Web App

```bash
# Backend
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

# Frontend dev server (separate terminal)
cd frontend && npm install && npm run dev
# → http://localhost:5173
```

The web app provides a form-based audit interface with real-time SSE progress, a filterable results table, Excel download, and a cache management dashboard. In production the built React frontend is served as static files from FastAPI.

---

## Output

Results are written to an Excel workbook with one row per user-accommodation-quiz combination. Green rows have the accommodation applied; yellow rows are missing it.

| Column group | Columns |
|---|---|
| Term     | `enrollment_term_id`, `term_name` |
| Course   | `course_id`, `course_name`, `course_code`, `sis_course_id` |
| Quiz     | `quiz_id`, `quiz_title`, `quiz_due_at`, `quiz_lock_at` |
| Identity | `engine`, `accommodation_type`, `user_id`, `user_name`, `sis_user_id`, `item_id` |
| Result   | `has_accommodation`, `completed`, `attempts_left` |
| Details  | `extra_time`, `extra_time_in_seconds`, `timer_multiplier_value`, `extra_attempts`, `spell_check`, `position` |

Every run prints a summary:

```
────────────────────────────────────────────────
Run summary
  Rows:      58,174

  Audit:     1m 59s
  Enrich:    0s
  Write:     15s
  Total:     2m 16s

  API calls: 5,179
  P-cache:   3,004 hits / 0 misses (100%)
  RT cache:  847 hits / 1,989 misses (30%)
  Users:     2,911 resolved
  Terms:     1 resolved
────────────────────────────────────────────────
```

---

## Project Structure

```
accommodation-audit/
├── .cache/                         ← gitignored; persistent cache files
├── api/                            Web API layer (FastAPI)
│   ├── main.py                     FastAPI app + static file serving
│   ├── models.py                   Pydantic request/response models
│   ├── jobs.py                     In-memory job store + background task runner
│   └── routes/
│       ├── audit.py                POST /audit, SSE stream, rows, download
│       └── cache.py                Cache stats and invalidation endpoints
├── audit/                          Core Python package (shared by CLI and web)
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
│   │   └── accommodations.py       Evaluation logic + audit_course/quiz/user wrappers
│   ├── config.py                   Environment-based settings (python-dotenv)
│   ├── enrichment.py               Post-audit: term names, user display data
│   ├── exceptions.py               Domain exception hierarchy (AuditError tree)
│   ├── logging_setup.py            Rotating file log configuration
│   ├── metrics.py                  RunMetrics collection and formatted summary
│   ├── planner.py                  AuditScope → AuditPlan → execution
│   ├── reporting.py                Excel report writer (pandas + xlsxwriter)
│   └── resolver.py                 Fuzzy name resolution → Canvas IDs
├── frontend/                       React web frontend (Vite)
│   ├── index.html
│   ├── package.json
│   ├── vite.config.js
│   └── src/
│       ├── App.jsx + App.module.css
│       ├── index.css               Design tokens
│       ├── hooks/useAudit.js       SSE job lifecycle hook
│       └── components/
│           ├── AuditForm.jsx       Scope inputs (ID or name)
│           ├── ProgressView.jsx    Live progress + metrics summary
│           ├── ResultsTable.jsx    Filterable results + Excel download
│           └── CacheStats.jsx      Cache dashboard with invalidation
├── dumps/                          Sample Canvas API response fixtures
├── logs/                           ← gitignored; rotating log files
├── scripts/
│   └── test_lti_session.py         Manual LTI session verification
├── tests/
│   ├── integration/
│   │   ├── conftest.py
│   │   └── test_canvas_repo.py
│   └── unit/
│       ├── conftest.py
│       ├── test_audit_classic.py
│       ├── test_audit_new.py
│       ├── test_audit_user.py
│       ├── test_cache.py
│       ├── test_cli_helpers.py
│       ├── test_concurrency.py
│       ├── test_enrichment.py
│       ├── test_metrics.py
│       ├── test_persistent_cache.py
│       ├── test_planner.py
│       ├── test_repo_catalog.py
│       ├── test_resolver.py
│       └── test_retry.py
├── main.py                         CLI entry point (click)
├── render.yaml                     Render deployment configuration
├── pyproject.toml
├── requirements.txt
├── requirements-api.txt            FastAPI + uvicorn (web app only)
├── README.md
└── TODO.md
```

---

## Deployment (Render)

The web app is configured for single-service deployment on Render's free tier via `render.yaml`. The build step installs Python dependencies and builds the React frontend; FastAPI serves both the API and the built static files from a single dyno.

A 1GB persistent disk is mounted at `.cache/` so the TTL cache survives redeploys and restarts. Set the six Canvas environment variables in the Render dashboard before deploying.

---

## Testing

```bash
# Unit tests — no credentials required
python -m pytest -m "not integration" -v

# Integration tests — requires .env with Canvas credentials
python -m pytest -m integration -v
```

The unit test suite (222 tests) covers accommodation evaluation, planner traversal and deduplication, resolver fuzzy matching, CLI input parsing, concurrency, both cache layers, metrics, enrichment, and the retry decorator.

---

## Development History

**Phase 1 — Local JSON proof of concept.** One course, one user, one quiz, one accommodation type. Data from local JSON files let the domain model take shape without network concerns.

**Phase 2 — Broadening scope.** All users, all quizzes, all courses, all accommodation types across both quiz engines.

**Phase 3 — Live Canvas API.** `CanvasClient`, `CanvasRepo`, and `NewQuizClient` (LTI via Playwright). Same business logic, zero upstream changes.

**Phase 4 — Stability.** Exception hierarchy, logging, retry with exponential backoff and full jitter, integration tests.

**Phase 5 — Performance.** Runtime cache, bounded concurrency via semaphores (10 courses in parallel), persistent TTL cache.

**Phase 6 — CLI and reporting.** `click` CLI, Excel report via pandas + xlsxwriter, `tqdm` progress bars.

**Phase 7 — Persistent cache.** TTL file cache for terms/courses/quizzes/users. Warm cache cuts term audits from 7 minutes to ~2 minutes.

**Phase 8 — Fuller reporting.** Bucket 1 enrichment (free from already-loaded objects). Bucket 2 enrichment via `Enricher` (term names, user display data, batched parallel fetches). Bucket 3 (quiz item position in spell-check rows).

**Phase 9 — User-scoped auditing.** `--user` flag with enrollment-based traversal across all scope combinations.

**Phase 10 — Metrics.** Per-run summary with API call counts, persistent and runtime cache hit rates, retries, and phase timings.

**Phase 11 — Planner.** `AuditPlanner` extracted from service layer. Owns traversal and deduplication. Multi-user queries deduplicate shared courses to avoid redundant fetches.

**Phase 12 — Fuzzy search.** All four scope flags accept names as well as IDs. `Resolver` delegates course/user search to Canvas's `search_term` API; resolves terms and quizzes locally with token-based matching.

**Phase 15 — React web frontend.** FastAPI backend with SSE progress streaming, in-memory background jobs, and Excel download. React + Vite frontend with audit form, live progress, filterable results table, and cache dashboard. Single-service deployment on Render.

---

## Key Design Decisions

**Planner owns traversal; service owns evaluation.** Extracting orchestration from the service layer gave each layer a single clear responsibility.

**Multi-user queries deduplicate at the course level.** The planner builds a `{course_id: set[user_id]}` map so each unique course is audited exactly once regardless of how many matching users are enrolled in it.

**Resolver delegates to Canvas for courses and users; filters locally for terms and quizzes.** Canvas has no term or quiz search endpoint, so those filter locally against already-cached lists.

**`AuditRow` carries two shapes.** Per-user results and per-item spell-check results share one dataclass for simplicity. A candidate for future refactoring.

**Enricher as a separate post-processing layer.** Separating display concerns from audit evaluation makes both independently testable and lets the enrichment step be skipped in programmatic contexts.

**Semaphore at the course level.** Course-level gating (10 at a time) keeps Canvas API request volume predictable without sacrificing throughput.

**Async from the start.** `JsonRepo` methods are `async` despite synchronous file reads, so the service layer always used `await` — `CanvasRepo` slotted in with zero upstream changes.

**Canvas's API inconsistencies are the real complexity.** IDs arrive as strings or ints. `course_id` is absent from some endpoints and parsed from embedded URLs. Classic and new quizzes have completely different API surfaces and accommodation semantics.

---

## Roadmap

See [TODO.md](TODO.md) for the full phased roadmap.

---

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
