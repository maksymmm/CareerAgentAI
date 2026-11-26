# ADR 0024: Preserve candidate decline terminality across restart

- Status: Accepted
- Date: 2026-11-26

## Context

A candidate may decline at the application, recruiter-message, or interview approval
gate. The decline is a durable terminal decision, not a temporary pause. Existing
provider-effect fencing proves that the declined action and all downstream effects are
not executed before the runtime closes, but that alone does not prove a later process
cannot resume the same run.

## Decision

Treat `CareerLoopPhase.FAILED` produced by an explicit candidate decline as terminal
across runtime reconstruction. The authenticated status endpoint must restore the
same minimal terminal snapshot, and the authenticated `/continue` endpoint must
return the same terminal phase without advancing the durable state version or creating
new provider attempts, effects, or external-action operations.

The acceptance regression runs all three decline gates against the production sandbox
composition, closes the first runtime, creates a new runtime over the same SQLite
database, and invokes status and continue through the authenticated WSGI boundary.
It compares the provider ledgers before and after the continuation request.

## Consequences

- A declined application, message, or interview cannot be silently resumed after a
  browser reload or process restart.
- Earlier explicitly approved provider effects remain durable and are not replayed.
- The declined action and all later provider actions remain absent from the SQLite
  sandbox ledgers.
- The contract is verified without production credentials, live providers, real
  applications, outbound messages, or calendar mutations.
