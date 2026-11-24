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


## Evidence completeness hardening

A missing effect row is not, by itself, proof that a calendar action had no effect.
The sandbox verifier now requires a durable intent and a contiguous, recognized attempt
history before returning any authoritative result. Confirmed absence is limited to
histories in which every recorded attempt explicitly ended before an effect. A missing
intent, missing/gapped attempt, unknown outcome, or missing effect after a recorded
success/timeout returns `UNKNOWN` and therefore blocks a retry.

When an effect exists, its fingerprint must match the durable intent, the final attempt
must record success or timeout-after-effect, earlier attempts must be definite no-effect
outcomes, and the stored action must agree with the delivered event state. Inconsistent
evidence is inconclusive; it is never converted into a negative observation.
