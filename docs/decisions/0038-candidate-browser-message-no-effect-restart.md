# ADR 0038: Recover candidate-approved message no-effect after restart

- Status: Proposed
- Date: 2026-11-26
- Scope: Candidate browser messaging and SQLite sandbox communication provider

## Context

The candidate can approve an exact recruiter-message draft in the browser, but the
provider may definitively record no delivery. The durable run remains at the
message-send boundary with a failed external action. A browser or runtime restart
must not cause the candidate to approve a different message, resubmit the application,
or lose the same idempotent message operation.

The HTTP/runtime recovery path already verifies provider absence before retry. The
browser path needs production-composition evidence across the same process boundary.

## Decision

Keep the existing two approval gates and recover only the exact approved message:

1. The application approval creates one immutable message draft and one application
   submission; it does not invoke the communication provider.
2. The browser displays the exact sender, recipient, subject, and body before the
   separate message approval.
3. The SQLite sandbox communication provider records a first-attempt
   NO_EFFECT, leaving the durable message as a draft with no delivery.
4. After the first runtime and browser session close, a fresh runtime opens the same
   SQLite database. Authenticated Continue verifies provider absence and retries the
   same durable operation ID; it does not recreate or resubmit the application.
5. The candidate reaches the next interview-approval gate only after one successful
   sandbox delivery.

The acceptance test uses RuntimeCandidateApp, authenticated loopback WSGI, Chromium,
synthetic .example.test identities, and a deterministic no-network SQLite provider.
The test outcome plan is injected only in the test; no production messaging adapter
or credential is enabled.

## Consequences

- Browser approval, durable no-effect recovery, and runtime restart are tested as one
  candidate workflow.
- The first no-effect attempt creates no outbound message or delivery.
- The retry preserves the original message operation and immutable contents.
- The application submission remains exactly once, and the message delivery occurs
  exactly once with zero provider replays.
- The next interview decision remains a separate explicit candidate approval.
