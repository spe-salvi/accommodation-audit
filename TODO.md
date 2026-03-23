# Project Roadmap

## Phase 1 — MVP: From Local JSON

- [x] Narrow Scope One Submission (One Course, One User, One Quiz) and One Accommodation Type (Extra Time, New Quiz)
  - [x] Models (Participant and AuditRow dataclasses)
  - [x] Data Access Layer (for participants only)
  - [x] Business Logic Layer
- [x] Broaden Scope
  - [x] All Users in Course
  - [x] Additional Accommodation Type (Extra Attempt, New Quiz)
    - [x] Submission model
    - [x] Data Access Layer (for submissions)
    - [x] Update Business Logic Layer
  - [x] Additional Accommodation Type (Extra Attempt, Classic Quiz)
    - [x] Update Submission model
    - [x] Fix bug: Not validating input against data returned
  - [x] Additional Accommodation Type (Extra Time, Classic Quiz)
  - [x] Additional Accommodation Type (Spell Check, New Quiz)
  - [x] Additional Accommodation Type (Split Test)
  - [x] All Quizzes in Course
  - [x] All Courses in Term
  - [x] All Terms

## Phase 2 — From Canvas API

- [ ] One Endpoint
- [ ] Broaden Endpoints to all Models
  - [ ] Submissions
  - [ ] Users
  - [ ] Quizzes
    - [ ] New
    - [ ] Classic
  - [ ] New Quiz Items
  - [ ] Courses
  - [ ] Terms
- [ ] Error Handling

## Phase 3 — From LTI API

- [ ] Grab Token, Create Session
- [ ] Authenticated Call
- [ ] Token/Session Reuse
- [ ] Refresh on Expiry
- [ ] Error Handling

## Phase 4 — Stability

- [ ] Clean Data Access Layer Boundaries
- [ ] Finalize Business Layer
- [ ] Confirm Normalization of Models
- [ ] Logging
  - [ ] Models
  - [ ] Data Access
  - [ ] Business
- [ ] Retry Calls
- [ ] Exceptions
- [ ] Basic Testing
  - [ ] Unit Tests - Business Layer
  - [ ] Integration - Models and Data Access
  - [ ] Small End-to-End Test(s)

## Phase 5 — Optimization

- Phase 5.1 - Runtime Cache

- [ ]

- Phase 5.2 - Persistent Cache (TTL)

- [ ]

- Phase 5.3 - Metric Logging

- [ ] Request Count
- [ ] Cache Hits
- [ ] Latency
- [ ] Rate-Limits
- [ ] Authorization Refresh Count, Limit
- [ ] Retries

- Phase 5.4 - Async

- [ ] Bounded Concurrency
- [ ] Research/Implement Semaphores

- Phase 5.5 - Dynamic Rate Adaptation

- [ ] Research Best Approach

- Phase 5.6 - Planner and DAG Traversal

- [ ] Research Best Approach

## Phase 6 — Reporting

- [ ] Report Presentation
- [ ] Progress Indicators
- [ ] Finalize Documentation
