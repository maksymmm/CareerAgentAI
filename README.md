# CareerAgentAI

CareerAgentAI is a modular AI-powered career platform built with Clean Architecture principles.

The project is designed to automate and coordinate career-related workflows while keeping business logic deterministic, testable, and independent of external services.

---

# Current Architecture

```
CareerAgentAI
│
├── application
│   ├── brain
│   ├── memory
│   └── workflow
│
└── tests
```

---

# Components

## Agent Brain

Coordinates the application.

Responsibilities:

- receive requests
- load memory snapshots
- start workflows
- continue workflows
- return immutable responses

The Agent Brain contains no business logic.

---

## Memory Engine

Responsible for immutable memory snapshots.

Responsibilities:

- manage memory records
- create snapshots
- expose public API

---

## Workflow Engine

Responsible for deterministic workflow execution.

Supports:

- start
- next step
- pause
- resume
- cancel
- complete
- snapshots

---

# Design Principles

- Clean Architecture
- Immutable Models
- Constructor Injection
- Deterministic Execution
- No Global State
- No Circular Dependencies

---

# Current Project Status

Completed:

- Workflow Engine
- Memory Engine
- Agent Brain Integration

Testing:

- 23 passing tests
- pytest

---

# Running Tests

Activate the virtual environment:

```bash
source .venv/bin/activate
```

Run all tests:

```bash
python -m pytest
```

---

# Project Structure

```
career_agent_ai/
    application/
        brain/
        memory/
        workflow/

    tests/
```

---

# Roadmap

Current focus:

- Agent Brain
- Workflow orchestration
- Memory integration

Upcoming:

- AI Agent layer
- Resume Agent
- Job Search Agent
- Company Research Agent
- Interview Preparation Agent

---

# License

Private project.