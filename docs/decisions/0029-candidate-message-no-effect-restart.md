# ADR 0029: Candidate message workflow through authenticated HTTP and UI

- Status: Proposed
- Date: 2026-11-26

## Context

The autonomous career loop already supported a separately approved recruiter/employer
message, but the authenticated candidate start request and browser sandbox did not
expose sender, recipient, subject, or body. As a result, a candidate using the
published HTTP/UI workflow could not enter the message intent that the loop's
existing approval state machine expects.

## Decision

- Extend the strict candidate start request with optional paired sender/recipient
  identifiers and bounded message subject/body fields. The browser sandbox exposes
  the same optional inputs.
- Require sender and recipient together; message content is accepted only when the
  identity pair is supplied. Publish matching constraints and dependencies in OpenAPI.
- Preserve the existing separate, version-bound message approval gate. Creating a
  run may persist a draft but must not create a provider delivery; only approval of
  the exact immutable draft may invoke the local SQLite sandbox provider.
- Add an end-to-end authenticated HTTP regression that approves the application,
  verifies the message remains a draft with no delivery attempt, approves the exact
  message, verifies exactly one durable sandbox delivery, and confirms terminal
  status replay after runtime restart.

## Consequences

The candidate API and browser sandbox can exercise the complete application-then-
message approval workflow without bypassing the published owner-scoped HTTP boundary.
The provider remains local and no-network; this decision does not add a real mailbox
integration or change communication no-effect recovery semantics.
