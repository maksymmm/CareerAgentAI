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
