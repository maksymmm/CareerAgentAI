# ADR 0045: Serialize cancellation against later candidate approvals

- Status: Proposed
- Date: 2026-11-27

## Context

The application-approval race is covered at the service and authenticated browser/API
boundaries. The candidate workflow has two later consequential gates: recruiter-message
approval and interview-calendar approval. Both must preserve the same one-winner rule
when cancellation and approval use one candidate-observed state version.

## Decision

Add deterministic Chromium regressions for both later gates and both race orderings.
The tests drive the real candidate dashboard through the preceding approvals, then
send authenticated cancellation and approval POSTs concurrently through a threaded
loopback WSGI server backed by RuntimeCandidateApp and the durable SQLite sandbox
providers.

The winner is held after its SQLite execution lease commits while the competing
request attempts the same version. The losing request must receive HTTP 409 with
approval_conflict before it can create a provider effect.

Required invariants:

- Message gate, cancellation wins: application submission remains exactly once,
  the message remains a draft, and communication/calendar provider attempts remain zero.
- Message gate, approval wins: one communication delivery commits, the run advances
  to interview approval, and no calendar effect is created.
- Interview gate, cancellation wins: the prior application/message effects remain
  exactly once, while the proposed event remains unchanged and no calendar attempt is
  created.
- Interview gate, approval wins: one calendar acceptance commits, the event advances
  exactly once to accepted version 2, and the run becomes complete.
- In every ordering, the losing request performs no provider effect and the durable
  SQLite projection matches the browser's refreshed status.

The tests use only synthetic identities, Chromium, authenticated loopback WSGI, and
no-network SQLite sandbox providers. Production runtime behavior is not replaced.

## Safety

No production credentials, real applications, outbound messages, live providers, or
real calendar mutations are used.
