# Decision 0059: Reject WAL-mode databases for strict read-only SQLite projections

## Status

Accepted

## Context

Opening SQLite with URI `mode=ro` protects the primary database from ordinary
writes and prevents creation of a missing file. It does not guarantee that opening
a WAL-mode database is free of filesystem writes: SQLite may create or update the
`-shm` shared-memory coordination sidecar while a read-only connection observes
the WAL.

`PRAGMA query_only = ON` blocks DML and DDL through the connection, including
temporary and attached databases, but it does not prevent SQLite's WAL coordination
writes. Candidate GET projections promise not to mutate durable storage or create
sidecar files, so accepting WAL mode would weaken that contract.

Using URI `immutable=1` is not a safe substitute: immutable connections bypass
normal locking and can ignore uncheckpointed WAL content, returning a stale snapshot.

## Decision

Before opening a strict read-only connection, resolve the local filesystem path
(including supported local `file:` URIs) and inspect the SQLite file header. If
either file-format version byte identifies WAL mode, reject the request before
calling `sqlite3.connect`.

- Rollback-journal databases continue through URI `mode=ro`.
- The existing `PRAGMA query_only = ON` remains enabled as defense in depth for
  temporary and attached databases.
- Missing files are still passed to SQLite's non-creating read-only open so the
  existing sanitized missing-storage behavior is preserved.
- WAL-mode files are not checkpointed, converted, or opened with `immutable=1`.

## Consequences

- Candidate read-only projections cannot create or update WAL sidecars.
- Existing WAL-mode databases fail closed and need an explicitly controlled
  checkpoint/conversion before being used by these projections.
- Rollback-journal reads retain SQLite's normal locking and snapshot behavior.
- Regression tests cover live WAL sidecar immutability, closed WAL databases with
  absent sidecars, local file-URI handling, and successful rollback-journal reads.
- No provider, application, communication, or calendar effect is introduced.
