# ADR 0044: Verify candidate cancellation and approval race through WSGI

- Status: Proposed
- Date: 2026-11-27

## Context

ADR 0043 adds a deterministic two-worker service regression for cancellation and
application approval from the same owner-scoped state version. That test proves the
SQLite execution lease has one winner, but it does not exercise the authenticated
candidate HTTP routes or the production-composition sandbox providers.

The browser-facing workflow needs the same guarantee: a cancellation request and an
approval POST based on one displayed prompt must not both win, and the losing request
must not create an external-action or provider effect.

## Decision

Add a Chromium regression against the real `RuntimeCandidateApp` and authenticated
loopback WSGI adapter. A threaded WSGI server receives the cancellation and approval
POSTs concurrently. The test synchronizes both requests, holds the selected winner
after its durable SQLite execution lease commits, and lets the competing request try
the same state version before releasing the winner.

Both winner orderings are required:

- Cancellation wins: approval returns `409 approval_conflict`, the run is terminal
  cancelled, the application remains saved, and all sandbox provider/operation counts
  remain zero.
- Approval wins: cancellation returns `409 approval_conflict`, exactly one sandbox
  application submission commits, the run advances to message approval, and only the
  local message draft may exist. Communication send and calendar effects remain zero.
- Both requests use the same authenticated owner, run ID and candidate-observed
  state version; the approval fingerprint comes from a fresh owner-scoped prompt.
- The final browser status and durable SQLite projection must agree.

The test patches only the repository lease boundary to make the race deterministic.
Production runtime composition, HTTP routing, SQLite repositories and sandbox
providers remain the real implementations.

## Safety

Only synthetic candidate/job data, Chromium, authenticated loopback WSGI, file-backed
SQLite and no-network sandbox providers are used. No production credentials, real
applications, outbound messages, live providers or calendar mutations are involved.
