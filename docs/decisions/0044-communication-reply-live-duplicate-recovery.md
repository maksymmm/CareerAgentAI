# ADR 0044: Recover concurrent candidate communication replies after response loss

- Status: Proposed
- Date: 2026-11-27

## Context

The sandbox communication provider supports idempotent sends and replies, and the
external-action service now preserves a live `in_progress` operation during its bounded
grace period. A reply still needs an end-to-end regression for the critical window in
which the provider commits an outbound reply but its response is lost before the
external-action operation becomes terminal.

A duplicate candidate retry during that window must not call the provider again, change
the operation to a false recovery state, or lose the reply's parent/thread identity.

## Decision

Add a deterministic two-runtime regression using one durable SQLite database and the
real `CommunicationService`, `ExternalActionService`, and
`SQLiteSandboxCommunicationProvider`.

- Persist an inbound synthetic parent and one immutable candidate reply draft.
- Let the first reply commit one `TIMEOUT_AFTER_EFFECT` provider delivery, then pause
  after the SQLite commit and before the timeout reaches the external-action service.
- Submit the same reply and operation ID from an independent runtime while the first
  response is paused.
- Require the duplicate to observe the live `in_progress` operation and return without
  invoking its provider plan.
- Release the first response so the operation becomes `reconciliation_required`.
- Restart with a fresh provider/service, verify the exact outbound reply evidence, and
  reconcile it to `succeeded` without another provider attempt or delivery.
- Assert the parent remains inbound, the reply remains outbound in the same thread and
  points to the exact parent, and a post-recovery service replay does not reach the provider.

The regression uses only synthetic `.example.test` identities and local SQLite sandbox
providers. It does not send an actual email or contact a recipient.

## Consequences

Candidate communication replies now have an explicit live-duplicate, response-loss, and
restart-reconciliation contract at the service/provider boundary. This proves only the
local SQLite sandbox behavior; it does not assert equivalent guarantees from a future
mailbox integration.
