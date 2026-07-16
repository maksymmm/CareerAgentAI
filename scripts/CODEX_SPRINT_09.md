# CODEX SPRINT 09
# Agent Brain Integration Layer

## Objective

Integrate the existing Career Memory Engine and Workflow Engine into a single deterministic orchestration layer called Agent Brain.

This sprint is NOT about AI.

This sprint is NOT about LLMs.

This sprint is NOT about HTTP.

This sprint is NOT about external APIs.

This sprint is ONLY about application orchestration.

The Agent Brain becomes the central coordinator of the application.

---

# Existing Components

The project already contains:

- Career Memory Engine
- Workflow Engine
- Clean Architecture
- Domain Models
- Application Layer

DO NOT rewrite them.

DO NOT duplicate them.

DO NOT redesign them.

Only integrate them.

---

# Create

career_agent_ai/application/brain/

---

Create at least:

agent_brain.py

agent_brain_state.py

agent_context.py

agent_request.py

agent_response.py

---

# AgentBrain Responsibilities

AgentBrain is responsible for:

- receiving requests

- loading memory snapshot

- starting workflows

- continuing workflows

- returning immutable responses

AgentBrain DOES NOT

- search jobs

- call OpenAI

- call Anthropic

- call Gemini

- call MCP

- call HTTP

- parse resumes

- evaluate companies

- perform business logic

It only coordinates.

---

# AgentRequest

Immutable dataclass.

Contains for example:

request_id

user_id

action

payload

metadata

timestamp

---

# AgentContext

Immutable snapshot passed during execution.

Contains:

user_id

memory_snapshot

active_workflow

metadata

No mutable state.

---

# AgentResponse

Immutable object.

Contains:

request_id

success

workflow_id

messages

metadata

timestamp

---

# AgentBrainState

Enum.

Example:

IDLE

RUNNING

WAITING

FAILED

SHUTDOWN

---

# AgentBrain

Must own references to

MemoryEngine

WorkflowEngine

No globals.

No singletons.

No service locator.

Constructor Injection only.

---

Public API

process()

start_workflow()

continue_workflow()

cancel_workflow()

pause_workflow()

resume_workflow()

snapshot()

---

Rules

AgentBrain may only use

MemoryEngine public API

WorkflowEngine public API

Never access repository internals.

Never mutate Memory objects.

Never mutate Workflow objects.

Everything remains immutable.

---

Workflow Integration

When processing a request

AgentBrain should

1.

load memory snapshot

2.

create execution context

3.

start or continue workflow

4.

return AgentResponse

No business logic.

---

Memory Integration

AgentBrain never edits repository directly.

Only through MemoryEngine.

---

Dependency Rules

Allowed

AgentBrain

↓

MemoryEngine

WorkflowEngine

Forbidden

Workflow -> AgentBrain

Memory -> Workflow

Domain -> Application

No circular dependencies.

---

Testing

Create comprehensive pytest coverage.

Minimum:

- AgentBrain creation

- constructor injection

- process request

- start workflow

- continue workflow

- pause workflow

- resume workflow

- cancel workflow

- immutable context

- immutable response

- memory snapshot usage

- workflow snapshot usage

- invalid transitions

Coverage should exceed 90%.

---

Documentation

Update README with

Agent Brain Architecture

Component Responsibilities

Execution Flow

Dependency Diagram

Keep documentation concise.

---

Constraints

No AI.

No networking.

No HTTP.

No async.

No database.

No file IO.

No YAML.

No JSON persistence.

No external libraries.

Only Python standard library.

---

Acceptance Criteria

✔ Clean Architecture preserved

✔ Constructor Injection only

✔ Immutable objects

✔ No circular dependencies

✔ Existing Memory Engine reused

✔ Existing Workflow Engine reused

✔ Comprehensive tests

✔ Deterministic behavior

✔ No business logic

✔ No external services

End of Sprint 09.