# ADR 0017 — Candidate browser recovery across runtime restart

## Status

Accepted

## Context

The candidate workflow persists application submission, message delivery, interview
approval, calendar provider evidence, and the current run phase in SQLite. Existing
service-level recovery tests prove that a restarted runtime can reconcile a
calendar TIMEOUT_AFTER_EFFECT outcome. The candidate browser must also recover
that same state without relying on tab memory or accidentally replaying an
already committed provider effect.

A browser reload and a process restart are separate failure boundaries. Passing
one does not prove the other.

## Decision

The candidate dashboard recovery contract is:

1. A candidate run is identified by the durable owner-scoped run ID returned by
   the start operation.
2. The bearer token and selected run remain tab-local. Clearing the session and
   reloading the page must not restore either value from browser storage.
3. A new RuntimeCandidateApp may reopen the same SQLite database after the
   previous runtime has closed.
4. The candidate re-enters the bearer token and durable run ID, then obtains a
   fresh owner-scoped status snapshot.
5. An INTERVIEW_RECONCILIATION snapshot enables only the version-bound
   reconciliation controls. The candidate must provide the independently
   verified provider event ID and explicitly attest to the displayed version.
6. The authenticated reconciliation route validates the durable sandbox
   provider evidence. A timeout-after-effect is reconciled from the existing
   effect; it is not retried as a new calendar action.
7. The terminal dashboard snapshot must report COMPLETE, while the durable
   ledgers retain exactly one application submission, one outbound sandbox
   message, one calendar attempt, and one calendar effect with zero replays.

The browser acceptance uses a local WSGI server and SQLite sandbox providers
only. It does not contact an employer, send a real message, or mutate a real
calendar.

## Verification

The regression test_candidate_browser_recovers_calendar_timeout_after_runtime_restart_without_replay
drives the actual RuntimeCandidateApp, closes and recreates it against the
same SQLite file, reloads the Chromium dashboard on the same local origin, and
submits the explicit provider-evidence attestation. It then inspects the
candidate state, schedule event, external-action operations, and all three
provider ledgers.

The CI workflow must run the full pytest coverage command and a separate
python -m coverage report --fail-under=90 step after pytest. Both gates apply
to the exact reviewed commit.

## Consequences

The browser can recover a durable candidate workflow after both a tab/session
reset and an application-runtime restart. Credentials are never persisted by
the dashboard, and recovery does not imply permission to repeat a provider
effect. A provider outcome that cannot be independently verified remains
unresolved and must not be blindly retried.
