# ADR 0028: Candidate definite-no-effect submission recovery after restart

- Status: Proposed
- Date: 2026-11-26

## Context

A candidate may approve an exact application artifact, after which the sandbox
provider can prove that the first attempt produced no effect. The durable external
action is then FAILED and the candidate run remains in application-submit recovery.
The already approved artifact and operation identity must survive a process restart,
while a page load or status read must never trigger a retry.

## Decision

Add an HTTP-level acceptance regression using RuntimeCandidateApp and the local
SQLite sandbox submission provider.

- Start an owner-scoped candidate run and record the ordinary application approval.
- Script the first provider attempt as NO_EFFECT and the second as SUCCESS.
- Close the first runtime after the recoverable no-effect result.
- Reopen the same SQLite database and prove owner-scoped status and unavailable-prompt
  reads do not invoke the provider or change its attempt/effect ledger.
- Require an explicit authenticated POST to the existing continue route before the
  same approved operation is retried.
- Assert the same idempotency key is used for both attempts, exactly one provider
  effect is committed, the application tracker advances from saved to applied, and a
  further restart/continue after COMPLETE creates no additional attempt.

The scenario uses synthetic job/application data, loopback-free WSGI calls, and the
no-network SQLite sandbox provider. The explicit continue request is the candidate's
recovery action; the prior approval remains bound to the unchanged application intent.

## Consequences

The candidate workflow now has a regression for definite no-effect recovery across a
runtime restart, including a read-only recovery boundary and exactly-once sandbox
effect assertion. This proves only the local SQLite sandbox contract, not retry
semantics of a future external provider.
