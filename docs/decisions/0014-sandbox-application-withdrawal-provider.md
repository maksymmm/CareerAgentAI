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

The adapter binds an operation ID to a fingerprint containing the application ID and
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
