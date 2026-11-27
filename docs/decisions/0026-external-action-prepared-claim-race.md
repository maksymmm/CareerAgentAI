# ADR 0026: Compare-and-swap ownership for prepared external actions

Status: accepted. Scope: provider-neutral external-action execution.

## Context

Two request workers may both read the same durable operation while it is
`prepared`. The repository transition is a compare-and-swap, so only one worker
can commit `prepared → in_progress`. Before this decision, the losing worker
surfaced the repository conflict even when another worker had already claimed
the exact same operation. Retrying the request could add avoidable ambiguity to
a candidate workflow.

## Decision

Only the caller whose compare-and-swap commits `prepared → in_progress` may
invoke the adapter. A caller that loses the transition re-reads the durable row.
It may return that row without adapter execution only when the operation ID,
action type, and payload still match the intent it originally read and the row
has left `prepared`. If the row is missing, remains prepared, or its action or
payload changed, the original conflict is preserved.

The losing caller never invokes the adapter and never attempts to repair or
rewrite the durable operation. The winner remains responsible for provider
execution and terminal persistence. The bounded in-progress grace period and
stale recovery behavior remain defined by ADR 0025.

## Consequences

- Concurrent duplicate requests converge on the durable winner instead of
  surfacing a false conflict after another worker has claimed the operation.
- Exactly one caller crosses the provider boundary for a prepared operation.
- A changed or rebound intent cannot be mistaken for the original operation.
- No schema migration or new provider capability is introduced.
- Regression tests use independent SQLite connections and a blocked local adapter;
  no real application, message, calendar, credential, or network provider is used.
