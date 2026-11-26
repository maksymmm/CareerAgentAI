# ADR 0034: Candidate dashboard discovery for pending reschedules

- Status: Proposed
- Date: 2026-11-26
- Scope: Candidate sandbox browser UI

## Context

The authenticated reschedule-intent collection added in ADR 0033 makes uncertain
interview changes discoverable after a browser restart. Until the dashboard consumes
that collection, candidates still need to know an event identifier before they can
reach the existing per-event recovery flow.

Discovery is not evidence that a provider action completed, and it is not a candidate
approval. The event and operation can change between collection and selection.

## Decision

Add an explicit "Pending reschedule recovery" panel to the candidate sandbox.

- Fetch the owner-scoped collection using bounded keyset pages of 25.
- Validate the response schema, candidate-safe event snapshot, source/event version
  relationship, coarse operation status, stable ordering, and next cursor before
  rendering any item.
- Render only the event projection and coarse status; never render operation,
  provider, candidate, application, or lease identifiers.
- Treat each row as a discovery hint. Selecting it must call the existing event
  detail route and the authoritative per-event reschedule-pending route again.
- Keep reconciliation disabled until the per-event route restores the exact pending
  slot and the candidate supplies a fresh, page-local independent provider
  attestation.
- Clear the collection on bearer-token changes and before reschedule submission or
  reconciliation so stale discovery rows cannot be mistaken for current state.
- Perform no provider I/O from collection discovery or dashboard rendering.

## Consequences

Candidates can find uncertain reschedules after a browser restart without knowing
internal operation identifiers. The collection endpoint remains read-only, and the
per-event recovery contract remains the sole source of reconciliation inputs. Browser
restart never restores a prior candidate attestation or authorizes an automatic retry.
