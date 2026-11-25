# Decision 0007: Enforce query-only mode on read-only SQLite connections

## Status

Accepted

## Context

Opening the primary SQLite file with URI `mode=ro` prevents writes to that file and
prevents accidental creation. It does not, by itself, make the entire connection
read-only: SQLite can still attach another writable database or use temporary
database objects through the same connection.

Candidate status projections use a shared database abstraction and are intended to
perform reads only. A future query path or helper should not accidentally mutate an
attached or temporary database merely because the primary file is read-only.

## Decision

`SQLiteDatabase.open_read_only()` opens the primary file with `mode=ro` and enables
`PRAGMA query_only = ON` before returning the connection.

This blocks ordinary DML and DDL against the primary, temporary, and attached
databases while preserving SELECT access. The guard is defense in depth, not a
hostile-code isolation boundary: code holding the raw connection can deliberately
change connection pragmas.

## Consequences

- Read-only candidate projections remain able to query the primary database.
- Accidental writes to attached and temporary databases are rejected.
- A missing primary database is not created.
- No schema migration or durable application data mutation is introduced.
- Regression tests verify primary, temporary, and attached-database write rejection.
