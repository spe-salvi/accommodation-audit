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
- [x] Bucket 3 — New quiz item position in spell-check rows (from already-loaded NewQuizItem, zero extra API calls)


## Phase 9 — User-Scoped Auditing

- [x] `--user` CLI flag (Canvas user ID)
- [x] Enrollment-based traversal (`list_enrollments` → narrow to enrolled courses only)
- [x] Scope combinations: `--user`, `--user --term`, `--user --course`, `--user --course --quiz`
- [x] `get_course_by_id` for course context resolution without term constraint
- [x] Spell-check rows correctly excluded from user-scoped results


## Phase 10 — Metrics

- [x] `requests_made` and `retries_fired` counters on `CanvasClient`
- [x] `users_fetched`, `terms_fetched` counters on `Enricher`
- [x] `hits` and `misses` counters on `PersistentCache` (accurate cross-run cache hit tracking)
- [x] `RunMetrics` dataclass + `collect_metrics()` + `format_metrics()`
- [x] Per-phase timing (audit, enrich, write) in run summary
- [x] Persistent and runtime cache hit rates in summary
- [x] Unit tests for metrics layer


## Phase 11 — Planner and DAG Traversal

- [x] `AuditScope` dataclass (engine, types, term/course/quiz/user IDs and query fields)
- [x] `AuditStep` and `StepKind` (COURSE, USER, QUIZ)
- [x] `AuditPlan.execute()` — concurrent step execution gated by semaphore
- [x] `AuditPlanner.build()` — resolves scope into deduplicated step list
  - [x] Term scope → list_courses → COURSE steps
  - [x] Course scope → single COURSE or USER step
  - [x] Quiz scope → single QUIZ step
  - [x] User scope → list_enrollments → USER steps (deduplicated by course)
  - [x] User + term scope → enrollment filtered by term
- [x] Multi-user deduplication — shared courses audited once, `user_ids` set for filtering
- [x] Semaphore gating moved from service to planner's `_execute_step`
- [x] Unit tests — planner traversal, deduplication, error cases


## Phase 12 — Fuzzy Search

- [x] `--term`, `--course`, `--quiz`, `--user` accept names as well as Canvas IDs
- [x] `Resolver` class with four resolution methods:
  - [x] `resolve_term` — token-based local filter against cached terms list
  - [x] `resolve_course` — delegates to Canvas `search_term` API (requires term context)
  - [x] `resolve_quiz` — token-based local filter against course quiz list (requires course context)
  - [x] `resolve_user` — delegates to Canvas `search_term` API (handles name + SIS user ID)
- [x] `ResolveError` raised with helpful message on no matches
- [x] Multiple matches audited (fan-out, no disambiguation prompt)
- [x] `canvas_repo.search_courses()` and `search_users()` methods
- [x] `_parse_id_or_query()` helper in `main.py` routes int strings to IDs, names to queries
- [x] Context rules enforced in CLI: course query requires term; quiz query requires course
- [x] Unit tests — resolver methods, ResolveError, CLI helpers


## Phase 13 — Integration and Test Coverage

- [x] Unit tests — metrics layer (Phase 10)
- [x] Unit tests — planner/DAG (Phase 11)
- [x] Unit tests — resolver and CLI helpers (Phase 12)
- [ ] Integration tests — enrichment (term names, user display data)
- [ ] Integration tests — persistent cache (live Canvas data)
- [ ] Integration tests — user-scoped auditing with live enrollments


## Phase 14 — Dynamic Rate Adaptation

- [ ] Observe 429 frequency during a run
- [ ] Dynamically reduce concurrency (lower semaphore limit) when rate limits are hit
- [ ] Restore concurrency gradually when requests succeed
- [ ] Surface adaptation events in metrics summary


## Phase 15 — React Web Frontend

- [x] FastAPI backend wrapping the audit service
  - [x] `POST /api/audit` — starts background job, returns job_id
  - [x] `GET /api/audit/{id}/stream` — SSE progress stream
  - [x] `GET /api/audit/{id}/rows` — completed rows as JSON
  - [x] `GET /api/audit/{id}/download` — Excel file download
  - [x] `GET /api/cache/stats` — persistent cache statistics
  - [x] `DELETE /api/cache/{entity}` — cache invalidation
- [x] React frontend (Vite)
  - [x] Audit configuration form (scope inputs accept ID or name, engine, types)
  - [x] Real-time SSE progress bar with phase labels
  - [x] Run metrics summary on completion
  - [x] Filterable/paginated results table
  - [x] Excel report download
  - [x] Cache stats dashboard with invalidation buttons
- [x] Single-service deployment on Render (FastAPI serves built React static files)
- [x] Persistent disk on Render for `.cache/` directory
- [ ] Microsoft Entra ID (Azure AD) SSO authentication with MFA


## Ongoing

- [ ] Keep README and TODO in sync with completed work
- [x] Add `.gitignore` entries for `.cache/`, `logs/`, `*.xlsx`, `.env`
