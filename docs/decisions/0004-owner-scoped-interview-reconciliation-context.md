# ADR 0004: Owner-scoped interview reconciliation context

## Status

Proposed

## Context

The autonomous candidate workflow durably stores an interview/calendar action when a
provider outcome is ambiguous. The run status projection intentionally exposes only a
minimal phase and state version. After a browser reload, the candidate can discover that
the run is awaiting interview reconciliation but cannot reconstruct which employer,
event, local time or timezone the pending operation concerns.

The ordinary approval prompt is not appropriate for this state: reconciliation is a
separate evidence-resolution contract, not a new approval or permission to retry.

## Decision

Expose an authenticated GET /v1/candidate/runs/{run_id}/interview-reconciliation
projection. It is available only while the owner-scoped run is in
interview_reconciliation with a pending RECONCILE_INTERVIEW_RESPONSE action.

The projection:

- returns the current run state version and a minimal human-readable schedule snapshot;
- verifies the schedule event belongs to the authenticated candidate and the run's
  application before exposing any event details;
- returns no internal external-action operation ID, provider event ID, credentials,
  provider evidence or raw persisted action mapping;
- performs no provider I/O and does not mutate the run or schedule;
- is fetched by the dashboard only after a fresh status read;
- enables reconciliation controls only when the context run ID, phase and state version
  exactly match the status snapshot;
- keeps the existing POST reconciliation route version-bound and evidence-gated.

A stale or no-longer-pending run returns a conflict. Missing or foreign-owned runs remain
indistinguishable as not found.

## Consequences

Candidates can recover the exact pending interview intent after a browser reload without
restarting the workflow or guessing which event requires investigation. The GET endpoint
is read-only with respect to provider effects and durable workflow state. Provider evidence
must still be independently verified before the candidate submits the existing POST
reconciliation decision. This does not enable blind retries or contact a real calendar.
