# Project Roadmap

## Phase 1 — MVP: Local JSON Proof of Concept

- [x] Narrow scope: one submission, one course, one user, one quiz, one accommodation type (extra time, new quiz)
  - [x] Models (Participant and AuditRow dataclasses)
  - [x] Data access layer (participants only)
  - [x] Business logic layer
- [x] Broaden scope
  - [x] All users in course
  - [x] Extra Attempt (new quiz) — Submission model + data access + business logic
  - [x] Extra Attempt (classic quiz) — update Submission model, fix validation bug
  - [x] Extra Time (classic quiz)
  - [x] Spell Check (new quiz)
  - [x] All quizzes in course
  - [x] All courses in term
  - [x] All terms


## Phase 2 — Live Canvas API

- [x] One endpoint
- [x] Broaden to all models
  - [x] Submissions
  - [x] Users
  - [x] Quizzes (new + classic)
  - [x] New Quiz Items
  - [x] Courses
  - [x] Terms
- [x] Error handling


## Phase 3 — LTI API

- [x] Grab token, create session
- [x] Authenticated call
- [x] Token/session reuse
- [x] Refresh on expiry
- [x] Error handling


## Phase 4 — Stability

- [x] Clean data access layer boundaries
- [x] Finalize business layer
- [x] Confirm normalization of models
- [x] Logging (models, data access, business)
- [x] Retry calls with exponential backoff + full jitter
- [x] Exception hierarchy (AuditError tree)
- [x] Basic testing
  - [x] Unit tests — business layer
  - [x] Integration tests — models and data access
  - [x] Small end-to-end tests


## Phase 5 — Performance

- [x] Bounded concurrency via semaphores (course-level, default 10)
- [x] Runtime cache (in-memory, per-run, eliminates duplicate requests)
- [x] Persistent cache (TTL-based file cache — terms, courses, quizzes, users)


## Phase 6 — CLI and Reporting

- [x] `click`-based CLI with `--term`, `--course`, `--quiz`, `--engine`, `--types`, `--output`, `--debug`, `--no-progress`, `--refresh-entity` flags
- [x] Excel report via pandas + xlsxwriter (conditional formatting, frozen header, auto-width)
- [x] `tqdm` progress bars (course auditing, enrichment, file write)
- [x] Per-run timing (audit, enrich, write phases)


## Phase 7 — Persistent Cache

- [x] TTL-based JSON file cache under `.cache/`
- [x] Entity types: terms (1yr), courses (30d), quizzes (1d), users (1yr)
- [x] `--refresh-entity` CLI flag for manual cache invalidation
- [x] `cache-stats` CLI subcommand
- [x] Paginated terms endpoint fix (get_paginated_json)


## Phase 8 — Fuller Reporting

- [x] Bucket 1 enrichment — course name, quiz title, due/lock dates, attempts left, enrollment_term_id (free from already-loaded objects, zero extra API calls)
- [x] Bucket 2 enrichment — term name and user display data via Enricher post-processing layer
  - [x] Term name (terms list, 1-year cache)
  - [x] User name + SIS user ID (batched parallel fetches, 1-year cache)
- [x] Enricher progress bar (combined bar: terms + users, suppressed on warm cache)
- [ ] Bucket 3 — New quiz item enrichment (position/question number per item)


## Phase 9 — User-Scoped Auditing

- [x] `--user` CLI flag (Canvas user ID)
- [x] Enrollment-based traversal (`list_enrollments` → narrow to enrolled courses only)
- [x] Scope combinations: `--user`, `--user --term`, `--user --course`, `--user --course --quiz`
- [x] `get_course_by_id` for course context resolution without term constraint
- [x] Spell-check rows correctly excluded from user-scoped results


## Phase 10 — Metrics

- [x] `requests_made` and `retries_fired` counters on `CanvasClient`
- [x] `users_fetched`, `users_from_cache`, `terms_fetched`, `terms_from_cache` on `Enricher`
- [x] `RunMetrics` dataclass + `collect_metrics()` + `format_metrics()`
- [x] Per-phase timing (audit, enrich, write) in run summary
- [x] Runtime cache hit rate in summary
- [ ] Unit tests for metrics layer


## Phase 11 — Planner and DAG Traversal

- [ ] Design traversal DAG (input scope → optimal API path)
  - [ ] `--user` alone: enrollments → courses → quizzes
  - [ ] `--user --term`: enrollments filtered by term → courses → quizzes
  - [ ] `--term`: account courses → quizzes (current behavior, already optimal)
  - [ ] `--course` / `--quiz`: direct fetch (current behavior, already optimal)
- [ ] Planner selects traversal strategy based on input scope
- [ ] Avoid redundant course fetches when same course appears in multiple user enrollments


## Phase 12 — Fuzzy Search

- [ ] Search by any human-readable field without knowing the Canvas ID
- [ ] Searchable fields:
  - [ ] Term name
  - [ ] Course name
  - [ ] Course code
  - [ ] SIS course ID
  - [ ] Quiz title
  - [ ] User name
  - [ ] SIS user ID
- [ ] Fuzzy matching strategy (exact → prefix → substring → threshold similarity)
- [ ] CLI integration: `--term`, `--course`, `--quiz`, `--user` accept names as well as IDs
- [ ] Disambiguation prompt when multiple matches found
- [ ] Scope rules for name-based input (e.g. course name requires term context)


## Phase 13 — Integration and Test Coverage

- [ ] Unit tests — metrics layer
- [ ] Unit tests — planner/DAG
- [ ] Integration tests — enrichment (term names, user display data)
- [ ] Integration tests — persistent cache (live Canvas data)
- [ ] Integration tests — user-scoped auditing


## Phase 14 — Dynamic Rate Adaptation

- [ ] Observe 429 frequency during a run
- [ ] Dynamically reduce concurrency (lower semaphore limit) when rate limits are hit
- [ ] Restore concurrency gradually when requests succeed
- [ ] Surface adaptation events in metrics summary


## Phase 15 — React Web Frontend

- [ ] Design REST API layer (FastAPI or similar) wrapping the audit service
- [ ] Authentication (admin-only, token-based)
- [ ] React frontend
  - [ ] Audit configuration form (scope, engine, types, user)
  - [ ] Fuzzy search inputs for term/course/quiz/user
  - [ ] Real-time progress display (WebSocket or SSE)
  - [ ] Run history and report download
  - [ ] Cache stats dashboard
- [ ] Hosting on Render (free tier)
- [ ] Background job queue for long-running term audits


## Ongoing

- [ ] Keep README and TODO in sync with completed work
- [ ] Add `.gitignore` entries for `.cache/`, `logs/`, `*.xlsx`, `.env`
