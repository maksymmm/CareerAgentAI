# ADR 0041: Verify candidate cancellation through the browser after restart

- Status: Proposed
- Date: 2026-11-26

## Context

The candidate run cancellation API and dashboard control are covered by service,
HTTP, and SQLite restart regressions. Those tests do not prove that the real browser
loads a fresh owner-scoped status snapshot, enables cancellation only at an ordinary
approval gate, submits the displayed state version, and keeps the terminal decision
after a new runtime and browser session are created.

## Decision

Add a Chromium acceptance test against `RuntimeCandidateApp` and the authenticated
loopback WSGI boundary:

1. Start a synthetic candidate run at the application approval gate.
2. Authenticate in the actual candidate dashboard and load the owner-scoped run list.
3. Refresh the durable status and verify the cancellation control becomes enabled
   only for the fresh ordinary approval snapshot.
4. Cancel through the dashboard control and verify the terminal cancelled snapshot.
5. Close the browser and first runtime, then create a new runtime and browser session
   over the same SQLite database.
6. Verify the run remains cancelled, the cancellation control remains disabled, and
   an authenticated continuation request returns the same terminal state.
7. Inspect durable provider ledgers and prove cancellation caused no application,
   communication, calendar, or external-action effect.

The test lives under top-level `tests/` so synchronous Playwright browser execution is
not counted as application-package statement coverage. CI enforces both pytest
coverage and an independent two-decimal coverage CLI threshold.

## Safety

Only synthetic candidate/job data, authenticated loopback HTTP, Chromium, and the
SQLite sandbox composition are used. No production credentials, real applications,
outbound messages, live providers, or calendar mutations are involved.
