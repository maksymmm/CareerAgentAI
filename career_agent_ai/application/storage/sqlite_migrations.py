"""Versioned, checksum-verified SQLite schema migrations."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase


@dataclass(frozen=True)
class SQLiteMigration:
    """One immutable, ordered SQLite schema migration."""

    version: int
    name: str
    statements: tuple[str, ...]
    destructive: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.version, int) or isinstance(self.version, bool) or self.version < 1:
            raise ValueError("migration version must be a positive integer.")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("migration name must not be empty.")
        if len(self.name.strip()) > 200:
            raise ValueError("migration name must not exceed 200 characters.")
        if not self.statements or any(
            not isinstance(statement, str) or not statement.strip()
            for statement in self.statements
        ):
            raise ValueError("migration statements must contain non-empty SQL strings.")
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(
            self,
            "statements",
            tuple(statement.strip() for statement in self.statements),
        )

    @property
    def checksum(self) -> str:
        """Return the stable SHA-256 checksum of migration identity and SQL."""
        payload = "\n".join((str(self.version), self.name, *self.statements))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SQLiteMigrationRunner:
    """Apply trusted versioned migrations transactionally and exactly once."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database
        self._ensure_registry()

    def apply(
        self,
        migrations: Iterable[SQLiteMigration],
        *,
        allow_destructive: bool = False,
    ) -> tuple[int, ...]:
        """Apply pending migrations and return versions applied in this call.

        Existing versions are checksum-verified before being skipped. Destructive
        migrations require an explicit caller opt-in so production automation cannot
        silently cross a destructive schema boundary.
        """
        if not isinstance(allow_destructive, bool):
            raise TypeError("allow_destructive must be a boolean.")
        ordered = tuple(migrations)
        versions = [migration.version for migration in ordered]
        if versions != sorted(versions) or len(set(versions)) != len(versions):
            raise ValueError("migrations must be uniquely versioned in ascending order.")
        applied: list[int] = []
        for migration in ordered:
            existing = self._load(migration.version)
            if existing is not None:
                name, checksum = existing
                if name != migration.name or checksum != migration.checksum:
                    raise ValueError(
                        f"migration {migration.version} differs from the applied migration."
                    )
                continue
            if migration.destructive and allow_destructive is not True:
                raise PermissionError(
                    f"destructive migration {migration.version} requires explicit approval."
                )
            if self._apply_one(migration):
                applied.append(migration.version)
        return tuple(applied)

    def applied_versions(self) -> tuple[int, ...]:
        """Return all applied migration versions in deterministic order."""
        rows = self._database.connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        return tuple(int(row[0]) for row in rows)

    def _ensure_registry(self) -> None:
        self._database.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY CHECK(version >= 1),
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        self._database.connection.commit()

    def _load(self, version: int) -> tuple[str, str] | None:
        row = self._database.connection.execute(
            "SELECT name, checksum FROM schema_migrations WHERE version = ?",
            (version,),
        ).fetchone()
        return None if row is None else (str(row[0]), str(row[1]))

    def _apply_one(self, migration: SQLiteMigration) -> bool:
        """Apply one migration while holding the SQLite writer lock.

        The registry is re-read after BEGIN IMMEDIATE so concurrent deployment
        processes converge on one exactly-once application.
        """
        connection = self._database.connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT name, checksum FROM schema_migrations WHERE version = ?",
                (migration.version,),
            ).fetchone()
            if row is not None:
                name, checksum = str(row[0]), str(row[1])
                if name != migration.name or checksum != migration.checksum:
                    raise ValueError(
                        f"migration {migration.version} differs from the applied migration."
                    )
                connection.commit()
                return False
            for statement in migration.statements:
                connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, name, checksum, applied_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    migration.version,
                    migration.name,
                    migration.checksum,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            connection.commit()
            return True
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
