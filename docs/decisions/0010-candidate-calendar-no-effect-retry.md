# ADR 0010: Retry confirmed no-effect interview calendar actions after restart

- Status: Accepted
- Scope: Candidate workflow and SQLite sandbox calendar provider

## Context

An interview acceptance can fail before the calendar provider records an effect. The
SQLite sandbox calendar provider records the explicit `NO_EFFECT` attempt before
returning its pre-effect failure. The external-action operation is durably marked
`FAILED`, while the candidate loop preserves the already approved interview action
in the recoverable `INTERVIEW_ACCEPT` phase.

This differs from a timeout after an effect or another ambiguous provider outcome.
Those outcomes remain `RECONCILIATION_REQUIRED` and must not be blindly retried.

## Decision

After a process restart, authenticated candidate `/continue` may retry the same
previously approved interview action when its durable operation is in the retryable
pre-effect `FAILED` state and the original prepared event snapshot still matches.
The scheduling service reopens the same operation ID; it does not create a new
calendar intent or ask the provider to replay an existing effect.

The sandbox regression scripts attempt one as `NO_EFFECT` and attempt two as
`SUCCESS`. It proves the first attempt leaves the event proposed and the calendar
effect ledger empty, then proves the second attempt accepts the same event exactly
once and completes the candidate run.

## Consequences

- The original candidate approval is retained across restart.
- The same durable calendar operation ID is reused.
- Confirmed pre-effect failure can recover without a new candidate action.
- Ambiguous timeout-after-effect remains on the separate evidence-reconciliation path.
- Application submission and outbound message effects are not replayed during calendar retry.
- All regression effects are synthetic and SQLite-local; no live calendar is contacted.
