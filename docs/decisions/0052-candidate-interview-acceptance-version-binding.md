# ADR 0052: Bind candidate interview acceptance to the displayed event version

- Status: Accepted
- Date: 2026-11-27

## Context

The scheduling service now supports an `expected_version` argument for candidate
acceptance. The autonomous career loop did not yet propagate the version displayed
in the interview approval prompt. It re-read the current event before accepting, so
a reschedule that completed after the prompt was issued could be mistaken for the
candidate-approved slot. A second race remained between the loop's snapshot check
and the durable provider claim.

## Decision

- Include the event's positive SQLite version in the candidate interview approval
  details. The existing canonical action fingerprint therefore binds the version
  along with the exact displayed slot.
- Confirm the event version did not change while preparing the approval prompt.
- On approval, re-read a stable event snapshot and require exact equality with the
  approved details before proceeding.
- Pass the approved version unchanged to
  `SchedulingService.accept(expected_version=...)`; its durable repository claim
  remains the final compare-and-swap before any provider effect.
- Recognize an already-applied acceptance only when the event is exactly one version
  beyond the approved source and every approved slot field is unchanged.
- Validate the new numeric field in the browser's closed approval-detail schema and
  fail closed on missing, fractional, boolean, unsafe or non-positive versions.

## Consequences

A stale prompt cannot authorize a newer calendar slot, including when a reschedule
wins after the prompt validation read. The candidate must load and approve a fresh
prompt for the new version. A crash after an exact approved acceptance remains
recoverable without issuing a duplicate provider effect.

## Verification

Regression coverage includes a concurrent reschedule between the loop snapshot and
the provider claim, the already-accepted source-version + 1 recovery path, browser
rejection of invalid event versions, and the complete GitHub CI/coverage gates.
All calendar effects are confined to the no-network SQLite sandbox provider; no
real calendar or production credential is used.
