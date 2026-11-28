# 0053 — Recover provider-applied candidate acceptance after pointer clearance

- Status: Accepted
- Date: 2026-11-27
- Scope: Candidate interview acceptance reconciliation in the SQLite sandbox

## Context

The calendar action adapter first commits the accepted event through
SQLiteSchedulingRepository.complete_action. That transition advances the event
version and clears active_action_operation_id. The shared external-action service
then persists the operation's terminal succeeded status. A process crash between
those writes leaves a provider-applied accepted event at source version + 1, no active
event pointer, and an external-action row requiring reconciliation.

The existing candidate intent projection required the active pointer and an event
still at its source version. It therefore failed closed on the legitimate provider-
applied crash window, hiding the action from a candidate after browser/runtime restart.

Acceptance is reversible. A later reschedule and re-accept can recreate the same
status and time slot, so matching status and slot alone cannot prove that the older
operation produced the current event.

## Decision

- Discover a pointerless acceptance only when the exact durable operation is still
  reconciliation_required, its immutable payload matches the source event slot,
  the current event is accepted at exactly source_event_version + 1, and a provider
  event identity is present.
- Continue to require the active pointer and exact source version for an action whose
  event commit has not completed.
- Return source_event_version as candidate-safe metadata; never expose operation,
  provider-event, application, or owner identifiers.
- Let the browser validate either the exact active source snapshot or the exact
  committed target snapshot, then require a fresh owner-scoped run-status read before
  enabling provider attestation.
- During reconciliation, accept an already-committed acceptance only at exactly
  source version + 1. A later same-slot acceptance is stale and raises a typed
  scheduling conflict, mapped to the existing HTTP 409 approval-conflict contract.

## Consequences

- A provider-applied acceptance remains discoverable after a runtime restart even
  though the active-action pointer has been cleared.
- Discovery is read-only and does not call or replay the calendar provider.
- A later move-away/return/re-accept cannot be mistaken for the original operation.
- SQLite and Chromium regressions cover exact source/target versions, provider identity,
  owner scoping, browser/runtime restart, and stale candidate attestations.
- All provider effects remain in the no-network SQLite sandbox.
