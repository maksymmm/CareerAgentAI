# ADR 0060: Fence stale candidate decline reconciliation at the service boundary

- Status: Accepted
- Date: 2026-11-28
- Scope: Provider-applied candidate interview decline reconciliation

## Context

The candidate decline pending-intent projection accepts a pointerless operation only
when the current event is exactly the prepared source version plus one. That protects
the HTTP workflow, but SchedulingService.resolve_reconciliation is a shared
application boundary and can be called without that candidate projection.

For an already-applied decline, matching declined status, source slot, timezone, and
positive provider evidence does not prove that the ambiguous operation produced the
current event if its version has advanced since the operation's source snapshot.

## Decision

When reconciling a completed calendar.decline whose event already matches the
provider-applied target, require the current event version to equal the prepared source
version plus one. Otherwise fail closed before marking the external-action operation
succeeded.

The check is limited to the already-applied decline path. A not-yet-applied operation
continues to require the exact prepared source snapshot before applying its transition.
Provider evidence remains mandatory and reconciliation never replays the provider.

## Consequences

- The shared scheduling service enforces the same version invariant as candidate
  pointerless decline discovery.
- A stale same-slot decline cannot be attached to a later event version by another
  service caller.
- The operation remains reconciliation-required on conflict.
- Regression coverage injects a terminal ledger-write failure after one sandbox
  provider effect, advances the event version while preserving the visible declined
  target, and proves reconciliation is rejected with one effect and zero replays.
- No production provider, credential, application, message, or calendar is used.
