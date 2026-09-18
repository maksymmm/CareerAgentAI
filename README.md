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
│   ├── jobs
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

Current implementation is in-memory. Durable persistence is a planned infrastructure step.

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
- Pre-vacancy opportunity scoring
- Bounded pre-vacancy action policy
- Unsent proactive outreach drafting
- Continuous integration workflow

Next architectural steps:

- durable career/run persistence
- real signal-source adapters
- employer intelligence
- application tracking
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
