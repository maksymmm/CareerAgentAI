# ADR 0051: Serialize sandbox evidence verification with provider writes

- Status: Accepted
- Date: 2026-11-27

## Context

Application submission, mailbox delivery, and calendar effects are committed to separate
SQLite sandbox ledgers. Each provider writes its attempt and effect atomically inside a
writer transaction. An evidence reader that observes the ledger while that transaction
is still in progress must not report confirmed absence merely because the effect row is
not committed yet.

Sequential restart tests prove durable evidence after commit, but they do not prove the
cross-connection boundary between an in-flight provider write and a simultaneous
reconciliation lookup.

## Decision

Keep evidence verification serialized with provider writes by acquiring the SQLite
writer reservation before reading each effect ledger. The verification transaction is
rolled back after the read and must not invoke an outcome plan, append an attempt,
increment a replay counter, or create an effect.

Add one parameterized regression using independent SQLite connections for all three
sandbox providers. Each writer is held inside its transaction, then the verifier starts
on another connection. The verifier must remain blocked until the writer commits a
TIMEOUT_AFTER_EFFECT; it must then return PRESENT, never ABSENT_CONFIRMED.

The regression also asserts one attempt, one effect, zero replays, and one outcome-plan
call for each provider. A separate CI step enforces the two-decimal coverage threshold
after pytest, even when pytest fails unless the job is cancelled.

## Consequences

The no-network application, communication, and calendar providers now share an explicit
tested evidence-serialization contract across process-local connections. This proves
only the local SQLite sandbox boundary; it does not claim equivalent transaction or
idempotency guarantees from a real ATS, mailbox, or calendar provider.
