# ADR 0043: Serialize concurrent sandbox communication retries around durable delivery evidence

- Status: Accepted
- Date: 2026-11-27

## Context

A candidate message send can be invoked twice with the same operation ID while the first
SQLite sandbox provider call is still executing. The critical failure window is a
provider delivery that commits durably but returns `TIMEOUT_AFTER_EFFECT`. A duplicate
must not execute the outcome plan again, create another outbound message, or reinterpret
the lost response as evidence that no delivery occurred.

Sequential idempotency and restart reconciliation do not prove this concurrent provider
boundary.

## Decision

Add a two-connection acceptance test against the real
`SQLiteSandboxCommunicationProvider` and one shared durable SQLite database.

- Persist one synthetic draft before starting either worker.
- Hold the first send inside the provider outcome plan while it owns the SQLite writer
  transaction.
- Start a same-key duplicate on an independent SQLite connection and prove it remains
  pending until the first transaction commits.
- Commit exactly one `TIMEOUT_AFTER_EFFECT` attempt and one outbound delivery.
- Require the duplicate to return the exact committed message, increment the durable
  replay counter once, and never invoke the outcome plan again.
- Verify positive delivery evidence, reopen the database in a new provider instance,
  and replay the same message without changing attempt history or creating another
  delivery.

The test uses synthetic `.example.test` identities and the local no-network SQLite
sandbox only.

## Consequences

The communication sandbox now has an explicit concurrent response-loss regression at
the provider boundary, including positive evidence and process-restart replay. The
contract does not claim that a real mailbox provider has SQLite's transaction or
idempotency guarantees.
