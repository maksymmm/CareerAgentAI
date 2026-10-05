# CareerAgentAI

CareerAgentAI is a modular AI-powered career platform built with Clean Architecture principles.

The project is designed to automate and coordinate career-related workflows while keeping business logic deterministic, testable, and independent of external services.

---

# Current Architecture

```
CareerAgentAI
│
├── application
│   ├── agents
│   │   ├── job_application
│   │   ├── job_search
│   │   └── resume
│   ├── brain
│   ├── career
│   │   ├── decision engine
│   │   ├── orchestration
│   │   ├── pre-vacancy opportunity pipeline
│   │   └── outreach drafting
│   ├── communication
│   │   ├── provider-neutral draft/send/read/reply protocol
│   │   └── safe dry-run provider
│   ├── external_actions
│   ├── jobs
│   │   └── durable application tracker
│   ├── memory
│   ├── search
│   ├── storage
│   └── workflow
│
└── tests
```

---

# Core Components

## Agent Brain

Coordinates application-level requests.

Responsibilities:

- receive requests
- load memory snapshots
- resolve specialized agents
- start and continue workflows
- return immutable responses

The Agent Brain coordinates; domain-specific decisions remain in dedicated services.

---

## Career Orchestrator

Turns a career objective into a bounded execution plan.

It supports:

- deterministic planning
- isolated run state
- unique run identifiers
- bounded execution
- agent failure handling
- human-action gates
- in-memory resume of paused runs
- SQLite-backed recovery of paused runs after process restart
- run summaries in memory

A human-gated run pauses instead of performing the next external action automatically.

---

## Career Decision Engine

Selects the next career action from the objective and current progress.

The decision contract is explainable through:

- selected action
- rationale
- confidence
- metadata describing the decision source

The implementation is deterministic and provider-independent so an LLM/ML planner can later implement the same contract without changing orchestration boundaries.

---

## Job Search

The job-search layer provides:

- provider abstraction
- live job-provider integration
- repository-level deduplication
- keyword/company/location/remote/employment/salary filters
- relevance scoring
- explicit sorting

---

## Pre-Vacancy Opportunity Pipeline

The proactive pipeline is designed for companies that may be hiring before a public vacancy is available.

Flow:

```
Signal Provider
      │
      ▼
Opportunity Ranking
      │
      ▼
Bounded Decision
      │
      ├── ignore
      ├── monitor
      └── prepare_outreach
              │
              ▼
        Unsent Outreach Draft
```

The current implementation deliberately stops at `prepare_outreach`.

It does **not** send messages, contact employers, or perform other external communication.

Signals enter through an explicit `OpportunitySignalProvider` boundary. The static provider remains deterministic for supplied data and tests. A production-capable RSS/Atom adapter can also collect real company-owned news feeds over HTTPS. It emits signals only when configured deterministic rules match actual feed entries, retains the entry/feed provenance and observation time, rate-limits requests, bounds response size, reports source failures without fabricating results, and deduplicates provider-native entry identities.

`EmployerIntelligenceService` aggregates those deduplicated source-backed signals by employer while preserving the underlying evidence, latest observation timestamp, signal types, and provenance sources. The same deduplicator is applied before opportunity scoring so repeated provider observations do not inflate a company's score.

---

## Memory Engine

Responsible for career memory snapshots and run summaries.

The engine preserves its original in-memory default while accepting a provider-neutral
memory repository. Its SQLite implementation persists JSON-safe, versioned records
across process restarts, validates stored data on retrieval, and supports indexed
queries by candidate user and memory type. Memory records use stable keys and retain
metadata and timezone-aware creation timestamps without unsafe deserialization.

Resumable career-run state continues to use a separate SQLite persistence boundary so
paused human-gated runs can survive process restarts.

---

## Crash-Safe External Actions

Consequential provider calls are coordinated through a provider-neutral external-action service and a durable SQLite operation repository. Callers supply a stable operation ID, prepare the intent, obtain explicit human approval, and only then request execution.

Operations move through `prepared`, `in_progress`, `succeeded`, `failed`, and `reconciliation_required` states. Intent is committed before an adapter is invoked and the terminal outcome is committed afterward. Reusing an operation ID with a different intent is rejected, while repeating a completed invocation returns its existing result without another provider call.

An `in_progress` operation found after restart is treated as ambiguous. It moves to `reconciliation_required` and is **not** blindly executed again; a future provider-specific reconciliation workflow must determine the real external outcome. Adapters must also pass the operation ID to providers as their idempotency key.

The current capability is infrastructure only: no production communication or application provider is configured, and no real external action is sent.

---

## Communication Adapter

Recruiter and employer communication is isolated behind a provider-neutral protocol
covering draft, send, read, and reply primitives. The application service validates
plain-text content and provider identifiers, persists message/thread/reply identifiers
in SQLite, and retains them across restarts.

