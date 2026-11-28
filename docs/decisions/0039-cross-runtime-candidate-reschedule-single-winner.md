# ADR 0039: Cross-runtime candidate reschedule single-winner execution

## Status

Accepted as a production-readiness test contract.

## Context

The candidate reschedule endpoint is served by independent runtime instances that
share a file-backed SQLite store. A browser retry, a second tab, or a restarted
candidate service can submit the same approved event version and target while the
original provider call is still running.

A durable idempotency key alone is not sufficient to describe the user-visible
concurrency contract. The candidate must not see a false success, trigger a second
provider call, or lose the durable winner while the first call is unresolved.

## Decision

The event-scoped SQLite execution lease is the single-winner boundary across
candidate runtimes:

- The first runtime acquires the lease and may invoke the sandbox calendar provider.
- A concurrent runtime submitting the same owner/event/source-version/target approval
  receives `202 Accepted` with `outcome=running` while the first attempt is live.
- The duplicate must not invoke its own provider outcome plan or create an attempt or
  effect.
- After the first runtime commits success, a replay from another runtime returns the
  durable completed event without another provider attempt or effect replay.

The regression uses two independent candidate compositions and two loopback WSGI
listeners over one temporary SQLite database. The first sandbox provider callback is
held deterministically with thread events, so the duplicate is submitted while the
original provider call is known to be live. The test asserts the event version,
operation terminal state, attempt history, effect count, and replay counter.

## Consequences

This proves the candidate HTTP/runtime boundary, not just the provider adapter, keeps
one provider effect across two independent runtime instances. It does not claim
power-loss durability or validate a production calendar adapter. The test uses only
synthetic event data, local SQLite, and the no-network sandbox provider.

No production credentials, real calendar, application submission, or outbound message
is involved.
