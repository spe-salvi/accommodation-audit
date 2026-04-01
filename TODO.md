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

- [X] One Endpoint
- [X] Broaden Endpoints to all Models
  - [X] Submissions
  - [X] Users
  - [X] Quizzes
    - [X] New
    - [X] Classic
  - [X] New Quiz Items
  - [X] Courses
  - [X] Terms
- [X] Error Handling

## Phase 3 — From LTI API

- [X] Grab Token, Create Session
- [X] Authenticated Call
- [X] Token/Session Reuse
- [X] Refresh on Expiry
- [X] Error Handling

## Phase 4 — Stability

- [X] Clean Data Access Layer Boundaries
- [X] Finalize Business Layer
- [X] Confirm Normalization of Models
- [X] Logging
  - [X] Models
  - [X] Data Access
  - [X] Business
- [X] Retry Calls
- [X] Exceptions
- [X] Basic Testing
  - [X] Unit Tests - Business Layer
  - [X] Integration - Models and Data Access
  - [X] Small End-to-End Test(s)

## Phase 5 — Optimization

- Phase 5.1 - Async

- [X] Bounded Concurrency
- [X] Research/Implement Semaphores

- Phase 5.2 - Runtime Cache

- [X]

- Phase 5.3 - Persistent Cache (TTL)

- [X]

- Phase 5.4 - Planner and DAG Traversal

- [ ]

- Phase 5.5 - Metric Logging

- [ ] Request Count
- [ ] Cache Hits
- [ ] Latency
- [ ] Rate-Limits
- [ ] Authorization Refresh Count, Limit
- [ ] Retries

- (TBD) Phase 5.6 - Dynamic Rate Adaptation

- [ ]

## Phase 6 - GUI

- [ ] 
- [ ] 
- [ ] 
- [ ] 

## Phase 7 — Reporting

- [ ] Report Presentation
- [X] Progress Indicators
- [ ] Finalize Documentation
