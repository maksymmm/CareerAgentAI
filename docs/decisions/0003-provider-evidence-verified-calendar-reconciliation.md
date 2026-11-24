# ADR 0003: Provider-evidence-verified calendar reconciliation

Status: accepted (candidate sandbox). Scope: calendar accept, decline, and reschedule.

## Decision

Candidate scheduling uses a SQLite-backed no-network calendar provider. Stable operation
IDs bind canonical action/event intents; attempts and effects survive restart and one
operation can create at most one simulated provider effect.

Reconciliation accepts `completed=true` only when provider evidence matches the exact
event identity, approved action, target slot/timezone, and provider event ID. It accepts
`completed=false` only after authoritative absence under a SQLite writer lock. Unknown,
malformed, conflicting, or unavailable evidence fails closed and leaves workflow state
unchanged.

## Consequences

- A timeout after the effect cannot reopen an operation and duplicate a calendar response.
- An interruption committed before the effect can be safely reopened and retried.
- Provider evidence supplies the durable provider event ID; callers cannot invent one.
- All effects remain local sandbox records. No production credential, invitation response,
  appointment, or real calendar is created or modified.


## Writer contention and concurrent invocation

Provider writes acquire `BEGIN IMMEDIATE` before binding an operation ID, counting an
attempt, or recording an effect. If another SQLite connection holds the writer lock,
the action fails as a pre-effect error: no intent, attempt, or effect is recorded, and
the same approved intent may be retried after the lock is released.

Evidence lookup also acquires a writer lock. A busy database is an unavailable-evidence
error, never confirmed absence; a later lookup after the lock is released returns the
durable effect. Concurrent calls using the same operation ID and identical intent
serialize to one committed effect and one scripted provider attempt. Concurrent
conflicting intents cannot rebind the operation ID; only the winning fingerprint may
reach the outcome plan.
