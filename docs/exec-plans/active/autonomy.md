# Autonomous Implementation Plan

This document is the prioritized execution queue for CareerAgentAI. Codex should take the highest-priority unfinished item that is not blocked and deliver it as one coherent, verified unit.

### Release scope clarification

The eight completed units cover the autonomous backend core. `SPEC.md` now records
its implemented contracts and release exclusions; `PROJECT_PLAN.md` retains the
broader web/mobile/provider/deployment roadmap. Completed core work does not imply
that the public product is complete.

Parent integration commit `44bcc50907ce2827e1612fecbe16f1ac81dca2cb` passed
GitHub CI #899 with 635 tests and 91.72% coverage, and its exact-head Codex review
found no major issues. Any documentation commit after that evidence must pass its own
exact-head CI and Codex review before integration. The resulting integration HEAD must
then receive fresh release evidence before the final explicit approval to merge into
`main`. Subsequent product delivery needs separate acceptance criteria for the user
interface, real providers and deployment.

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

Status: **COMPLETED**

Goal: model interview/trial-day scheduling and present exact employer, address, date, and time when human participation is required.

Acceptance criteria:
- [x] Scheduling domain model.
- [x] Calendar adapter boundary.
- [x] Human gate for accept/decline/reschedule.
- [x] Time-zone-aware storage.
- [x] Conflict handling.
- [x] Tests for restart and duplicate events.

Delivered: a provider-neutral scheduling domain for interviews, trial days, phone/video
calls, and other human-participation events; a deterministic no-I/O calendar adapter;
and restart-safe SQLite persistence. Events retain employer, location, application and
provider identifiers, exact UTC instants, and their presentation timezone. Human-facing
projections expose employer, event type, address/link, local date/time, timezone, UTC
offset, and status. Accept, decline, and reschedule actions require explicit human
approval and use the crash-safe external-action service. Durable per-event action
claims suppress stale/concurrent duplicate responses; explicit pre-provider failures
can be retried deliberately, while uncertain or post-provider outcomes require
reconciliation. Conflict detection is deterministic and persisted state is strictly
validated after restart.

Next priority: real signal-source adapters and employer intelligence.

## 6. Real signal-source adapters and employer intelligence

Status: **COMPLETED**

Goal: replace static-only proactive opportunity input with real provider adapters while keeping scoring deterministic and explainable.

Acceptance criteria:
- [x] At least one production-capable signal provider boundary implementation.
- [x] Provenance and observation timestamps.
- [x] Rate/error handling.
- [x] Deduplication.
- [x] Employer intelligence aggregation.
- [x] No fabricated signals.

Delivered: a production-capable HTTPS RSS/Atom opportunity-signal adapter for configured
company-owned news feeds. Signals are emitted only from actual parsed feed entries that
match explicit deterministic rules. Each signal retains an entry/feed provenance URL,
provider-native external identity when available, publication/fetch observation time,
and matched evidence keywords. Collection is request-rate-limited, response-size
bounded, fail-soft across sources, and exposes sanitized per-source errors. Stable
provider identities are deduplicated before employer aggregation and before opportunity
scoring so repeated observations cannot inflate a score. Employer intelligence groups
the retained evidence by company with latest observation time, signal types, and
provenance sources. No synthetic signal is created when source evidence does not match.

Next priority: end-to-end autonomous career loop.

## 7. End-to-end autonomous career loop

Status: **COMPLETED**

Goal: connect search -> decision -> resume/application preparation -> approval -> communication -> tracking -> interview coordination.

Acceptance criteria:
- [x] Bounded long-running loop.
- [x] Durable recovery.
- [x] Idempotent external actions.
- [x] Clear human-action events.
- [x] End-to-end integration tests with fake providers.
- [x] No duplicate application/message after restart.

Delivered: a bounded durable state machine connects ranked search, deterministic
career decision progression, resume preparation, application preparation, explicit
human approval, crash-safe application submission, optional outbound communication,
application tracking, and interview coordination. Active loop state uses versioned
strict JSON in SQLite and can recover both human-gated states and already-approved
nonterminal phases after restart. Application submission has its own provider-neutral
adapter and no-I/O fake, executes through the existing external-action coordinator,
and uses the stable application identity for idempotency rather than a transient run
ID. Existing communication and scheduling services retain their own crash-safe
idempotency and reconciliation contracts. Human-action events expose the precise
decision being requested, including the exact typed application artifact and its
SHA-256 digest before submission plus exact interview employer/location/local time.
The approved application artifact is persisted in restart-safe loop state and copied
into the crash-safe external-action intent so provider execution is cryptographically
bound to the content the human saw. Interview coordination requires candidate
ownership and compatible application linkage. End-to-end fake-provider tests exercise
process restarts and verify that tracked applications and outbound messages are not
duplicated. A later run is prevented from resubmitting a user/job application that has
already progressed beyond draft.
Durable execution leases are heartbeat-renewed during long-running steps so a second
worker cannot reclaim an active run mid-provider-call. Snapshot persistence failures
after already-completed external actions remain resumable, and ambiguous application
submission outcomes enter an explicit reconciliation gate. Provider-verified success
is finalized without another submit call; provider-verified no-effect outcomes reopen
the same stable idempotency operation for a deliberate retry.
The same explicit reconciliation pattern covers ambiguous recruiter-message delivery
and interview/calendar responses, preventing uncertain external effects from becoming
terminal loop failures.

