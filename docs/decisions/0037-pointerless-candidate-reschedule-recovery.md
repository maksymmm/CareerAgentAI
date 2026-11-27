# 0037 — Recover provider-applied candidate reschedules after pointer clearance

## Context

A candidate-approved reschedule can cross the calendar provider boundary and commit the
new event snapshot before the external-action operation records its terminal
`succeeded` status. If that final persistence write fails, the event is already at
the target slot and its active-action pointer has been cleared, while the operation
remains `reconciliation_required`.

A browser/runtime restart must not hide this operation as absent, retry the provider,
or restore a prior candidate attestation.

## Decision

Keep pointerless recovery restricted to the existing owner/event/version-bound
`calendar.reschedule` operation in `reconciliation_required`. The candidate's
owner-scoped discovery list may expose only the allowlisted event snapshot, source
version, and coarse operation status. Selecting a discovery row must still reload the
owned event and the authoritative per-event pending endpoint.

Accept the already-applied target only when the event is exactly at source version + 1,
has `reschedule_requested` status, and matches the persisted target instants and
timezone. Require the complete source snapshot and deterministic operation identity.
Reconciliation remains evidence-only and requires a fresh explicit candidate
attestation; it must not invoke or replay the calendar provider.

## Invariants

- The active-action pointer may be null only for the exact provider-applied target
  recovery state; mismatched or duplicate operations fail closed.
- Discovery, event detail, and pending restoration are read-only and provider-I/O-free.
- Candidate attestation is never restored across a runtime/browser restart.
- Positive reconciliation requires matching durable sandbox calendar evidence.
- One provider attempt and one effect remain durable; replay count stays zero.

## Verification

A Chromium regression uses authenticated loopback WSGI, the real candidate dashboard,
the SQLite sandbox calendar provider, and a SQLite trigger that rejects only the
terminal success write. It restarts the candidate runtime/browser over the same
database, discovers the pointerless operation, requires fresh attestation, and proves
evidence-only reconciliation completes without a second provider attempt or effect.
