# ADR 0049: Fail closed before recreating candidate provider ledgers

- Status: Accepted
- Date: 2026-11-27

## Context

The candidate runtime creates request-local repositories and sandbox providers. Their
constructors use additive SQLite schema initialization. If an existing database loses
a provider-ledger table, a later request could recreate that table as empty before the
provider verifies an ambiguous operation. The missing history could then be mistaken
for confirmed absence, weakening the no-blind-retry guarantee.

The candidate readiness probe previously checked the durable career-loop columns only.
It did not establish that the application, communication, scheduling, external-action
and sandbox-provider ledgers required by the complete candidate workflow were present.

## Decision

Add a singleton `candidate_schema_manifest` with schema version 1.

- A fresh database may bootstrap normally. The manifest is written only after all
  candidate repositories and sandbox providers initialize and the complete schema
  validates.
- If the manifest already exists, request-local composition validates the marker,
  every required table and every provider-ledger column before constructors run.
  Core repositories may still apply additive column migrations; full schema validation
  runs again before the manifest is written or accepted.
- A legacy database without the manifest may adopt it when no candidate history exists.
  If history exists, every required table and every provider-ledger column must already
  be present before additive core-table migrations or provider initialization proceed.
- `/readyz` validates the complete manifest and schema using a read-only connection.
- Authenticated `/v1/candidate/*` requests perform the same read-only schema check after
  bearer authentication and before delegation to request-local composition.
- `/healthz` remains storage-independent. Unauthenticated candidate requests continue
  through the existing authentication boundary without opening storage.

Missing or incomplete schema returns a sanitized unavailable response and is never
repaired by silently creating an empty provider ledger.

## Consequences

The service fails closed when provider history may have been lost. Empty first-run
databases remain bootstrappable, and legacy databases with complete provider ledgers can
adopt the manifest. The manifest validates schema presence and version, not row-level
evidence; each provider retains its own cross-ledger integrity checks during reconciliation.
No network provider, production credential, or external action is introduced.
