# ADR 0046: Browser recovery for pointerless candidate interview declines

- Status: Proposed
- Date: 2026-11-27

## Context

A candidate-approved calendar decline can commit the provider effect and the target
event transition before the external-action ledger persists its terminal
`succeeded` status. The event correctly clears its active-action pointer, while the
operation remains `reconciliation_required`. The service-level recovery path now
recognizes this narrowly defined pointerless state, but the candidate still needs a
browser-level proof that the pending intent is restored after both runtime and browser
restart.

## Decision

Add a Chromium acceptance using `RuntimeCandidateApp`, authenticated loopback WSGI,
and the no-network SQLite sandbox calendar provider.

- Inject a SQLite trigger that rejects only the terminal success write after the
  provider has committed one decline effect and the event has advanced to
  `declined` at source version + 1.
- Restart the runtime and create a fresh browser session over the same SQLite file.
- Load the owned event and pending decline; require the pointerless operation to be
  restored as pending without another provider attempt or effect.
- Keep both reconciliation actions disabled until the candidate checks the explicit
  independent-verification attestation.
- Reconcile positive provider evidence and require the operation to become
  `succeeded` without replaying the provider.

## Consequences

The browser-level contract now covers the post-effect terminal-write failure window,
including the cleared active pointer, durable restart, candidate attestation, and
evidence-only reconciliation. The test asserts one provider attempt, one decline
effect, zero replays, and the exact declined event version.

Only synthetic event data, Chromium, loopback HTTP, and the local SQLite sandbox are
used. No production credentials, live calendar providers, or real calendar mutations
are involved.
