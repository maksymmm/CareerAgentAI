# Autonomous Implementation Plan

This document is the prioritized execution queue for CareerAgentAI. Codex should take the highest-priority unfinished item that is not blocked and deliver it as one coherent, verified unit.

## 1. Crash-safe idempotency for consequential external actions

Status: **IMPLEMENTED — VERIFICATION BLOCKED**

Goal: guarantee that restart/retry behavior cannot silently duplicate a real-world action such as an application submission or recruiter message.

Acceptance criteria:
- [x] Introduce an explicit external-action operation model with stable operation/idempotency ID.
- [x] Add repository abstraction for external-action operations.
- [x] Add SQLite persistence with parameterized SQL and appropriate indexes.
- [x] Model at least: prepared, in_progress, succeeded, failed, reconciliation_required.
- [x] Persist intent before adapter execution.
- [x] Persist terminal outcome after execution.
- [x] On restart, do not blindly repeat ambiguous in-flight operations.
- [x] Add tests for duplicate invocation, crash/restart recovery, failure, and malformed persisted state.
- [ ] Maintain >=90% total coverage.
- [x] Document the contract.

Delivered: a provider-neutral adapter boundary, mandatory explicit approval at execution,
atomic SQLite state transitions, terminal duplicate suppression, and safe recovery of
ambiguous in-flight work. No real provider adapter or credential is configured.

Verification blocker: the complete 217-test suite passes, but this environment does
not contain `pytest-cov`, and its PyPI and apt package sources are rejected by the
network proxy. The required coverage threshold must be verified once that dependency
is available.

## 2. Durable long-term career memory

Status: **PENDING**

Goal: replace purely in-memory career memory with a durable provider-neutral boundary.

Acceptance criteria:
- [ ] Define durable memory repository interface.
- [ ] SQLite implementation.
- [ ] Versioned/validated JSON-safe serialization.
- [ ] Retrieval by user and memory type.
- [ ] Restart tests.
- [ ] No unsafe deserialization.
- [ ] Existing `MemoryEngine` contract preserved or migrated explicitly.

## 3. Application tracker

Status: **PENDING**

Goal: persist and track each job application across its lifecycle.

Acceptance criteria:
- [ ] Application aggregate and lifecycle states.
- [ ] Repository boundary and SQLite implementation.
- [ ] Link application to job/company and external-action operation IDs.
- [ ] Timeline/history events.
- [ ] Duplicate prevention.
- [ ] Query/filter support.
- [ ] Tests and documentation.

## 4. Communication adapter

Status: **PENDING**

Goal: introduce a provider-neutral communication layer for recruiter/employer messages.

Acceptance criteria:
- [ ] Adapter protocol for draft/send/read/reply primitives.
- [ ] Safe dry-run/fake provider.
- [ ] Human approval gate before real send.
- [ ] Idempotency integration.
- [ ] Thread/conversation identifiers persisted.
- [ ] Input sanitization and failure handling.
- [ ] No production credentials in repository/tests.

## 5. Scheduling and interview coordination

Status: **PENDING**

Goal: model interview/trial-day scheduling and present exact employer, address, date, and time when human participation is required.

Acceptance criteria:
- [ ] Scheduling domain model.
- [ ] Calendar adapter boundary.
- [ ] Human gate for accept/decline/reschedule.
- [ ] Time-zone-aware storage.
- [ ] Conflict handling.
- [ ] Tests for restart and duplicate events.

## 6. Real signal-source adapters and employer intelligence

Status: **PENDING**

Goal: replace static-only proactive opportunity input with real provider adapters while keeping scoring deterministic and explainable.

Acceptance criteria:
- [ ] At least one production-capable signal provider boundary implementation.
- [ ] Provenance and observation timestamps.
- [ ] Rate/error handling.
- [ ] Deduplication.
- [ ] Employer intelligence aggregation.
- [ ] No fabricated signals.

## 7. End-to-end autonomous career loop

Status: **PENDING**

Goal: connect search -> decision -> resume/application preparation -> approval -> communication -> tracking -> interview coordination.

Acceptance criteria:
- [ ] Bounded long-running loop.
- [ ] Durable recovery.
- [ ] Idempotent external actions.
- [ ] Clear human-action events.
- [ ] End-to-end integration tests with fake providers.
- [ ] No duplicate application/message after restart.

## 8. Production hardening

Status: **PENDING**

Acceptance criteria:
- [ ] Structured logging and correlation IDs.
- [ ] Configuration validation.
- [ ] Database migration strategy.
- [ ] Observability for failed/stuck runs.
- [ ] API surface and OpenAPI where applicable.
- [ ] Security review of external inputs and secrets.
- [ ] Release/deployment documentation.
- [ ] Full CI green at >=90% coverage.

## Completion definition

The project is not considered complete merely because modules exist. Completion requires a verified end-to-end flow that safely survives restarts, prevents duplicate consequential actions, and stops at explicit human gates for real-world decisions.
