# ADR 0004: Owner-scoped interview reconciliation intent

## Status

Proposed with the candidate sandbox recovery implementation.

## Context

An ambiguous calendar response leaves a durable candidate run in
`interview_reconciliation`. The run status list and state-versioned POST resolver
survive a browser reload, but the sandbox previously required the candidate to reconstruct
the pending event context manually. The ordinary approval prompt intentionally rejects
reconciliation actions, and exposing its private operation details would violate the
candidate-facing data boundary.

## Decision

Add an authenticated, read-only
`GET /v1/candidate/runs/{run_id}/interview-reconciliation` projection. It:

- loads the run only for its authenticated owner;
- verifies the pending action, durable `calendar.accept` operation, prepared event
  snapshot, current event version, candidate/application linkage and persisted success
  result when present;
- returns only the run state version, local event details and allowed reconciliation
  outcomes;
- never returns the internal operation ID or provider event ID;
- performs no provider I/O and does not retry the action;
- restricts the no-effect outcome once the local event is already accepted.

The existing POST resolver remains authoritative and checks the exact run state version
before reconciliation. Provider evidence remains the final arbiter of the claimed outcome.

## Consequences

A reloaded browser can recover the pending intent from the selected owner-scoped run.
Stale or inconsistent operation/event state fails closed. Candidate-visible responses
remain minimal and do not become provider credentials.
