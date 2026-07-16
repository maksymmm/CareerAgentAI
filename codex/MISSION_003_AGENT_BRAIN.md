# Mission 003 – Agent Brain

You are implementing the decision engine of CareerAgent AI.

This is the central intelligence layer.

The Agent Brain must coordinate all future agents.

---

## Responsibilities

- Decide what task to execute next.
- Prioritize actions.
- Avoid duplicate work.
- Respect user preferences.
- Track progress.
- Detect failures.
- Retry failed tasks.
- Escalate when human action is required.

---

## Architecture

Create:

- AgentBrain class
- Task Queue
- Priority Queue
- Decision Engine
- State Machine
- Event Bus interfaces

---

## States

Idle

SearchingJobs

EvaluatingEmployer

PreparingResume

Applying

WaitingForReply

Negotiating

InterviewScheduled

Completed

Paused

Error

---

## Decision Rules

If no jobs exist:

→ Search jobs

If better jobs appear:

→ Re-prioritize queue

If employer score is too low:

→ Skip

If user approval required:

→ Pause

If interview invitation received:

→ Notify user immediately

---

## Requirements

Production quality.

Typed.

Unit tests.

Documentation.

No placeholders.

No fake logic.