# ADR 0050: Bound operational recovery issue inspection

- Status: Accepted
- Date: 2026-11-27

## Context

The operational issues endpoint previously loaded every failed, ambiguous, stale,
and stuck record into memory before returning JSON. A growing durable action ledger
could therefore make a read-only diagnostic request consume unbounded memory and
perform a full-table sort.

## Decision

- Accept a validated issue limit from 1 to 500, defaulting to 100.
- Query each external-action lifecycle status independently with `LIMIT limit + 1`.
- Use partial expression indexes on normalized SQLite timestamps and stable IDs so
  status-specific recovery queries avoid temporary full-result sorts.
- Query failed, stale, and malformed career-loop records through matching partial
  indexes; preserve the rule that human-gated and actively leased runs are not stuck.
- Return an explicit `truncated` boolean when additional issues exist beyond the page.
- Keep the probe read-only and expose no raw malformed payloads.

The independent coverage CLI gate also runs after pytest unless the job is cancelled,
so a failed test run does not silently skip the second coverage threshold.

## Consequences

Operational issue response size and Python-side materialization are bounded. Operators
can raise the requested limit up to 500 and can distinguish a complete response from a
truncated one. The endpoint does not claim that a truncated page is exhaustive.
All provider and career-loop inspection remains read-only.
