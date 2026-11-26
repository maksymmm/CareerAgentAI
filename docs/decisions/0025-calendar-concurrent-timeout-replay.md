# ADR 0025: Serialize concurrent calendar retries around durable provider evidence

- Status: Accepted
- Date: 2026-11-26

## Context

The SQLite calendar sandbox may receive two requests with the same operation ID while
the first request is inside the provider transaction. A particularly important crash
window occurs when the provider commits an accepted calendar effect but the first
caller receives only `TIMEOUT_AFTER_EFFECT`. A concurrent duplicate must not execute
the outcome plan again or create a second effect; it must wait for the writer
transaction and then observe the committed effect.

Sequential idempotency and restart reconciliation do not by themselves prove this
concurrent boundary.

## Decision

Add a two-connection concurrency acceptance using the production
`SQLiteSandboxCalendarProvider` and a shared durable SQLite file.

- Hold the first request inside the provider outcome plan while a duplicate request
  starts against an independent SQLite connection.
- Commit exactly one `TIMEOUT_AFTER_EFFECT` attempt and one accepted calendar effect.
- Require the duplicate to return the committed event without invoking the outcome
  plan again, and record exactly one replay.
- Reopen the database in a new provider instance and prove another replay returns the
  same event without changing the attempt history or creating another effect.
- Verify positive calendar evidence from the durable effect ledger.

The test uses synthetic interview data and the local SQLite sandbox only.

## Consequences

Concurrent same-key calendar calls are now covered at the provider boundary, including
the ambiguous response-loss window and a subsequent process restart. The contract
proves one durable effect and replay-only recovery; it does not claim that a real
calendar provider offers equivalent idempotency or transactional guarantees.
