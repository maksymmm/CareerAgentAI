# ADR 0014: Durable sandbox application-withdrawal provider

- Status: Proposed
- Date: 2026-11-25

## Context

The candidate application tracker can record a candidate-declared local withdrawal,
but that operation intentionally does not contact an employer or retract a submitted
application. Before a separately approved employer-facing workflow can be considered,
the repository needs a deterministic provider boundary that can exercise the same
failure modes as submission, communication, and calendar providers without network
access or production credentials.

A withdrawal has two materially different failure classes. A confirmed no-effect
attempt may be retried with the same immutable intent. A timeout after the provider
effect must be reconciled from durable evidence and must never create a second effect.

## Decision

Add `SQLiteSandboxApplicationWithdrawalProvider` as a provider-only integration
primitive. It is permanently no-network and reports `is_dry_run = True`.

Before binding any withdrawal intent, the adapter verifies an exact prior row in
`sandbox_submission_ledger` for both the application ID and provider submission ID
while holding the same writer lock. A missing submission table or mismatched/absent
row fails before intent, attempt, or outcome-plan consumption. The adapter then binds
an operation ID to a fingerprint containing the application ID and
the previously recorded provider submission ID. A unique application constraint
prevents a second withdrawal operation from being bound to the same application.
Attempts and effects are stored separately and transactionally.

The adapter supports deterministic `SUCCESS`, `NO_EFFECT`,
`INTERRUPTED_BEFORE_EFFECT`, and `TIMEOUT_AFTER_EFFECT` outcomes. A no-effect
attempt is durable and can be retried only with the same operation ID and intent.
A timeout-after-effect commits one effect before raising; a subsequent same-key call
returns the existing receipt and cannot create another effect.

Evidence lookup acquires `BEGIN IMMEDIATE` before inspecting the ledger. A busy
writer or ambient caller-owned transaction raises an error; it is never translated
into `ABSENT_CONFIRMED`. Provider construction rejects an ambient transaction
before schema DDL and never commits caller-owned work.

The generic external-action service serializes same-process execution calls by
operation ID so a second live worker does not misclassify the first worker's
`IN_PROGRESS` state as a crashed execution. The in-memory lock does not survive a
process restart: a recovered `IN_PROGRESS` operation remains ambiguous and must be
resolved from provider evidence. Cross-process duplicate-effect safety remains the
responsibility of the provider's durable idempotency ledger.

## Scope and safety boundary

This ADR does not wire employer withdrawal into the candidate HTTP API, runtime,
dashboard, or live provider configuration. The existing candidate withdrawal action
remains tracker-only. No employer is contacted, no message is sent, and no calendar
event is changed. A future provider-backed candidate workflow requires its own
explicit approval contract, owner/version fencing, durable external-action operation,
and independent review.

## Consequences

- Restart-safe tests can distinguish confirmed no-effect retry from ambiguous
  timeout-after-effect reconciliation.
- The operation ID and application ID cannot be rebound to a different intent.
- Concurrent identical calls produce one effect and at most one provider attempt.
- SQLite writer contention fails closed before plan consumption or durable mutation.
- The sandbox provider is a test/integration seam, not evidence that a real employer
  withdrawal has occurred.
