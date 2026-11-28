# ADR 0057: Fence duplicate candidate interview approvals across runtimes

- Status: Accepted
- Date: 2026-11-28

## Context

The candidate interview approval endpoint delegates to a request-local loop and
SQLite-backed scheduling service. The candidate prompt is bound to the exact event
version, but the complete runtime boundary also needs evidence that a stale duplicate
HTTP approval from another runtime cannot reach the calendar provider while the first
worker is executing the approved action.

A service-only provider test does not prove that the candidate API, durable run
version, execution lease, scheduling service, application tracker, and sandbox
calendar provider compose correctly.

## Decision

Add an authenticated WSGI regression using two independent RuntimeCandidateApp
instances over the same file-backed SQLite database.

- Seed one synthetic, owner-scoped interview approval prompt at event version 1 and
  an already-applied synthetic application.
- Read the candidate prompt through the published approval GET route and submit its
  exact fingerprint and state version through the published POST route.
- Pause the first request immediately before the SQLite sandbox calendar provider
  writes its effect, after the candidate run has durably advanced beyond the approval
  snapshot.
- Submit the same approval from a second runtime and require HTTP 409 before a
  second provider call.
- Release the first request and require the candidate run to complete, the event to
  become accepted, and the application tracker to advance to interview.
- Reopen a third runtime and verify the terminal run state without another provider
  action.
- Inspect the durable operation, attempt, effect and replay ledgers directly.

## Consequences

The candidate-facing HTTP/runtime composition now has a regression for duplicate
interview approval across independent runtime instances. The test proves one
calendar provider call, one durable attempt, one effect, zero replays, and a
restart-stable completed candidate run. It complements the service/provider
concurrency regression in PR #400; it does not claim that a real calendar provider
offers transactional or idempotency guarantees.

All data and provider effects remain synthetic and local to the no-network SQLite
sandbox. No production credentials, real applications, outbound messages, or live
calendar actions are used.
