# Accommodation Audit System

An async Python tool that audits student quiz accommodations across an institution's Canvas LMS instance — verifying that extra time, extra attempts, and spell-check settings are correctly applied at scale.

## The Problem

At most universities, students with approved accommodations (extra time, additional attempts, spell-check access) rely on instructors to manually configure those settings in Canvas for every quiz, in every course, every term. There is no built-in way to verify this was done correctly.

As an Instructional Technologist and LMS administrator, I discovered three compounding issues:

1. **Manual verification doesn't scale.** Checking one student's accommodations across a single course means navigating multiple Canvas screens per quiz. Across hundreds of courses and thousands of quizzes per term, manual auditing is not feasible.
2. **No existing tooling.** Canvas has no built-in accommodation audit report. The data needed to verify accommodations lives across several different API endpoints — some of which behave differently for "classic" vs. "new" quizzes.
3. **Real compliance gaps.** Students were not always receiving the accommodations they were entitled to, and there was no systematic way to catch it before (or after) the fact.

This tool automates the entire audit: it pulls participant, submission, quiz, and item-level data from Canvas, evaluates each student's accommodation status against the configured settings, and produces a structured audit report that can span a single quiz, a course, or an entire term.

## Architecture

The system follows a layered architecture with clear separation of concerns:

```
┌──────────────────────────────────────────────────────────┐
│                    Runner / Planner                       │  Orchestration
│  Plans audit work, coordinates execution across entities  │
└────────────────────────┬─────────────────────────────────┘
                         │ delegates to
┌────────────────────────▼─────────────────────────────────┐
│           AccommodationService  /  Tasks                  │  Business Logic
│  Evaluates accommodations, builds audit rows              │
└────────────────────────┬─────────────────────────────────┘
                         │ reads via
┌────────────────────────▼─────────────────────────────────┐
│              AccommodationRepo (Protocol)                  │  Data Access
│  Abstract interface — callers never know the data source  │
├──────────────┬───────────────────────────────────────────┤
│  CanvasRepo  │            JsonRepo                        │  Implementations
│  (live API)  │       (local JSON files)                   │
└──────┬───────┴───────────────────────────────────────────┘
       │ uses
┌──────▼──────────────────┐     ┌──────────────────────────┐
│     CanvasClient        │     │        Cache              │
│  Auth, pagination,      │     │  Runtime + TTL-based      │
│  response unwrapping    │     │  persistent caching       │
└──────┬──────────────────┘     └──────────────────────────┘
       │
┌──────▼──────────────────┐
│    NewQuizClient        │
│  New Quizzes API surface│
└─────────────────────────┘
```

**Why two repository implementations?** `JsonRepo` loads data from local JSON files and was essential for development and testing without hitting the Canvas API. `CanvasRepo` is the production implementation that calls the live API. Both conform to the same `AccommodationRepo` protocol, so the business logic layer is completely unaware of the data source.

**Why a separate `CanvasClient`?** The Canvas API has its own pagination scheme (RFC 5988 `Link` headers) and response wrapping conventions (e.g., `{"quiz_submissions": [...]}` vs. bare arrays). `CanvasClient` owns those concerns so that `CanvasRepo` can focus on mapping API responses to domain models. `NewQuizClient` handles the separate New Quizzes API surface, which uses different base paths and authentication patterns.

**Why a planner and runner?** As the audit scope grew from a single quiz to entire terms, orchestration became its own concern. The planner determines what work needs to be done (which courses, quizzes, and accommodation types); the runner executes that plan, coordinating the service layer across entities.

## Key Features

- **Multi-engine support.** Canvas has two quiz engines ("classic" and "new") with different API shapes, different submission models, and different accommodation semantics. The system normalizes both into a unified model.
- **Hierarchical auditing.** Audit a single quiz, all quizzes in a course, or all courses in a term — each level composes the one below it.
- **Accommodation types.** Currently evaluates extra time (new and classic), extra attempts (new and classic), and spell-check per question (new quizzes only).
- **Session-aware matching.** For new quizzes, participants and submissions are linked via session IDs extracted from `external_tool_url` query parameters, with a user-ID fallback.
- **Caching layer.** Runtime cache with TTL-based persistence to reduce API load during large audits.
- **Defensive parsing.** Canvas API responses are inconsistent — IDs appear as strings or ints, `course_id` is sometimes absent and must be extracted from embedded URLs. The parsing layer handles all of this gracefully.

