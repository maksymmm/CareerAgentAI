# Autonomous Implementation Plan

This document is the prioritized execution queue for CareerAgentAI. Codex should take the highest-priority unfinished item that is not blocked and deliver it as one coherent, verified unit.

## 1. Crash-safe idempotency for consequential external actions

Status: **COMPLETED**

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
- [x] Maintain >=90% total coverage.
- [x] Document the contract.

Delivered: a provider-neutral adapter boundary, mandatory explicit approval at execution,
atomic SQLite state transitions, terminal duplicate suppression, and safe recovery of
ambiguous in-flight work. No real provider adapter or credential is configured.

Verification: GitHub CI verified all 217 tests passing with 90.67% total coverage.

## 2. Durable long-term career memory

Status: **COMPLETED**

Goal: replace purely in-memory career memory with a durable provider-neutral boundary.

Acceptance criteria:
- [x] Define durable memory repository interface.
- [x] SQLite implementation.
- [x] Versioned/validated JSON-safe serialization.
- [x] Retrieval by user and memory type.
- [x] Restart tests.
- [x] No unsafe deserialization.
- [x] Existing `MemoryEngine` contract preserved or migrated explicitly.

Delivered: a provider-neutral memory repository boundary, compatible in-memory
default, and restart-safe SQLite implementation. Persisted values and metadata are
strict JSON, carry an explicit serialization version, and are validated when written
and loaded. Records can be queried deterministically by user and memory type.

## 3. Application tracker

Status: **COMPLETED**

Goal: persist and track each job application across its lifecycle.

Acceptance criteria:
- [x] Application aggregate and lifecycle states.
- [x] Repository boundary and SQLite implementation.
- [x] Link application to job/company and external-action operation IDs.
- [x] Timeline/history events.
- [x] Duplicate prevention.
- [x] Query/filter support.
- [x] Tests and documentation.

Delivered: an immutable, validated application aggregate with explicit lifecycle
transitions, chronological history, optimistic concurrency, unique candidate/job
applications, and stable links to company and crash-safe external-action operation
IDs. The provider-neutral repository has in-memory and restart-safe SQLite adapters;
SQLite storage uses versioned strict JSON for event metadata and deterministic
filtering and ordering. No external action is executed by the tracker.

Next priority: communication adapter.

## 4. Communication adapter

Status: **COMPLETED**

Goal: introduce a provider-neutral communication layer for recruiter/employer messages.

Acceptance criteria:
- [x] Adapter protocol for draft/send/read/reply primitives.
- [x] Safe dry-run/fake provider.
- [x] Human approval gate before real send.
- [x] Idempotency integration.
- [x] Thread/conversation identifiers persisted.
- [x] Input sanitization and failure handling.
- [x] No production credentials in repository/tests.

Delivered: a provider-neutral communication boundary and deterministic no-I/O fake,
strict validated plain-text message models, and restart-safe SQLite storage for
message, thread, and reply-to identifiers. Send/reply actions always pass through
the existing approval-gated external-action service. Duplicate requests reuse their
durable result, failures remain safely terminal, and ambiguous in-flight actions are
held for reconciliation without another provider call. No real provider, credential,
or network send is configured. Review hardening also prevents a new operation ID from
resending an outbound message, validates provider delivery results against their
prepared intent, applies strict deterministic message typing, and orders persisted
threads by timezone-aware instants. A durable atomic draft-to-operation claim prevents
stale and concurrent competing sends immediately before provider execution. Failures
after possible provider success now require reconciliation rather than being recorded
as safe failures. Successful reply retries reuse their durable result across restarts;
only an explicit pre-delivery provider failure releases the delivery claim for a new
deliberate operation, while ambiguous outcomes remain locked.
Concurrent identical message inserts converge on the durable winning row; conflicting
reuse of a message ID remains rejected.
Thread ordering preserves exact microsecond instants through a legacy-compatible UTC
epoch backfill, and malformed lone Unicode surrogates are rejected at the domain edge.

Next priority: scheduling and interview coordination.

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
