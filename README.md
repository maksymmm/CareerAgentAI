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

Signals enter through an explicit `OpportunitySignalProvider` boundary. The included static provider is deterministic and is intended for supplied data and tests; it does not pretend to discover live hiring signals.

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
- Durable application lifecycle tracking, history, duplicate prevention, and operation linkage
- Durable, versioned SQLite career memory with user/type retrieval
- Workflow state restoration
- Pre-vacancy opportunity scoring
- Bounded pre-vacancy action policy
- Unsent proactive outreach drafting
- Continuous integration workflow

Next architectural steps:

- real signal-source adapters
- employer intelligence
- communication adapter
- scheduling
- long-running autonomous execution

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