## Project Structure

```
accommodation-audit/
├── audit/                          # Main application package
│   ├── cache/                      # Caching layer
│   │   ├── cache.py                #   Cache implementation
│   │   ├── runtime.py              #   In-memory runtime cache
│   │   └── ttl.py                  #   TTL-based persistent cache
│   ├── clients/                    # HTTP layer
│   │   ├── canvas_client.py        #   Core Canvas REST API client (auth, pagination)
│   │   ├── new_quiz_client.py      #   New Quizzes API client
│   │   └── session.py              #   Session/token management
│   ├── models/                     # Domain models
│   │   ├── audit.py                #   AuditRow and AuditRequest dataclasses
│   │   ├── canvas.py               #   Canvas entities (Course, Quiz, Submission, etc.)
│   │   └── parsing.py              #   Safe type coercion and validation helpers
│   ├── planner/                    # Audit planning
│   │   └── planner.py              #   Determines work scope and execution plan
│   ├── repos/                      # Data access layer
│   │   ├── base.py                 #   AccommodationRepo protocol definition
│   │   ├── canvas_repo.py          #   Live Canvas API implementation
│   │   └── json_repo.py            #   Local JSON file implementation (dev/test)
│   ├── runner/                     # Execution orchestration
│   │   └── runner.py               #   Coordinates audit execution across entities
│   ├── services/                   # Business logic
│   │   ├── accommodations.py       #   Core evaluation and audit composition
│   │   └── tasks.py                #   Task definitions for audit operations
│   ├── config.py                   # Environment-based settings
│   └── main.py                     # CLI entry point
├── dumps/                          # Sample Canvas API response data
│   ├── classic_quizzes.json
│   ├── classic_submissions.json
│   ├── courses.json
│   ├── enrollments.json
│   ├── new_items.json
│   ├── new_quizzes.json
│   ├── new_submissions.json
│   ├── participant.json
│   ├── participants.json
│   └── users.json
├── tests/                          # Test suite
│   ├── integration/                #   Integration tests (repo ↔ models)
│   │   ├── conftest.py
│   │   └── test_canvas_repo.py
│   └── unit/                       #   Unit tests (business logic)
│       ├── conftest.py
│       ├── test_audit_classic.py
│       ├── test_audit_logic.py
│       ├── test_audit_new.py
│       ├── test_cache.py
│       ├── test_planner.py
│       └── test_repo_catalog.py
├── .env                            # Local environment config (not committed)
├── .gitignore
├── LICENSE
├── pyproject.toml
├── README.md
├── requirements.txt
└── TODO.md
```

## Example Usage

```python
import asyncio
import httpx
from audit.config import settings
from audit.clients.canvas_client import CanvasClient
from audit.repos.canvas_repo import CanvasRepo
from audit.services.accommodations import AccommodationService
from audit.repos.base import AccommodationType

async def main():
    async with httpx.AsyncClient() as http:
        client = CanvasClient(
            base_url=settings.canvas_base_url,
            token=settings.canvas_token,
            http=http,
        )
        repo = CanvasRepo(client, account_id=settings.canvas_account_id)
        service = AccommodationService(repo)

        # Audit all accommodation types for every quiz in a course
        rows = await service.audit_course(
            course_id=12345,
            engine="new",
        )

        for row in rows:
            if row.has_accommodation:
                print(f"User {row.user_id} — {row.accommodation_type.value}: {row.details}")

asyncio.run(main())
```

## Installation

```bash
# Clone the repository
git clone https://github.com/your-username/accommodation-audit.git
cd accommodation-audit

# Create a virtual environment and install dependencies
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Configure environment variables
cp .env.example .env
# Edit .env with your Canvas instance URL, API token, and account ID
```

### Required Environment Variables

| Variable | Description |
|---|---|
| `CANVAS_BASE_URL` | Your Canvas instance URL (e.g., `https://canvas.university.edu`) |
| `CANVAS_TOKEN` | Canvas API access token with appropriate read permissions |
| `CANVAS_ACCOUNT_ID` | Root or sub-account ID for course listing |

## Development Process

This project was built iteratively, starting from the narrowest possible scope and broadening only after each layer was solid.

