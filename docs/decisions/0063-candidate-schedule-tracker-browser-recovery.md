# ADR 0063: Candidate schedule tracker browser recovery

- Status: Accepted
- Date: 2026-11-28
- Scope: Candidate-facing read-only schedule-event tracking in the sandbox browser

## Context

The candidate schedule projection already provides bounded owner-scoped pages and
minimal event details. The authenticated WSGI routes and browser client also expose
status filtering, keyset pagination, exact local-time rendering, and a per-event
version floor. The remaining production-readiness gap was a browser-level proof that
joins those contracts across concurrent event advancement and runtime reconstruction.

A projection-only test cannot prove that the browser preserves the selected event's
newest observed version, rejects a stale later detail response, keeps foreign event
data private, or avoids initializing provider/action storage while rendering a
candidate's schedule.

## Decision

Add a Chromium acceptance that:

1. Seeds 26 accepted events and one proposed event for a synthetic candidate, plus
   one foreign event whose timezone data is intentionally malformed.
2. Loads the authenticated schedule dashboard through the first 25-row keyset page
   and the final one-row page.
3. Applies the accepted/proposed status filters and verifies that the filter remains
   owner-scoped.
4. Captures event version 2 from the list, advances the same event to version 3 from
   an independent writable connection, and verifies that the browser accepts the
   newer detail.
5. Returns a stale version-2 detail after version 3 has been observed and verifies
   that the browser clears the stale detail instead of rolling the selected event
   backward.
6. Closes the first RuntimeCandidateApp and WSGI server, opens a second runtime over
   the same SQLite file, and verifies that the event remains available at version 3.
7. Requests a foreign event through the authenticated HTTP route and requires the
   same not-found response as an absent event.
8. Verifies that private provider/application/action identifiers never reach the
   browser, the schedule section exposes no consequential action controls, the
   database bytes remain unchanged during reads, and no provider or external-action
   tables are initialized.

The browser is limited to read-only schedule operations. It cannot accept, decline,
reschedule, cancel, or modify a calendar event.

## Consequences

- Candidate schedule pagination, filtering, detail, owner isolation, version
  monotonicity, and runtime restart are covered through the real browser and WSGI
  boundary.
- The regression proves that a concurrent newer event is accepted while a later
  stale response is rejected.
- The no-write assertion covers the complete browser/runtime read path, not only
  the projection class.
- Only synthetic events and local rollback-journal SQLite are used.
- No production credentials, real applications, recruiter messages, network
  providers, or live calendar mutations are involved.

## Verification

The exact-head GitHub Actions workflow must pass the complete pytest suite, the
pytest-cov 90% gate, and the independent coverage CLI 90% gate. Codex code and
security reviews are required for the exact final commit before merge.
