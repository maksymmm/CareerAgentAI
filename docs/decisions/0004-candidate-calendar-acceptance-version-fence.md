# ADR 0004: Bind candidate calendar acceptance to the approved event version

- Status: Accepted
- Date: 2026-11-27

## Context

The calendar adapter already binds a prepared acceptance intent to the event version
read when the intent is created, and the SQLite repository uses that version in its
final claim compare-and-swap. However, `SchedulingService.accept` did not accept the
version that the candidate had actually reviewed. If a reschedule completed before the
service read the event, the service could create a new acceptance intent from the newer
slot even though the candidate approved an older snapshot.

## Decision

- Add an optional, strictly bounded `expected_version` argument to
  `SchedulingService.accept`. Candidate-facing callers must pass the exact version
  from the displayed owner-scoped event snapshot.
- Reject a stale version before creating a new external-action operation.
- Bind new acceptance intents to both `candidate_id` and `event_version`.
- On idempotent reuse, reject a different approved version or candidate identity.
- Retain the repository's existing expected-version claim as the final guard against
  a concurrent update after the service has read the approved snapshot.
- Preserve compatibility for trusted internal callers that approve the current event
  at invocation time; they may omit `expected_version`.

## Verification

Regression tests cover invalid versions, stale approval after a reschedule, candidate
and operation-version rebinding, and a concurrent event-version advance between the
service snapshot read and the SQLite provider claim. All provider calls use the local
fake/sandbox adapter; no live calendar is contacted.
