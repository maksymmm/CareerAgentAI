# CODEX SPRINT 07

## Mission

Implement the Career Memory Engine.

This is NOT ChatGPT memory.

This is the persistent intelligence layer that allows CareerAgent AI to remember everything that happened during a user's career journey.

The Memory Engine will become the long-term knowledge base used by Agent Brain and all specialized agents.

---

# Goal

Create a production-ready memory module using Clean Architecture.

The implementation must be deterministic.

No AI.

No external APIs.

No placeholders.

No TODOs.

Everything must be fully typed.

Python 3.11+

---

# Architecture

Create

career_agent_ai/

    domain/

        memory.py

    application/

        memory/

            memory_engine.py

            memory_repository.py

    infrastructure/

        memory/

            in_memory_repository.py

---

# Memory Types

Support independent memories for:

- EmployerMemory
- RecruiterMemory
- JobMemory
- ResumeMemory
- InterviewMemory
- SalaryMemory
- OfferMemory
- RejectionMemory
- SkillMemory

Each memory must be immutable.

Use dataclasses.

---

# Repository

Create repository interface.

Required methods:

save()

update()

find()

find_all()

delete()

exists()

The infrastructure layer must provide an in-memory implementation.

---

# Memory Engine

Implement MemoryEngine.

Responsibilities:

Store memories.

Retrieve memories.

Update memories.

Merge duplicated memories.

Search memories by type.

Search memories by employer.

Search memories by recruiter.

Search memories by job.

Generate immutable snapshots.

---

# Rules

Memory must never mutate directly.

Every update creates a new immutable object.

Repository returns immutable collections.

No globals.

No singleton.

No hidden state.

---

# Documentation

Update README.

Explain Memory Engine.

Explain repository architecture.

Explain how Agent Brain will consume memories.

---

# Testing

Create comprehensive pytest suite.

Minimum coverage:

- create memory

- update memory

- delete memory

- merge memories

- repository CRUD

- immutable objects

- duplicate detection

- search by employer

- search by recruiter

- snapshot generation

All tests must pass.

---

# Acceptance Criteria

Clean Architecture.

100% typed.

Immutable domain.

Deterministic behavior.

No placeholders.

Production-ready implementation.

The code must integrate naturally with previous sprints without breaking existing functionality.