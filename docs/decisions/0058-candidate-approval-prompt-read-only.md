# ADR 0058: Read candidate approval prompts without writable provider composition

- Status: Accepted
- Date: 2026-11-28

## Context

The authenticated candidate approval GET route loaded its prompt by opening a writable
SQLite connection and composing the full autonomous loop. That composition initializes
application-submission, communication, scheduling, external-action and sandbox-provider
schemas even though the request only reads one candidate-owned prompt. A missing or
misconfigured database path could also be created as a side effect of a GET request.

Approval prompt retrieval is a read boundary. It must not initialize providers, create
storage, migrate schema, or decode another candidate's private loop payload.

## Decision

Add an owner-scoped approval-prompt projection to
`SQLiteCandidateRunStatusProjection` and use it from the request-local candidate loop.

- Open the existing database with `SQLiteDatabase.open_read_only`.
- Select trusted owner metadata, serialized state and storage version in one query.
- Reject missing and foreign runs before deserializing their payload.
- Reuse the strict durable-state deserializer and the existing candidate approval
  contract to build the same prompt and fingerprint.
- Keep approval POST, continuation and reconciliation on their existing writable
  execution paths.

## Consequences

Candidate approval GET no longer constructs submission, mailbox or calendar providers
and cannot create a missing SQLite file or initialize provider tables. Existing storage
remains byte-for-byte unchanged during prompt retrieval. Foreign malformed payloads
remain indistinguishable from missing runs; malformed owned state returns a sanitized
internal error.

The regression suite verifies an unchanged SQLite file and schema, no database creation
for a missing path, owner-first rejection of malformed foreign state, and sanitized
failure for malformed owned state.

This is a read-only guarantee for the local SQLite sandbox composition. It does not
claim that a remote provider or production deployment is involved.
