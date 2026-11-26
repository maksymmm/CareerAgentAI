# ADR 0031: Candidate browser message decline across runtime restart

- Status: Proposed
- Date: 2026-11-26

## Context

The candidate browser now exposes separate application and recruiter-message
approval gates. The message approval must remain a genuine human decision: declining
the exact displayed message must not invoke the communication provider, and the
terminal decision must survive loss of the runtime process and browser session.

## Decision

Add a Chromium acceptance contract using RuntimeCandidateApp, the authenticated
loopback WSGI sandbox, and the no-network SQLite communication provider:

1. Approve the application and verify that the exact message exists only as a draft.
2. Load the separate message approval prompt and decline that exact snapshot.
3. Verify that no communication intent, provider attempt, delivery, or outbound
   message is created.
4. Close the runtime and browser session, then reopen the same SQLite database with
   a fresh RuntimeCandidateApp and browser session.
5. Verify the run remains terminal FAILED; status reads and an explicit continue
   request must not create a communication effect or change the durable message.
6. Keep the candidate's owner scope and the existing version-bound approval contract.

The test uses synthetic `.example.test` identities, loopback HTTP, Chromium, and
SQLite sandbox providers only. No real recipient, production credential, network
messaging provider, employer contact, application submission, or calendar mutation
is involved.

## Consequences

- The browser's decline control is covered at the consequential message boundary.
- A draft cannot be mistaken for an outbound message after a decline.
- Runtime restart cannot silently reopen a declined candidate decision.
- The communication provider ledger remains empty for the declined message.