**Phase 1 — Proof of concept with local JSON.** The first working version handled one course, one user, one quiz, and one accommodation type (extra time on a new quiz). Data came from JSON files saved from Canvas API responses. This let me focus on getting the domain model right without dealing with network concerns.

**Phase 2 — Broadening scope.** With the foundation in place, I expanded along multiple axes: all users in a course, then additional accommodation types (extra attempts, classic quiz support, spell-check), then all quizzes in a course, then all courses in a term. Each expansion tested the architecture's flexibility — when adding a new accommodation type required changing only the evaluator map and the data-loading logic, I knew the layered design was working.

**Phase 3 — Live API integration.** The `CanvasClient` and `CanvasRepo` provide the production data layer. A separate `NewQuizClient` handles the distinct New Quizzes API surface. The same business logic that passed tests against local JSON works against the live Canvas API with no changes.

**Phase 4 — Orchestration and caching.** As the audit scope grew to full terms (hundreds of courses, thousands of quizzes), two new concerns emerged: planning what work to do and caching API responses. The planner/runner layer coordinates execution, while the cache layer (runtime + TTL-based persistence) keeps API calls within rate limits.

## Tradeoffs and Design Decisions

**`AuditRow` carries two shapes.** A row can represent a per-user result (extra time, extra attempts) or a per-item result (spell-check on a specific quiz question). I chose to keep a single row type rather than splitting into two for simplicity during early development and testing. This is a candidate for refactoring as the system matures.

**First-submission-only.** Students may have multiple submissions for a given quiz. The system currently keeps only the first submission per user. This simplifies the data model and avoids ambiguity in the audit output, but a future version could surface per-attempt detail.

**Eager loading of quiz context.** When auditing a quiz, all participants, submissions, and items are loaded upfront into a `QuizAuditContext` rather than fetched per-user. This trades memory for fewer API calls — the right tradeoff when auditing hundreds of students per quiz.

**Async from the start.** The `JsonRepo` methods are `async` even though they perform synchronous file reads. This was deliberate: it meant the business logic layer was always written with `await`, so switching to the truly async `CanvasRepo` required zero changes upstream.

## Lessons Learned

**Layered architecture pays for itself early.** Defining the `AccommodationRepo` protocol before writing any implementation meant I could build and test all business logic against `JsonRepo` without ever touching the network. When it came time to add `CanvasRepo`, the integration was straightforward — I just had to map API responses to the same models.

**Start narrow, then generalize.** The temptation was to build a system that handled all accommodation types, both quiz engines, and full term-level auditing from day one. Instead, I started with a single accommodation type for a single user on a single quiz. Each expansion was a small, testable step that either validated the architecture or revealed where it needed to flex.

**Async from the start, even when it doesn't matter yet.** Making `JsonRepo` async from day one meant the entire service layer was written with `await` from the beginning. When I introduced the truly async `CanvasRepo`, the transition was seamless — zero changes to business logic. The upfront cost was minimal (a few extra `async def` signatures); the payoff was a clean integration path.

**Canvas's API inconsistencies are the real complexity.** The business logic for evaluating accommodations is straightforward. The hard part was normalizing Canvas's API responses: IDs that are sometimes strings and sometimes ints, `course_id` fields that are absent from some endpoints and must be reverse-parsed from embedded URLs, two completely different API surfaces for "classic" vs. "new" quizzes, and pagination via `Link` headers with wrapped vs. unwrapped response bodies. The parsing and client layers exist almost entirely to absorb this inconsistency so the rest of the system doesn't have to care.

**Orchestration becomes its own layer.** Early on, "audit a term" was just a loop in the service layer. As caching, planning, and error recovery entered the picture, that loop accumulated too many responsibilities. Extracting the planner and runner into their own modules kept the service layer focused on evaluation logic and made the orchestration independently testable.

## Testing

The test suite is organized into unit and integration tests. Unit tests cover the business logic layer using `JsonRepo` as the data source, verifying correct evaluation of each accommodation type across both quiz engines. Integration tests verify the repository-to-model pipeline.

```bash
pytest
```

## Roadmap

See [TODO.md](TODO.md) for the full phased roadmap. Key upcoming work includes LTI API integration with session management, retry logic with backoff, bounded concurrency via semaphores, and structured reporting with progress indicators.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
