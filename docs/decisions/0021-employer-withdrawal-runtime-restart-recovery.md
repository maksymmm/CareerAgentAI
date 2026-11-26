# ADR 0021: Employer withdrawal recovery across runtime restart

- Status: Proposed
- Date: 2026-11-26

## Context

The candidate-approved employer withdrawal path already records a durable SQLite
sandbox effect and reconciles provider timeouts from evidence. Existing tests covered
the provider timeout and reconciliation in one request, but did not exercise the
failure window where the provider effect is durable and evidence lookup is unavailable
before the first runtime exits.

That window matters because the candidate tracker must remain unchanged until the
provider result is independently verified, and a new runtime must recover without
calling the provider again.

## Decision

Add a Chromium acceptance test that runs two independent RuntimeCandidateApp instances
against the same SQLite database. Runtime 1 receives a synthetic TIMEOUT_AFTER_EFFECT.
The first provider-evidence lookup is deliberately unavailable, leaving the external
action in RECONCILIATION_REQUIRED and the candidate application in APPLIED. The browser
session and runtime are then closed.

Runtime 2 restores the same database. The candidate re-authenticates, reloads the
owner-scoped application detail, and explicitly confirms the withdrawal again. The
service reconciles the durable sandbox evidence, advances the tracker to WITHDRAWN, and
must not invoke the provider a second time. The second runtime's outcome plan fails if
the provider is called.

## Consequences

- The test covers the provider-effect/evidence-unavailable crash window.
- Candidate tracker state cannot advance before provider evidence is available.
- Restart recovery is proven through the actual dashboard and runtime composition.
- The provider attempt and effect remain exactly one, with zero replays.
- The workflow remains SQLite sandbox-only; no real employer is contacted.
