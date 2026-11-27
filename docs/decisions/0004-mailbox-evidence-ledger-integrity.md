# ADR 0004: Validate mailbox evidence against all durable provider ledgers

- Status: Accepted
- Date: 2026-11-27

## Context

The SQLite communication sandbox persists an operation intent, ordered delivery
attempts, a provider-side message, and a delivery record in separate tables.
Reconciliation previously trusted the delivery row and referenced message alone.
An orphaned delivery, mismatched fingerprint, or missing delivery after a recorded
`SUCCESS`/`TIMEOUT_AFTER_EFFECT` attempt could therefore be interpreted without
checking the rest of the durable evidence.

The mailbox evidence contract must fail closed when these ledgers disagree. It must
also distinguish a valid no-effect history from an incomplete or corrupted record.

## Decision

`verify_delivery` reads the intent, ordered attempts, delivery row, replay counter,
and referenced message under one SQLite writer snapshot.

A consistent never-attempted operation is absent only when all operation ledgers are
empty. Once attempts exist, a durable intent is required, attempt numbers must be
contiguous from one, and every outcome must be a recognized sandbox outcome.

A confirmed absence requires no delivery row and no effect-producing attempt. A
positive delivery requires exactly one final effect-producing attempt, matching
intent/delivery/message fingerprints, a recognized `send` or `reply` action, a
non-negative replay counter, and a valid outbound provider message. Reply evidence
must retain its parent-message identifier.

Malformed, orphaned, incomplete, or contradictory records raise
`CommunicationReconciliationEvidenceError`; they are never converted to success
or confirmed absence. Evidence lookup remains read-only and rejects an existing
transaction or a busy writer lock.

## Consequences

- A timeout after a simulated delivery cannot be reconciled from a delivery row whose
  intent, attempt history, or message has been removed or altered.
- A no-effect retry is available only after the provider proves a consistent absence.
- Reconciliation does not add provider attempts, deliveries, or replay increments.
- The provider remains a no-network SQLite sandbox; no real recruiter message or
  production credential is introduced.
- The same cross-ledger integrity rule remains to be applied to application-submission
  evidence.
