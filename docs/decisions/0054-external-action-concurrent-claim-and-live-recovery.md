# ADR 0054: Serialize concurrent external-action claims and live recovery

- Status: Accepted
- Date: 2026-11-28
- Scope: Shared external-action service used by candidate application, messaging, and calendar workflows

## Context

Two independent races can occur around one durable external-action operation.

First, a duplicate request can read an operation after the winning worker has changed it to
`in_progress` but while that worker is still executing the provider. Immediately promoting
that row to `reconciliation_required` makes a live provider call appear abandoned and can
cause both callers to lose the successful result.

Second, two callers can both read the same `prepared` operation before either attempts the
`prepared → in_progress` compare-and-swap. Only one transition can win. The loser must not
invoke the adapter, but a raw conflict also loses the durable winner that explains why the
operation is already being executed.

## Decision

- Treat `in_progress` as live for a bounded, validated grace interval (default 300 seconds,
  permitted range 1–3600 seconds). Future timestamps are conservatively live.
- Promote only an expired operation to `reconciliation_required`; this recovery transition
  never calls the adapter. If the compare-and-swap loses, re-read and return a concurrently
  completed or classified durable winner.
- Claim `prepared` with the repository compare-and-swap. On conflict, re-read the operation
  and return it only when operation ID, action type, and immutable payload still match the
  original observation and the row has left `prepared`. Otherwise preserve the conflict.
- Keep provider invocation exclusively after the winning claim.

## Consequences

- Concurrent duplicate callers share one durable operation and cannot both invoke the adapter.
- A live provider call is not reclassified as abandoned merely because another request arrived.
- A lost claim cannot be rebound to a different payload and cannot create a second provider call.
- Deterministic SQLite regressions cover live duplicates, stale recovery, future clock skew,
  terminal-winner races, concurrent prepared claims, and payload rebinding.
- The guarantee applies to this local durable claim protocol; it does not assert that a real
  external provider supplies transactional or idempotency guarantees.
