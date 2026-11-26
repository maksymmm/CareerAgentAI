# ADR 0016: Candidate-approved sandbox employer-withdrawal dashboard

- Status: Accepted
- Date: 2026-11-25

## Context

The candidate application tracker already exposes read-only application detail and
history. The employer-withdrawal provider workflow is a distinct consequential action:
it requires a prior successful application submission, an explicit candidate decision,
durable provider evidence, and a state-version-bound local tracker transition.

A local tracker-only withdrawal must never be presented as an employer-side effect.
Likewise, a provider-backed sandbox result must not be presented as a real employer
acknowledgement.

## Decision

The candidate sandbox dashboard exposes a separate employer-withdrawal panel only
after an owner-scoped application detail has been loaded. The action is eligible only
for `applied`, `interview`, and `offer` states. It requires a fresh checkbox
confirmation for each request and sends only the application ID and the exact displayed
state version. Provider submission IDs and operation IDs remain server-owned.

The browser accepts only the exact response allowlist and requires the same application,
`withdrawn` status, a single state-version increment, and
`provider_status=confirmed`. It clears stale detail and confirmation after conflicts
or uncertain responses. It never retries automatically: a no-effect retry requires a
new detail read and a new explicit candidate confirmation.

After confirmed success, the UI updates the detail snapshot, invalidates old history,
and requires the candidate to reload the application collection for a fresh list.
It labels the result as a local SQLite sandbox simulation and states that no real
employer was contacted.

## Consequences

- The existing tracker-only withdrawal remains a separate operation and cannot imply
  provider-side completion.
- Browser confirmation is version-bound and cannot be reused after token, selection,
  status, or request-context changes.
- A definite no-effect result remains visible as an unconfirmed outcome; another
  attempt requires a fresh explicit action.
- Browser acceptance uses only synthetic SQLite application and withdrawal ledgers.
- No employer network adapter, production credential, real application, message, or
  calendar action is introduced.
