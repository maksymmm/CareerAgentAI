# ADR 0037: Invalidate interview-decline discovery on selection

- Status: Proposed
- Date: 2026-11-26
- Scope: Candidate sandbox interview-decline recovery dashboard

## Context

The pending interview-decline collection is a read-only discovery hint. Selecting a
row must reload the owned event and its per-event pending snapshot before the
candidate can inspect or attest provider evidence.

The collection uses asynchronous keyset pagination. A second-page request can remain
in flight while the candidate selects a row from the first page. If the selection
does not invalidate the collection generation immediately, a late response can append
stale rows or restore a cursor after the candidate has moved into event recovery.

## Decision

When a pending-decline row is selected, synchronously call the existing
clearDeclineIntents invalidation before starting the authoritative event-detail and
per-event pending reads.

The generation check remains the authority for rejecting the late page response.
The existing per-event endpoint remains the only source that can restore pending
decline state; discovery does not grant approval or invoke the provider.

## Verification

A Chromium acceptance regression seeds 26 ambiguous declines in the SQLite sandbox,
holds page two at the authenticated loopback WSGI boundary, selects a row from page
one, then releases and fully consumes the delayed response. It verifies the old
collection and cursor stay cleared, the authoritative per-event recovery completes,
a new explicit discovery succeeds, and sandbox calendar attempt/effect counts do not
increase.
