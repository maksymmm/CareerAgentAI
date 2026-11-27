# 0038 — Fence stale candidate reschedule attestations by event version

## Context

Pointerless candidate reschedule recovery can restore an operation after the provider has
applied its target and the event has advanced from source version N to N+1. The candidate
may keep that recovery snapshot open while another approved action advances the same
event again. A same-slot reschedule can preserve the visible status and target while
still advancing the aggregate version.

Matching only the current status and target slot is therefore insufficient: an old
attestation could otherwise complete an older operation after a newer event version has
become authoritative.

## Decision

An already-applied calendar reschedule target is a valid reconciliation state only
when the current event version is exactly the prepared source version plus one. If the
event has advanced further, fail closed with an approval conflict. Do not mark the old
operation succeeded, mutate the event, or invoke the provider. The candidate must reload
the current event and obtain a fresh recovery snapshot before taking another action.

The source-state path remains bound to the exact prepared source version and immutable
source slot. The pointerless target-state path remains bound to the exact target slot,
timezone, status, and single version increment.

## Invariants

- An old attestation cannot resolve an operation after any later event-version advance,
  even when the current slot and status still equal the old target.
- A stale reconciliation attempt creates no provider attempt, effect, or replay.
- The old operation remains reconciliation-required until a fresh, valid recovery
  workflow can resolve it.
- A newer same-target approval remains independently durable and successful.
- Reconciliation remains evidence-only and never replays the calendar provider.

## Verification

A Chromium regression creates a provider-applied pointerless reschedule, restarts the
candidate runtime/browser, restores the old pending snapshot, then performs a second
explicitly approved same-target reschedule from the next event version. The browser
submits its stale attestation. The API rejects it, the old operation remains pending,
the newer operation remains succeeded, and the two operations retain exactly two
provider attempts/effects with zero replays.
