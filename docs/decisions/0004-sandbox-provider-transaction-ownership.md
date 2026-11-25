# Decision 0004: Sandbox providers own their SQLite transactions

## Status

Accepted

## Context

The sandbox application-submission provider shares a SQLite connection with the external-action repository in the candidate runtime. Its provider operation must commit only the intent, attempt, and effect records that it owns. Silently joining an already-open transaction can commit unrelated caller work when the provider finishes its own operation.

## Decision

A sandbox submission provider call must begin its own BEGIN IMMEDIATE transaction. If the shared SQLite connection is already inside a transaction, submission fails closed with PreSubmissionError before reading or writing provider ledger rows.

The provider must not join, commit, or roll back a caller-owned transaction. A caller that needs an atomic multi-operation unit must coordinate it outside this provider boundary rather than relying on the provider to take ownership of an ambient transaction.

## Consequences

- Provider effects and their intent/attempt evidence remain one provider-owned transaction.
- A caller transaction cannot be committed as a side effect of a provider call.
- Existing normal service execution remains unchanged because repository transitions complete before the provider adapter is invoked.
- The regression test opens an unrelated caller transaction, attempts a submission, verifies that the transaction remains active and provider tables remain untouched, then rolls back and proves the caller's write was not committed.

## Safety boundary

This decision applies only to the local SQLite sandbox provider. It does not enable network submission, use production credentials, or change human-approval requirements.
