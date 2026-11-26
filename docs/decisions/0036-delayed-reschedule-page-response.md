# ADR 0036: Ignore delayed pending-reschedule pages after selection

- Status: Proposed
- Date: 2026-11-26
- Scope: Candidate sandbox browser recovery

## Context

The pending-reschedule dashboard loads bounded keyset pages asynchronously. A later
page can remain in flight while the candidate selects a row from an earlier page.
Selection invalidates the discovery collection and starts authoritative event-detail
and per-event recovery reads. The delayed page response must not restore stale rows,
a stale cursor, or an in-flight state that prevents a later explicit discovery.

## Decision

Keep the collection-generation guard as the authority for every asynchronous page
response and prove the delayed-response ordering with a deterministic browser test.

- Hold the second keyset-page request at the loopback WSGI boundary.
- Select an event from the already-rendered first page while the second request is held.
- Release the second response only after selection has cleared the collection.
- Require the delayed response to leave the collection empty and its cursor disabled.
- Require the authoritative per-event recovery read to complete independently.
- Require a new explicit discovery request to load the first page successfully.
- Compare durable sandbox calendar attempt/effect counts before and after the scenario.

The test uses Chromium, authenticated loopback HTTP, and synthetic SQLite sandbox
operations. It does not call a live provider or perform a real calendar mutation.

## Consequences

The regression now covers the exact response-ordering race rather than only a
synchronous stale-selection transition. A candidate may explicitly reload discovery
after selecting an item, while an older delayed page cannot repopulate the invalidated
collection or interfere with the next request.
