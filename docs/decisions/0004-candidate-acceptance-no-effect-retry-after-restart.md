# Decision 0004: Retry confirmed no-effect candidate acceptance after restart

- Status: Accepted
- Date: 2026-11-26

## Context

Candidate interview acceptance persists a deterministic operation identity bound to
the authenticated candidate, event, and displayed event version. The SQLite sandbox
calendar provider can return a definite NO_EFFECT result before creating a calendar
effect. The external-action ledger records that attempt as failed, and the dashboard
allows a deliberate retry of the same event-version operation.

The existing browser coverage exercised this transition with a mocked candidate loop,
while the real-provider restart test covered only TIMEOUT_AFTER_EFFECT. The real SQLite
provider path therefore lacked an end-to-end proof that a definite no-effect result
remains safely retryable after the candidate runtime is replaced.

## Decision

Keep the existing deterministic operation ID and require a new explicit candidate
acceptance click after restart. The restarted runtime must re-read the event and the
owner-scoped operation status, validate the unchanged prepared source snapshot, reopen
the failed operation through the existing compare-and-swap lifecycle, and invoke the
SQLite sandbox provider with the same idempotency key.

The retry is permitted only because the first sandbox provider attempt durably recorded
NO_EFFECT; it is not an ambiguous reconciliation and does not create a second operation.
A changed event version or source snapshot remains a conflict. The runtime must not
infer a retry from page load or status lookup.

## Verification

A Chromium acceptance regression starts RuntimeCandidateApp against a SQLite database,
records a real sandbox NO_EFFECT, stops the first runtime, and starts a second runtime
with a provider plan that succeeds only on attempt two. The candidate must explicitly
click Accept again. Assertions prove the same version-bound operation reaches
succeeded, the provider attempts are exactly no_effect then success, the event advances
once to accepted version 2, and the database contains one effect with zero replays.
No live calendar adapter or production credential is used.