Every send and reply—including local dry runs—requires explicit human approval and
is executed through the crash-safe `ExternalActionService`. Stable operation IDs
suppress duplicate requests and provider calls. A process restart after an operation
entered `in_progress` moves it to `reconciliation_required` rather than risking a
duplicate message. Provider failures are persisted as terminal failed attempts; a
new deliberate attempt must use a new operation ID.

Only persisted drafts can begin a new send operation, so changing the operation ID
cannot resend an already-outbound message. Provider delivery responses must preserve
the exact prepared content and conversation identifiers and must report an outbound
state before they are persisted. Message timestamps are normalized to UTC, while
thread retrieval compares timestamp instants so legacy offset timestamps remain
chronologically safe.

Immediately before a provider call, SQLite atomically binds the draft to exactly one
delivery operation. Stale, restarted, or concurrent operations with other IDs cannot
cross that claim, even if they were prepared earlier. If a provider returns success
but its response cannot be validated or durably stored, the operation moves to
`reconciliation_required` rather than being mislabeled as a safe failure or retried.
Successful reply operations can be replayed with the same operation ID after restart
without another provider call. A provider's explicit pre-delivery failure releases
the claim so a deliberate new operation can retry; uncertain or post-delivery failures
retain the claim and continue to block competing sends until reconciliation.
Concurrent workers may also idempotently persist the same previously unseen message:
the losing insert reloads and returns the identical winning row, while reuse of the
same message ID for different content or direction remains a hard conflict.
Thread chronology uses an indexed integer UTC epoch at exact microsecond precision;
legacy ISO timestamps are safely backfilled during repository initialization. Message
text validation also rejects lone Unicode surrogate code points before any adapter or
SQLite boundary is reached.

The included fake provider is deterministic, in-memory, and dry-run only. It performs
no network I/O and needs no credentials. No production communication provider or
credential configuration is included.

---

## Scheduling and Interview Coordination

Human-participation career events such as interviews, trial days, phone calls, and video calls are modeled as durable scheduling aggregates. Each event preserves the employer, event type, address or meeting location, UTC-normalized start/end instants, the original IANA timezone used for presentation, lifecycle status, application linkage, and provider identifiers.

The scheduling service exposes an exact human-facing projection with employer, location, local date, local start/end time, timezone, and UTC offset so DST transitions remain unambiguous. SQLite persistence uses integer epoch-microseconds for deterministic ordering without losing timestamp precision, validates persisted state on reload, and prevents duplicate provider-event identities.

Accept, decline, and reschedule are provider-neutral calendar actions. Every consequential response passes through the existing human-approval and crash-safe external-action layer. A durable per-event operation claim prevents stale or concurrent workers from issuing competing calendar responses. Explicit pre-provider failures release the claim for a deliberate retry; uncertain provider outcomes and post-provider persistence failures require reconciliation and are never blindly retried.

The included fake calendar adapter is deterministic, in-memory, and performs no network I/O. No production calendar provider or credential is configured.

---

## Application Tracker

The application tracker persists an immutable aggregate for each candidate/job pair.
It models validated transitions through `saved`, `applied`, `interview`, `offer`,
`rejected`, and `withdrawn`, while retaining a chronologically ordered timeline.

Both the provider-neutral repository and its SQLite implementation support stable
queries by candidate, job, company, status, and external-action operation ID.
SQLite enforces duplicate prevention for candidate/job pairs and optimistic versions
prevent stale writers from silently overwriting newer state. Application submission
operations can be linked by their stable crash-safe external-action IDs without
causing or repeating any external action. Persisted records use versioned, strict
JSON for timeline metadata and are fully validated after restart; no unsafe
deserialization is used.

---

## End-to-End Autonomous Career Loop

The end-to-end loop now connects ranked job discovery, deterministic career decision
progression, resume preparation, durable application preparation, explicit human
approval, crash-safe application submission, optional recruiter/employer messaging,
application tracking, and interview coordination.

The loop is a bounded state machine with a restart-safe SQLite state repository.
All entry points for existing loop runs (`get`, `resume`, `continue_run`, and the
three reconciliation resolvers) require keyword-only `user_id`. Service callers
must derive it from the authenticated principal, never an untrusted request body.
The loop checks it against the durable request owner before exposing state, changing
an approval, claiming a lease, or resolving a provider outcome. A foreign run and
a missing run both raise the same `KeyError`.
Every consequential external action uses a stable idempotency key and the existing
`ExternalActionService`. Application submission is scoped to the durable application
identity rather than an individual run, so a later run cannot silently submit the
same tracked application again. Communication and interview responses reuse their
existing crash-safe adapters and persistence contracts.

Human-action events are explicit for application submission, outbound messaging, and
interview/trial-day calendar responses. Before an application can be approved, the
resume/application preparation step must emit an explicit typed artifact. The exact
artifact text and SHA-256 digest are persisted in loop state, shown in the approval
event, copied into the durable external-action intent, and passed unchanged through
the submission-adapter contract. Reusing an operation ID with different artifact
content is rejected, so the provider cannot silently submit something other than what
the human approved.