Next priority: production hardening.

## 8. Production hardening

Status: **COMPLETED**

Acceptance criteria:
- [x] Structured logging and correlation IDs.
- [x] Configuration validation.
- [x] Database migration strategy.
- [x] Observability for failed/stuck runs.
- [x] API surface and OpenAPI where applicable.
- [x] Security review of external inputs and secrets.
- [x] Release/deployment documentation.
- [x] Full CI green at >=90% coverage.

Delivered: bounded structured JSON logging with correlation scopes and credential-key
redaction; strict environment-driven runtime configuration with safe external-effect
defaults; a read-only SQLite operational probe for failed, ambiguous, and stale work;
and an authenticated WSGI operational endpoint with a matching OpenAPI 3.1 contract.
A versioned SQLite migration runner records immutable SHA-256 checksums, applies each
migration transactionally, and refuses destructive migrations without explicit
approval. Deployment, migration, rollback, and security-review documentation defines
the production operating contract. The complete GitHub CI suite remains above the
project's 90% coverage floor.

## Completion definition

### Exact-head release review follow-up

Review of `100acf2e8d` found an ownerless approval/reconciliation boundary (P1) and
SQLite query casing that could disable durable heartbeat renewal (P2).

- [x] Require caller identity on all existing autonomous-loop entry points and
  compare it with the durable owner before state disclosure or mutation.
- [x] Exercise foreign approve/decline after restart at every approval gate and
  reject missing/invalid identities and cross-user resolver/recovery/read calls.
- [x] Use SQLite-reported actual database backing in both renewal repositories,
  avoiding URI reinterpretation for casing, decoded tokens and repeated query keys.
- [x] Open worker and renewal connections with consistent explicit URI semantics.
- [x] Document the required caller API migration and URI filename behavior.

Verification: full local suite passed with 611 tests and 91.69% coverage (90% floor).
Regression cases compare SQLite-reported backing for ordinary, case-variant,
percent-encoded, repeated-key and fragment-bearing URI filenames. Owner tests cover
approve/decline and all three ambiguous-outcome gates across process restart.

Additional P1 follow-up: production runtime validation now asks SQLite for actual
URI backing, preserves URI text, and rejects encoded/fragment-bearing memory
paths and invalid URIs. Durable case-sensitive and repeated-key paths remain
accepted. Local verification: 623 tests passed with 91.71% coverage.

The subsequent exact-head review found percent-encoded NUL bytes could make SQLite
truncate a URI filename. Runtime validation now rejects raw or decoded NUL bytes
before opening the database URI, with coverage in every runtime environment.

Release review of `c61a1a6fe6` found live job discovery bypassed the runtime network
kill switch. Job providers now use the same execution-boundary guard as employer
signals, and default `AgentFactory` job-search composition is network-disabled unless
an explicitly enabled validated runtime configuration is supplied.

Release review of `87696564b7` found the new public `RuntimeConfig` annotation was
unavailable to runtime introspection. The factory now imports that type from the
cycle-safe config module at module scope, with regression coverage for
`typing.get_type_hints()` on both annotated factory methods.
The runtime package also advertises and caches its lazy composition exports so
`dir()` and `inspect.getmembers()` preserve the established public API surface.

## 9. Operational service composition

Status: **COMPLETED**

Goal: turn the existing read-only operational API into a safely composable deployment
unit without adding a real provider or crossing an external-action boundary.

Acceptance criteria:
- [x] Validate production runtime configuration before opening the service database.
- [x] Require a deployment-secret bearer token without storing it in `RuntimeConfig`.
- [x] Compose the SQLite probe, API service, and WSGI adapter through one public root.
- [x] Use request-local SQLite connections that are safe under threaded WSGI servers.
- [x] Open operational inspection read-only so missing storage cannot be masked.
- [x] Add regression tests for health, authenticated inspection, invalid secrets, and cleanup.
- [x] Document the deployment contract.

Delivered: `build_operational_app_from_env()` validates environment and operational
authentication, then wires the configured SQLite database to the read-only probe and
WSGI API. Each request opens and closes SQLite in its own execution thread, and the
runtime rejects new requests after idempotent shutdown. Invalid bearer tokens fail
before runtime validation can probe a SQLite URI or create a database file. Liveness
and routing do not open SQLite; inspection failures stay inside the sanitized API error
boundary. Inspection uses SQLite read-only mode, so lost storage is never replaced by
an empty database. No network
provider, consequential action, production
credential, or server process is enabled by this composition.

Next priority: define a candidate-facing approval UI contract before implementing a
web interface; real provider adapters remain separately sandbox-gated. Merge into
`main` still requires explicit user approval.

The project is not considered complete merely because modules exist. Completion requires a verified end-to-end flow that safely survives restarts, prevents duplicate consequential actions, and stops at explicit human gates for real-world decisions.
