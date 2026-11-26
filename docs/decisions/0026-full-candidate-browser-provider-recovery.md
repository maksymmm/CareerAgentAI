# ADR 0026: Full candidate browser recovery across sandbox providers

- Status: Proposed
- Date: 2026-11-26

## Context

The candidate workflow performs three consequential sandbox effects in order:
application submission, communication delivery, and calendar acceptance. Existing
regressions exercise application/message recovery and calendar recovery separately,
while the full provider round trip is covered at the runtime/API layer. A candidate
must be able to recover the complete journey through the actual browser dashboard,
including loss of each provider response and multiple process restarts.

## Decision

Add one Chromium acceptance journey that forces each sandbox provider to commit a
single TIMEOUT_AFTER_EFFECT result:

1. Application submission commits once; a fresh runtime and browser reconcile its
   durable provider submission ID without replay.
2. Communication delivery commits once; a second fresh runtime and browser reconcile
   its durable delivery evidence without redelivery.
3. Calendar acceptance commits once; a third fresh runtime and browser reconcile the
   exact provider event ID and complete the candidate run without a second calendar
   effect.

Each restart reuses the same SQLite database but creates a new RuntimeCandidateApp,
WSGI server, browser page session, and in-memory credentials. The final assertions
bind the terminal candidate state to one successful external action per provider,
one durable effect per provider, one attempt per provider, and zero provider replays.

The CI workflow also runs python -m coverage report --fail-under=90 after the
full pytest coverage command, so the independent coverage CLI gate is required for
every exact PR head.

## Safety

The acceptance test uses synthetic candidate/application data, loopback HTTP, Chromium,
and the no-network SQLite sandbox providers. It does not configure production
credentials, contact employers, send real messages, or modify live calendars.
