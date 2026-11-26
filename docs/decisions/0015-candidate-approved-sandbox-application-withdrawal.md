# ADR 0015: Candidate-approved sandbox application withdrawal

- Status: Proposed
- Date: 2026-11-25

## Context

The application tracker can record a candidate-declared local withdrawal, and the
sandbox withdrawal adapter can model an employer-side effect. Neither capability
alone completes a candidate-authorized provider workflow.

The provider-backed action must be a distinct explicit approval. It must be tied to
an owned application, the current application state version, and exactly one durable
successful application submission. The candidate must not supply a provider
submission ID or operation ID.

## Decision

Expose authenticated
`POST /v1/candidate/application/withdraw-employer` with a strict schema-versioned
body containing the application ID, expected state version, and
`confirm_employer_withdrawal: true`.

The runtime resolves the prior successful `application.submit` operation from the
application's durable external-action links. It derives a stable withdrawal
operation ID from the authenticated owner and application ID, prepares an
`application.withdraw` external-action intent, and invokes only
`SQLiteSandboxApplicationWithdrawalProvider`.

The provider independently verifies the exact application ID and provider
submission ID against the sandbox submission ledger under its writer lock. The
candidate service then verifies the provider withdrawal evidence before recording
the application as `WITHDRAWN` through the repository's versioned update.

A definite `NO_EFFECT` remains retryable only after a new explicit candidate
request. An interrupted or timeout outcome is reconciled from provider evidence;
a confirmed effect is never replayed. A recent `IN_PROGRESS` operation is left
alone for a five-minute grace period. Missing, foreign, stale, malformed, busy,
or contradictory evidence fails closed.

The SQLite repository applies the owner predicate before decoding private
application children. Other repository adapters retain a conservative default
owner check and may override it with an owner-first query.

## Safety boundary

This endpoint is sandbox-only. It never calls a live employer, network provider,
email service, or calendar. The response reports a confirmed SQLite sandbox
effect, not a real-world employer acknowledgement. The existing tracker-only
withdrawal remains a separate local action and cannot be mistaken for provider
confirmation.

No production credentials, real applications, outbound messages, or calendar
mutations are used.

## Consequences

- Candidate approval is explicit, separate from local tracker withdrawal.
- Provider submission identity is derived from durable server-side history.
- One stable operation ID is bound to one owner/application pair.
- A successful provider effect is durably reconciled before the tracker changes.
- Restart and no-effect retries reuse the same idempotency key.
- Owner isolation, state-version fencing, provider evidence, and durable audit
  history are exercised through the authenticated WSGI runtime.
