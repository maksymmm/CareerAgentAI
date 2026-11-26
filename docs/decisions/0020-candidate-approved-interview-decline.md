# ADR 0020: Candidate-approved interview decline through the sandbox provider

- Status: Proposed
- Date: 2026-11-26

## Context

Candidate schedule events were exposed through owner-scoped read-only snapshots, while
the scheduling domain and SQLite sandbox calendar provider already supported a durable
`calendar.decline` operation. The candidate dashboard had no explicit, version-fenced
way to decline a proposed event or recover when the provider response was lost.

A decline is consequential even in a sandbox: it must be candidate-approved, bound to
the exact event snapshot, and never retried after an ambiguous provider outcome.

## Decision

Add two authenticated candidate endpoints:

- `POST /v1/candidate/schedule-event/decline`
- `POST /v1/candidate/schedule-event/decline-reconciliation`

The initial request requires an exact event ID, positive event state version, and
`confirm_decline: true`. The runtime first performs owner-scoped read-only lookup,
then rechecks the version on the writable request connection. The operation ID is
derived server-side from the authenticated owner, event ID, and displayed version;
it is never returned to the browser.

The operation uses `SchedulingService.decline` and
`SQLiteSandboxCalendarProvider` only. A definite no-effect outcome remains retryable
only after a fresh event read and a new explicit candidate confirmation. An ambiguous
outcome returns `202` and blocks another provider call until reconciliation verifies
the sandbox provider's durable evidence. The browser never supplies an internal
operation ID or provider event ID.

The response is a minimal event/status receipt. It excludes owner IDs, operation IDs,
provider event IDs, and application IDs.

## Consequences

- Candidate approval is explicit and version-fenced.
- A stale event cannot be declined based on an old dashboard snapshot.
- Replaying the same version-bound request cannot create a second provider effect.
- A timeout after effect is reconciled from SQLite evidence after runtime/browser restart.
- The candidate dashboard distinguishes confirmed, no-effect, and ambiguous outcomes.
- This remains a sandbox-only workflow; no network calendar provider is configured.
