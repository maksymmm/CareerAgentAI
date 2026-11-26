# ADR 0023: Fence live candidate reschedules with durable execution leases

- Status: Accepted
- Date: 2026-11-26

## Context

The candidate reschedule pending-intent endpoint can be called from another browser tab while the original provider request is still executing. Treating every `IN_PROGRESS` operation as a process-restart orphan lets the lookup transition it to `RECONCILIATION_REQUIRED`. The original request then loses its expected-status compare-and-swap even if the provider action completes normally.

A process-local lock is insufficient because candidate requests may be served by different worker processes. A live request must be distinguishable from a stranded operation across the SQLite-backed runtime boundary.

## Decision

Candidate reschedule execution acquires an event-scoped lease in a separate SQLite sidecar database before entering the scheduling service. The lease has an opaque per-request token, a bounded expiry, and a unique event constraint. The active request renews the lease through a separate sidecar connection, so heartbeat writes do not contend with a provider transaction held on the application database.

The authenticated pending-intent GET opens the sidecar only in read-only mode to check the lease. An unexpired lease produces the allowlisted `running` outcome and never mutates the operation. The writable recovery path repeats the lease check immediately before promoting an `IN_PROGRESS` operation, closing the read-to-write race.

After a worker exits normally, it releases only the lease carrying its token. After process loss, the lease expires; the next pending lookup may then atomically promote the stranded operation to `RECONCILIATION_REQUIRED`. Provider evidence remains mandatory and lookup never invokes or retries the provider. Lease tokens and operation identifiers are never returned to the candidate.

## Consequences

- Concurrent tabs and worker processes sharing the SQLite filesystem observe the same active event lease without contending on the provider database writer lock.
- The sidecar is created as a private mode-0600 regular file; the common read-only lookup never creates it.
- A live reschedule retains its original operation compare-and-swap path.
- A crashed worker becomes recoverable after the short lease TTL, without blind provider replay.
- If heartbeat renewal fails or the lease expires while a provider is still running, recovery remains fail-closed: the original terminal write may lose its CAS and the candidate must reconcile provider evidence.
- The common empty pending lookup remains read-only and does not initialize the lease table.
