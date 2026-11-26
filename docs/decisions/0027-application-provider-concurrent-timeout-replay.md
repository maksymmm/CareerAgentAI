# ADR 0027: Concurrent application submission timeout-after-effect replay

- Status: Proposed
- Date: 2026-11-26

## Context

The SQLite sandbox application provider already covers same-key concurrent submissions
and sequential timeout-after-effect recovery across restart. Those tests do not prove
that a duplicate arriving while the first provider transaction is still open observes
the committed submission after the first caller loses its response.

This is the application-submission counterpart to the communication and calendar
provider concurrency regressions. Without it, the three durable provider ledgers do
not share the same tested contract for the ambiguous response-loss window.

## Decision

Add a deterministic two-connection regression using independent
`SQLiteSandboxSubmissionProvider` instances over one temporary SQLite database.

- Hold the first `submit` call inside its injected outcome plan while its
  `BEGIN IMMEDIATE` transaction is open.
- Start a second same-key, same-intent submission on another connection and prove
  neither request completes while the first transaction is held.
- Release the first plan as `TIMEOUT_AFTER_EFFECT`; require exactly one caller to
  receive the timeout and the duplicate to return the exact persisted submission.
- Reopen the database with a fresh provider, verify positive submission evidence, and
  replay the same operation without invoking the outcome plan or creating another
  effect.
- Assert one provider attempt, one ledger row, and exactly two counted replays:
  the concurrent duplicate and the post-restart replay.

The test uses synthetic application identifiers and a local temporary SQLite file.
No network provider, production credential, real application, or employer contact is
involved.

## Consequences

The application provider now has an explicit regression for the same-key concurrent
timeout-after-effect boundary already exercised by the communication and calendar
sandbox providers. This proves only the local SQLite sandbox contract; it does not
claim equivalent idempotency guarantees from any future external provider.
