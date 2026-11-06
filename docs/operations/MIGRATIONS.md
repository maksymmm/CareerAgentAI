# SQLite Migration Policy

Schema changes that require explicit release sequencing use `SQLiteMigration` and `SQLiteMigrationRunner`.

Rules:

1. Versions are positive integers and are supplied in strictly ascending order.
2. Version/name/SQL are immutable after a migration has been applied.
3. The runner stores a SHA-256 checksum in `schema_migrations`; a mismatch fails closed.
4. Each pending migration executes in an immediate transaction and records its registry row in the same transaction.
5. Failed migrations roll back and remain unapplied.
6. A migration marked destructive is refused unless the caller explicitly sets `allow_destructive=True`.
7. Production deployment takes a verified backup before schema changes.
8. Never interpolate external/provider input into migration SQL.

Repository-local compatibility migrations that already exist inside legacy SQLite adapters should be moved into this registry when those schemas next require a breaking or coordinated migration. The registry is the release-level strategy going forward; existing startup-compatible additive migrations remain supported to avoid breaking deployed databases.

## Legacy career-memory primary-key migration

The legacy `career_memory` schema whose primary key is only `memory_key` requires a destructive table rebuild to move to the per-user composite key `(user_id, memory_key)`. Repository startup no longer performs that rebuild implicitly.

Opening a legacy database through `SQLiteMemoryRepository` fails closed unless the deployment explicitly opts in with `allow_destructive_migration=True`. That opt-in routes the rebuild through `SQLiteMigrationRunner`, records the migration checksum, and therefore constitutes the required human change-control approval. Take and verify a backup before enabling the flag, run the migration once, then return normal runtime composition to the default `False` value.
