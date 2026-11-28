# ADR 0056: Preserve live candidate decline provider calls during recovery

## Status

Accepted for the candidate sandbox workflow.

## Context

The candidate decline pending endpoint previously treated every durable
`in_progress` operation as abandoned and immediately changed it to
`reconciliation_required`. A second browser/runtime could therefore observe an
operation while its original provider call was still executing, prematurely expose
provider-attestation controls, and race the active worker's terminal write.

The shared external-action policy uses a bounded 300-second grace period so live
provider calls are not reclassified as crashes. Candidate decline recovery must use
the same boundary.

## Decision

- Read the operation's durable `updated_at` before opening the writable request stack.
- Return HTTP 202 with the candidate-safe `in_progress` outcome while the timestamp
  is younger than 300 seconds.
- Treat future timestamps conservatively as live to tolerate clock skew.
- Keep the dashboard's decline and reconciliation controls disabled while the
  provider call remains live.
- Only after the grace expires may recovery compare-and-swap
  `in_progress` to `reconciliation_required`.
- Continue requiring independent provider evidence before resolving an ambiguous
  outcome; recovery never replays the provider.

## Consequences

The pending endpoint does not acquire a SQLite writer path for a live operation.
A restarted candidate can distinguish an active provider call from an expired
ambiguous operation without weakening exactly-once sandbox effects or human
attestation. The 300-second value is intentionally aligned with the shared
external-action recovery policy.
