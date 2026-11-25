# Decision 0006: Application sandbox schema transaction ownership

## Status

Accepted

## Context

The SQLite sandbox application provider creates its intent, attempt, and submission
ledger tables during construction. SQLite DDL participates in an active transaction.
If the provider is constructed while a caller-owned transaction is open, the
constructor's schema initialization followed by `commit()` can commit unrelated
caller writes.

The submit path's transaction guard does not protect this constructor-time boundary.

## Decision

`SQLiteSandboxSubmissionProvider` must inspect the supplied connection before any
schema statement is executed. If `connection.in_transaction` is true, construction
fails with `PreSubmissionError` and performs no SQL or commit.

The provider may initialize its schema only when it owns the connection's transaction
boundary. Callers must construct it outside their own write transactions.

## Consequences

- Caller-owned pending writes remain pending and can still be rolled back.
- No provider tables are created by a rejected initialization.
- Normal schema initialization remains idempotent.
- The rule is enforced at the provider boundary and covered by a SQLite regression
  that checks transaction state, schema absence, and rollback behavior.
