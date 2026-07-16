# CODEX SPRINT 08

## Mission

Build the Workflow Engine.

This sprint introduces the orchestration layer that sits between the AgentBrain and all specialized agents.

The Workflow Engine is responsible only for execution flow.

It never performs business logic.

It never calls LLMs.

It never searches jobs.

It never evaluates employers.

It only coordinates steps.

---

# Architecture

CareerAgent AI

User

↓

Workflow Engine

↓

Agent Brain

↓

Agents

↓

Memory Engine

↓

Result

---

# Create

career_agent_ai/application/workflow/

with the following files:

workflow.py

workflow_step.py

workflow_engine.py

workflow_state.py

workflow_result.py

---

# Workflow

Workflow represents an immutable execution plan.

Fields:

- workflow_id
- name
- description
- created_at
- current_step
- status
- steps

Use frozen dataclasses.

---

# WorkflowStep

Each step represents exactly ONE action.

Examples:

SearchJobs

EvaluateEmployer

PrepareResume

Apply

WaitForApproval

NotifyUser

PrepareInterview

Each step has:

- id
- name
- order
- task
- status

Immutable.

---

# WorkflowState

Create enum:

Pending

Running

Paused

Completed

Failed

Cancelled

---

# WorkflowResult

Contains:

- workflow_id
- success
- completed_steps
- failed_step
- execution_time
- metadata

Immutable.

---

# WorkflowEngine

Responsibilities only:

start(workflow)

next_step()

complete_step()

fail_step()

pause()

resume()

cancel()

snapshot()

No business logic.

No AI.

No HTTP.

No external APIs.

No file access.

---

# Integration

WorkflowEngine communicates only with AgentBrain.

WorkflowEngine never calls specialized agents directly.

AgentBrain remains responsible for dispatching tasks.

---

# Tests

Create comprehensive pytest coverage.

Test:

✓ workflow creation

✓ immutable workflow

✓ state transitions

✓ execution order

✓ pause

✓ resume

✓ cancellation

✓ failures

✓ snapshots

✓ invalid transitions

Target:

100% passing tests.

---

# Rules

Use Python 3.11.

Strict typing.

Frozen dataclasses.

Clean Architecture.

Small files.

No placeholders.

No TODO.

No mock business logic.

No fake AI.

No OpenAI integration.

No LangGraph.

No external libraries except pytest.

Everything deterministic.

The result must compile and all tests must pass.