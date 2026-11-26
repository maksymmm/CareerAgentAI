# ADR 0004: Concurrent timeout-after-effect communication replay

- Status: Proposed
- Date: 2026-11-26

## Context

The SQLite communication provider persists an idempotency intent, provider attempt,
outbound message, and delivery evidence in one write transaction. Existing tests cover
sequential replay and restart reconciliation, but they do not prove that two independent
SQLite connections racing on the same operation key observe one committed delivery
when the first provider response is lost after the effect.

## Decision

Add a provider-boundary concurrency regression that:

1. Holds the first send inside its SQLite write transaction after entering the outcome
   plan.
2. Starts a same-key send from a second independently opened SQLite connection.
3. Commits exactly one TIMEOUT_AFTER_EFFECT delivery from the first request.
4. Requires the duplicate to wait for the transaction, return the exact persisted
   outbound message, and increment the replay counter without invoking the outcome plan.
5. Opens a third provider connection after restart, verifies positive delivery evidence,
   and replays the same operation without adding an attempt or a second message.

The regression requires one attempt, one delivery row, one outbound message, and two
counted replays (the concurrent duplicate and the post-restart replay).

## Safety

The test uses synthetic addresses, a local SQLite database, and no-network sandbox
provider code. It does not configure production credentials or contact a recipient.
