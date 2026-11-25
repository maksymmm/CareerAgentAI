# Decision 0005: Communication and calendar sandbox providers own their transactions

## Status

Accepted

## Context

The SQLite sandbox communication and calendar providers share a database connection with candidate workflow repositories. A provider must commit only its own durable intent, attempt, and effect records. Schema initialization, message drafting, or an external-action simulation must not silently join and commit unrelated caller work.

## Decision

Communication and calendar provider constructors reject initialization when the shared connection already has an active transaction, before issuing schema DDL. Communication draft/delivery and calendar action methods likewise reject an ambient transaction before provider reads, writes, or outcome-plan execution.

Each mutating provider method starts its own `BEGIN IMMEDIATE` transaction and owns its commit/rollback boundary. Evidence verification remains read-only and rejects an ambient transaction. A caller that needs a larger atomic unit must coordinate it outside these provider boundaries; the providers never commit or roll back caller-owned work.

## Consequences

- Caller-owned writes remain pending and are controlled only by the caller.
- Provider intent, attempt, and effect records remain within a provider-owned transaction.
- A rejected call performs no provider action and does not invoke the sandbox outcome plan.
- Normal runtime behavior remains unchanged because repository transactions finish before the provider adapter is invoked.
- Regression tests cover constructor, draft, delivery, and calendar-action boundaries, including rollback proof.

## Safety boundary

This decision applies only to the local SQLite sandbox communication and calendar providers. It does not enable network providers, use production credentials, send real messages, or mutate real calendars.
