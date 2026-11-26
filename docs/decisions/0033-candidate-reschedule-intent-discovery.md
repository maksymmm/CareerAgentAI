# ADR 0033: Owner-scoped pending interview reschedule discovery

- Status: Proposed
- Date: 2026-11-26
- Scope: Candidate reschedule API and SQLite sandbox

## Context

The per-event reschedule pending endpoint restores an ambiguous target only after a
candidate already knows the event ID. A candidate returning after a browser restart
must be able to discover pending reschedules without scanning every event manually
or receiving another candidate's internal operation data.

The operation ledger is shared by candidates. Discovery must filter through the
authenticated event owner before interpreting or exposing an operation, preserve the
existing event-scoped execution lease, and avoid promoting a live request or replaying
the calendar provider.

## Decision

Add authenticated, read-only GET
`/v1/candidate/schedule-event/reschedule-intents` with bounded keyset pagination.

- Join ambiguous `calendar.reschedule` operations to schedule events owned by the
  authenticated candidate before parsing the full operation payload.
- Bound the ambiguous operation scan to 1,000 rows and fail closed when that bound is
  exceeded.
- Reject duplicate JSON members, non-finite values, mismatched deterministic operation
  IDs, invalid target slots, stale source snapshots, unexpected event pointers, and
  multiple pending operations for one event.
- Accept either the untouched source event or the exact target event at source
  version + 1, matching the existing per-event recovery contract.
- Report an allowlisted coarse status: `running`, `in_progress`, or
  `reconciliation_required`. An active sidecar lease is read-only evidence that the
  request is still running; discovery never promotes it.
- Return only a candidate-safe event snapshot, the operation's source version, and
  the coarse status. Never return candidate, application, provider, operation, or
  lease identifiers.
- Use stable event-ID keyset pagination with a maximum page size of 100 and no
  temporary sort in the SQLite operation scan.
- Keep the existing per-event pending endpoint authoritative. Selecting a discovered
  event must reload its event snapshot and per-event pending intent before enabling
  independent provider attestation.

## Consequences

Candidates can discover ambiguous reschedules after restart without knowing internal
identifiers. The collection endpoint performs no writes and no provider I/O; it is a
discovery hint, not an approval or reconciliation token. Browser-dashboard integration
remains a separate follow-up so the existing event-selection and attestation lifecycle
continues to gate all consequential actions.
