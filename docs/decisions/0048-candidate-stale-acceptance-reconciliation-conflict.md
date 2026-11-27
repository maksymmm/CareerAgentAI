# Decision 0048: Surface stale candidate interview reconciliation as a conflict

- Status: Accepted
- Date: 2026-11-27

## Context

A provider-applied candidate interview acceptance can remain
`reconciliation_required` when the external-action terminal success write fails
after the sandbox calendar effect and event transition commit. A later scheduling
cycle can move the event away, return it to the original slot, and accept it again.
The old provider effect still proves that the old operation once succeeded, but it
does not prove that the current event version belongs to that operation.

The scheduling service already rejects this stale version, but the generic
`ValueError` crossed the autonomous career loop boundary and was exposed by the
candidate WSGI API as a sanitized 500 instead of a known state conflict.

## Decision

Represent this exact stale-event condition as
`SchedulingOperationConflict`. The autonomous career loop translates that typed
domain conflict into `CareerLoopConflictError`, preserving the candidate API's
existing `409 approval_conflict` contract. Do not catch broad `ValueError`
exceptions: malformed persisted state and unexpected programming errors must not be
misclassified as candidate conflicts.

A conflict leaves the old operation `reconciliation_required`, keeps the current
event and provider ledgers unchanged, and never retries or replays the calendar
provider. The candidate must refresh the durable run state and obtain a new
authoritative decision path.

## Verification

An HTTP/SQLite regression drives a real candidate run through application, message,
and interview approval using only sandbox providers. A SQLite trigger rejects the
terminal success write after the calendar effect commits. Subsequent approved
reschedule-away, reschedule-return, and re-accept actions advance the event to
version 5 while restoring the original slot. After runtime restart, the old
candidate attestation returns `409 approval_conflict`; the old operation remains
reconciliation-required, the run remains at the reconciliation gate, and four
calendar attempts/effects remain unchanged with zero replays.

The CI workflow independently enforces the two-decimal coverage threshold after
the full pytest coverage run, including failed pytest runs unless the job is
cancelled.