Interview coordination verifies both candidate ownership and application linkage before
exposing an approval gate. A restart can recover both human-gated states and
already-approved in-progress phases through the durable continuation entry point.
Integration tests use only fake/no-I/O providers and verify that restarts do not create
duplicate applications or messages.

---

## Production Hardening

Production runtime behavior is now explicit rather than implicit. `RuntimeConfig` validates
environment, durable database path, logging level, network-provider permission, and
consequential-action permission from `CAREER_AGENT_*` environment variables. Safe
defaults keep network providers and consequential external actions disabled.

Operational logs can be emitted as compact JSON with a correlation ID propagated
through `correlation_scope()`. Structured fields use bounded JSON-safe serialization
and redact credential-like keys before formatting.

`SQLiteOperationalProbe` provides a read-only view of failed, ambiguous, and stale
external actions plus failed or non-human-gated stuck autonomous loops. A minimal WSGI
adapter exposes public liveness at `GET /healthz` and bearer-authenticated operational
issues at `GET /v1/operational/issues`. `openapi_document()` publishes the matching
OpenAPI 3.1 contract. The operational endpoint is read-only and returns `no-store`
responses.

`build_operational_app_from_env()` is the supported operational-service composition
root. It validates `RuntimeConfig`, requires a 32-to-4096-character ASCII token from
`CAREER_AGENT_OPERATIONAL_BEARER_TOKEN`, opens the configured SQLite database, and
wires the probe, service, and WSGI adapter without exposing the token through runtime
configuration. Database-backed requests open and close an isolated SQLite connection
inside their request thread, while liveness and routing remain database-independent.
The application provides an idempotent `close()` plus context-manager shutdown control
for deployment lifecycle hooks.

Coordinated schema changes use `SQLiteMigrationRunner`. Migration versions are
ordered, transactionally applied, and recorded with immutable SHA-256 checksums.
Destructive migrations fail closed unless the caller explicitly opts in. Deployment,
migration, rollback, and security-review guidance lives under `docs/operations/`.

---

## Workflow Engine

Responsible for deterministic workflow lifecycle management.

Supports:

- start
- next step
- execute
- complete
- fail
- pause
- resume
- cancel
- snapshots

---

# Design Principles

- Clean Architecture
- Immutable domain models
- Constructor injection
- Deterministic execution
- Explicit provider boundaries
- No global state
- No circular dependencies
- Bounded autonomous execution
- Human gates before consequential actions
- External communication isolated behind explicit interfaces

---

# Testing

Run the complete suite with:

```bash
python -m pytest
```

CI also runs pytest with coverage enforcement at the project's 90% minimum.

---

# Current Project Status

Implemented foundations:

- Workflow Engine
- Memory Engine
- Agent Brain
- Specialized career agents
- Job search and ranking
- Career Decision Engine
- Isolated autonomous career runs
- Human-gated resume support
- Durable SQLite recovery for paused career runs
- Crash-safe SQLite operation records for consequential external actions
- Explicit human approval and reconciliation gates around external-action adapters
- Provider-neutral, human-gated communication with a no-I/O fake provider
- Restart-safe persistence of communication thread, message, and reply identifiers
- Durable, human-gated interview/trial-day scheduling with conflict detection and exact local-time presentation
- Durable application lifecycle tracking, history, duplicate prevention, and operation linkage
- Durable, versioned SQLite career memory with user/type retrieval
- Workflow state restoration
- Pre-vacancy opportunity scoring
- Bounded pre-vacancy action policy
- Unsent proactive outreach drafting
- Production-capable HTTPS RSS/Atom opportunity-signal collection with provenance and rate/error handling
- Deterministic signal deduplication and employer intelligence aggregation
- Restart-safe end-to-end autonomous career loop with explicit human-action events
- Crash-safe, application-scoped submission idempotency and cross-run duplicate prevention
- Structured production logging with correlation IDs and secret redaction
- Validated runtime configuration with safe external-effect defaults
- Read-only operational observability API with OpenAPI 3.1
- Versioned checksum-verified SQLite migration runner and deployment/security runbooks
- Environment-composed operational WSGI runtime with request-local database lifecycle
- Continuous integration workflow

The eight-item autonomous-core execution plan is implemented on the integration branch.
This is a backend release candidate, not a completed public web/mobile product.
Release/deployment remains an explicit operator decision. See `SPEC.md` for the
current release contract and `PROJECT_PLAN.md` for the remaining product scope.

---

# Roadmap

The long-term system is intended to evolve toward an autonomous career agent that can:

1. understand a candidate's requirements and preferences
2. discover suitable public vacancies
3. identify companies showing credible pre-vacancy hiring signals
4. rank opportunities with explainable decisions
5. prepare applications and outreach
6. pause for human approval where required
7. communicate through explicit external-service adapters
8. track applications and responses
9. coordinate interviews and trial days
10. provide the exact employer, location, date, and time when human participation is required

The system should only cross consequential external-action boundaries through explicit, testable interfaces.

---

# License

Private project.
