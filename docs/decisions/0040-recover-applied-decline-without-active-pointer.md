# ADR 0040: Recover provider-applied declines after active-pointer clearance

- Status: Accepted
- Date: 2026-11-26

## Context

A candidate decline adapter can commit the provider effect and the event transition
before the external-action repository persists the terminal `succeeded` status. The
event transition clears `active_action_operation_id`; if the terminal status write
fails, the durable operation is conservatively left `reconciliation_required`.

The pending-decline endpoint previously required every ambiguous operation to remain
referenced by the event's active-action pointer. After this specific post-commit failure,
that pointer is correctly absent, even though the operation and provider evidence are
durable. The candidate therefore received a conflict and could not finish reconciliation
of an already-applied decline.

## Decision

Allow pointerless pending recovery only when all of the following hold:

- exactly one ambiguous decline operation is bound to the authenticated candidate,
  event, and deterministic source-version operation ID;
- the operation is already `reconciliation_required`, not merely `in_progress`;
- the event is already `declined` at exactly source version + 1;
- the complete source snapshot matches the event's unchanged slot; and
- the candidate still supplies independent provider evidence through the existing
  reconciliation contract.

Pointerless in-progress operations, source-state events, multiple operations, malformed
snapshots, and mismatched identities remain fail-closed. Recovery performs no provider
retry and does not recreate the cleared pointer.

## Verification

An HTTP/SQLite regression installs a temporary SQLite trigger that rejects only the
terminal `succeeded` status write. The real sandbox provider commits one effect and the
event transition clears the pointer; the external-action service records
`reconciliation_required`. A new candidate runtime discovers the operation, submits
independent positive evidence, and completes reconciliation without a second provider
effect. Existing tests continue to reject pointerless in-progress operations.
