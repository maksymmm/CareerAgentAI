# ADR 0006: Single-winner candidate application reconciliation

Status: accepted. Scope: authenticated candidate sandbox reconciliation over
request-local SQLite connections.

## Context

Application provider evidence can outlive the runtime that initiated a submission.
After restart, two browser tabs or retried HTTP requests may submit the same
version-bound reconciliation observation at nearly the same time. Provider-level
idempotency alone does not prove that the candidate workflow state transition is
single-winner.

## Decision

- Exercise the real RuntimeCandidateApp composition with a local
  SQLiteSandboxSubmissionProvider and a scripted TIMEOUT_AFTER_EFFECT.
- Close the first runtime and reopen the same SQLite database before reconciliation.
- Submit two authenticated application-reconciliation POST requests concurrently,
  each using the same observed state version and verified provider submission ID.
- Require exactly one 200 OK terminal transition and one sanitized 409 Conflict.
- Verify the candidate run is terminal, the durable external action is succeeded,
  and the provider ledger and attempt history remain unchanged.
- Use the durable owner-scoped run ID returned by runtime start for every HTTP route.

## Consequences

The test proves the candidate API's optimistic state-version boundary remains
single-winner across independent request-local SQLite connections and a process
restart. It also guards against a duplicate reconciliation request being mistaken
for a second provider submission or incrementing provider replay accounting.

All effects are synthetic and local to SQLite. No network provider, production
credential, real application submission, outbound message, or calendar mutation is
used.
