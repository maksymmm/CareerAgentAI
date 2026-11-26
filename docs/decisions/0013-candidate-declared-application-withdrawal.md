# ADR 0013: Candidate-declared application withdrawal is tracker-only

## Status

Proposed

## Context

The application aggregate already supports a terminal `withdrawn` lifecycle state,
but the candidate sandbox exposes only read-only application snapshots and history.
A candidate therefore cannot record a deliberate withdrawal through the authenticated
candidate workflow. The existing application submission claim must also remain
exclusive: a withdrawal must not race a submission that has already been claimed.

A local tracker transition is not the same operation as contacting an employer.
An application may already have been submitted, and the tracker cannot infer that an
employer has received or processed a withdrawal request.

## Decision

Expose an authenticated, owner-scoped
`POST /v1/candidate/application/withdraw` operation. It requires an explicit
`confirm_withdrawal: true` and the exact positive application `state_version`
observed by the candidate.

The write path:
- scopes the application row by both application ID and authenticated owner before
  decoding private aggregate children;
- permits only `saved`, `applied`, `interview`, and `offer` states;
- uses the existing SQLite repository compare-and-swap update and rejects an active
  submission claim;
- appends one durable `withdrawn` timeline event while preserving prior submission
  operation links;
- returns a minimal versioned result and requires a fresh read after conflicts.

The candidate dashboard labels this action as a local tracker update and explains
that no withdrawal notice is sent and no previously submitted application is
retracted.

## Consequences

The operation performs no provider call, outbound message, application retraction,
calendar mutation, or compensation. It records only the candidate's local status
decision. Any future employer-facing withdrawal must be a separate provider-backed
external action with its own explicit approval, durable idempotency key, evidence,
and reconciliation contract.
