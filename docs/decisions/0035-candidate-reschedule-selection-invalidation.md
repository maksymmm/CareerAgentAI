# ADR 0035: Invalidate pending reschedule discovery on selection

- Status: Proposed
- Date: 2026-11-26
- Scope: Candidate sandbox browser recovery

## Context

The pending-reschedule collection is a bounded discovery view. Its event snapshot and
cursor can become stale after the page is fetched, including while a later keyset page
is still in flight. Selecting an item already triggers authoritative event-detail and
per-event pending reads, but leaving the old collection visible allows the candidate
to reuse stale rows and cursors after that selection begins.

## Decision

When a candidate selects a pending-reschedule row, invalidate the entire collection
generation and clear all rendered pages before starting the authoritative event and
per-event pending reads.

- A late page response from the invalidated generation must not repopulate the list.
- The selected row remains only an identifier/version hint; the server's owned event
  detail and per-event pending response remain authoritative.
- If the durable event changed after discovery, the per-event recovery contract must
  reject the stale operation and leave all reconciliation controls disabled.
- A candidate must explicitly reload discovery to obtain a new page chain.
- Discovery and invalidation perform no provider I/O and do not mutate provider state.

## Consequences

The dashboard cannot present a stale collection as current while a selected event is
being revalidated. Candidates may need to reload the list after inspecting one item,
but stale cursors and rows cannot be reused. The browser regression exercises 25+1
keyset pages, a concurrent terminal event transition, and unchanged sandbox provider
attempt/effect counts.
