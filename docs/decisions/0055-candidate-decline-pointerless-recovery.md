# ADR 0055: Recover provider-applied candidate declines after pointer clearance

- Status: Accepted
- Date: 2026-11-28
- Scope: Candidate-approved interview decline recovery in the SQLite sandbox

## Context

A candidate decline spans the sandbox provider effect, the scheduling event transition,
and the external-action ledger's terminal status. The provider and event transition can
commit before the external-action operation is marked succeeded. The event transition
clears its active-action pointer, while the operation can remain
`reconciliation_required`. A strict pointer-only pending lookup then hides a real
provider-applied action after restart.

## Decision

Allow pointerless pending recovery only when all of the following hold:

- exactly one ambiguous decline row is discoverable for the owner and event;
- its deterministic operation ID binds the authenticated candidate, event, and
  immediately preceding source event version;
- the full source snapshot is present and well-typed;
- the source slot and timezone still match the current event; and
- the current event is exactly source version plus one with status `declined`.

A later version is not accepted even if it returns to the same slot. A pointerless
operation is never replayed by discovery. Reconciliation remains subject to independent
provider evidence; a `PRESENT` observation is required to confirm completion, and an
`ABSENT_CONFIRMED` observation cannot undo an already-committed decline.

## Consequences

- Browser/runtime restart can rediscover a provider-applied decline whose pointer was
  cleared before terminal ledger persistence.
- Coherent operation-ID or payload rebinding fails closed.
- The exact version fence prevents an old intent from being attached to a later
  same-slot event state.
- Discovery remains read-only and provider-free.
- Tests use synthetic interview data, temporary SQLite, and the no-network sandbox
  calendar provider only.
