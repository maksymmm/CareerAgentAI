# ADR 0039: Candidate decline version conflicts are known stale approvals

- Status: Accepted
- Date: 2026-11-26

## Context

The candidate dashboard submits a decline for the exact event version shown in the
candidate's explicit confirmation. Another scheduling writer can advance that event
before the decline request reaches the authenticated API. The API correctly rejects
the stale version with `409 approval_conflict` before preparing an external-action
intent or invoking the calendar provider.

The browser previously treated every decline request error as an uncertain provider
outcome. That message was inaccurate for a deterministic version conflict and left
the stale event snapshot visible, although the consequential controls were disabled.

## Decision

Treat `approval_conflict` as a known stale-approval outcome:

- clear the displayed event detail and all decline/reconciliation controls;
- clear the selected event identity and selected version;
- explain that no decline was submitted;
- require a fresh authoritative event read and a new explicit candidate confirmation;
- reserve uncertain-outcome guidance for failures that may have crossed the provider
  boundary.

The CI test job also runs the independent 90% coverage CLI gate after pytest, including
when pytest reports a failure, so coverage evidence is not silently skipped.

## Verification

A Chromium acceptance regression loads proposed/version 1, records the candidate's
explicit confirmation, advances the SQLite event to reschedule_requested/version 2
through a concurrent writer, and submits the stale confirmation. It proves the UI
clears the stale snapshot, a fresh read displays version 2, a new confirmation is
required, and no decline operation, provider attempt, or provider effect is created.
