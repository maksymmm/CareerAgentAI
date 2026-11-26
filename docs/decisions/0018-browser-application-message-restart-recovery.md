# ADR 0018: Browser recovery for application and message provider timeouts

## Status

Accepted for the candidate sandbox recovery contract.

## Context

Application submission and outbound communication are consequential provider
boundaries. A provider may durably commit its effect and lose the response, leaving
the candidate workflow in a reconciliation phase. A browser tab can also disappear,
and the runtime process can restart while the provider evidence remains durable.

A successful calendar recovery test does not prove that the earlier application and
message boundaries can be recovered through the candidate browser after a runtime
restart.

## Decision

The candidate browser treats credentials, selected run IDs, prompts, and reconciliation
attestations as ephemeral tab state. After a runtime restart, the candidate must
reauthenticate, select the durable run ID, and fetch a fresh owner-scoped status
snapshot before recording provider evidence.

Application reconciliation requires the exact provider submission ID from the
durable sandbox submission ledger and an explicit candidate attestation. Message
reconciliation requires a fresh version-bound status snapshot and explicit candidate
attestation. Neither route may replay the provider effect.

The browser must clear its local reconciliation controls after a successful submission,
a session reset, or a run-version change. The workflow resumes only from durable
runtime state.

## Consequences

- A committed application submission remains exactly one provider effect when its
  response is lost and the runtime restarts.
- A committed outbound message remains exactly one provider delivery when its
  response is lost and the runtime restarts.
- A candidate must provide fresh, version-bound evidence after each restart.
- A stale browser session cannot authorize reconciliation.
- The acceptance test uses synthetic candidate data, a local WSGI server, Chromium,
  and SQLite sandbox providers only; it never contacts a real employer or messaging
  service.

## Verification

The browser acceptance covers two consecutive runtime restarts in one candidate run:
application `TIMEOUT_AFTER_EFFECT`, then message `TIMEOUT_AFTER_EFFECT`, followed
by a successful sandbox calendar action. It asserts one application submission, one
outbound delivery, one calendar effect, zero provider replays, and a terminal
`COMPLETE` candidate run.
