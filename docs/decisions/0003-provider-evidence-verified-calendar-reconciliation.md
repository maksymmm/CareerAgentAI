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


## Canonical intent integrity

The sandbox persists the canonical JSON payload that produced each calendar intent
fingerprint, including the action, source event, reschedule arguments, and exact
delivered event. Verification recomputes the SHA-256 digest from that payload and
requires the effect action and complete event snapshot to match it. A matching pair
of rewritten database fingerprints is therefore insufficient evidence.

The attempt ledger must be non-empty, contiguous, and recognized. Confirmed absence
requires every attempt to be an explicit no-effect or interrupted-before-effect
outcome; confirmed presence requires a terminal success or timeout-after-effect and
an exact effect match. Legacy intent rows without the canonical payload remain
inconclusive and are never automatically backfilled or retried.


## Replay accounting integrity

Returning a previously recorded calendar effect is permitted only when the canonical
intent, terminal effect-producing attempt history, and exact effect snapshot still
match. Replay accounting is a separate integrity boundary: its persisted value must be
a non-negative SQLite signed 64-bit integer below the maximum before increment. The
increment uses a compare-and-swap predicate over the observed value; a zero-row update
is treated as a conflict, the transaction is rolled back, and the effect is not returned.
Malformed, negative, or exhausted counters never trigger a provider attempt or mutate
the attempt ledger. Reconciliation continues to derive PRESENT/ABSENT_CONFIRMED from
canonical intent, attempt history, and effect evidence rather than the replay counter.
