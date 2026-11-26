from __future__ import annotations

import math
import os
import secrets
import sqlite3
import stat
import time
from pathlib import Path
from threading import Event, Thread
from urllib.parse import unquote

from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase


def candidate_schedule_execution_lease_database_path(database_path: str) -> str:
    """Return a separate durable SQLite path so heartbeats do not contend with provider writes."""
    if not isinstance(database_path, str) or not database_path or database_path == ":memory:":
        raise ValueError("Candidate schedule execution leases require a durable database path.")
    raw_path = database_path
    if raw_path.startswith("file:"):
        raw_path = raw_path[5:].split("?", 1)[0].split("#", 1)[0]
        raw_path = unquote(raw_path)
    if not raw_path or raw_path == ":memory:":
        raise ValueError("Candidate schedule execution lease database path is invalid.")
    return str(Path(raw_path).resolve()) + ".candidate-schedule-leases.sqlite3"


def ensure_private_candidate_schedule_lease_database(database_path: str) -> None:
    """Create or open the lease sidecar as a private regular file with mode 0600."""
    if not isinstance(database_path, str) or not database_path:
        raise ValueError("database_path must be non-empty text.")
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = os.open(database_path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Candidate schedule execution lease storage must be a regular file.")
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        else:
            os.chmod(database_path, 0o600)
    finally:
        os.close(descriptor)


class SQLiteCandidateScheduleExecutionLeaseRepository:
    """Persist short-lived ownership of one candidate schedule execution."""

    TABLE_NAME = "candidate_schedule_execution_leases"
    DEFAULT_LEASE_TTL_MS = 30_000

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database
        self._create_schema()

    def acquire(
        self,
        operation_id: str,
        event_id: str,
        lease_token: str,
        *,
        now_epoch_ms: int | None = None,
        lease_ttl_ms: int = DEFAULT_LEASE_TTL_MS,
    ) -> bool:
        """Acquire the event's execution lease unless another live request owns it."""
        self._validate_identity(operation_id, "operation_id")
        self._validate_identity(event_id, "event_id")
        self._validate_identity(lease_token, "lease_token")
        now = self._now(now_epoch_ms)
        ttl = self._ttl(lease_ttl_ms)
        connection = self._database.connection
        if connection.in_transaction:
            raise RuntimeError("Execution leases require an owned SQLite transaction.")
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                f"DELETE FROM {self.TABLE_NAME} WHERE expires_at_epoch_ms <= ?",
                (now,),
            )
            cursor = connection.execute(
                f"""
                INSERT OR IGNORE INTO {self.TABLE_NAME} (
                    operation_id, event_id, lease_token,
                    expires_at_epoch_ms, updated_at_epoch_ms
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (operation_id, event_id, lease_token, now + ttl, now),
            )
            connection.commit()
            return cursor.rowcount == 1
        except Exception:
            connection.rollback()
            raise

    def release(self, operation_id: str, lease_token: str) -> bool:
        """Release only the lease still owned by the supplied opaque token."""
        self._validate_identity(operation_id, "operation_id")
        self._validate_identity(lease_token, "lease_token")
        connection = self._database.connection
        if connection.in_transaction:
            raise RuntimeError("Execution leases require an owned SQLite transaction.")
        connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = connection.execute(
                f"DELETE FROM {self.TABLE_NAME} WHERE operation_id = ? AND lease_token = ?",
                (operation_id, lease_token),
            )
            connection.commit()
            return cursor.rowcount == 1
        except Exception:
            connection.rollback()
            raise

    @classmethod
    def is_active_for_event(
        cls,
        connection: sqlite3.Connection,
        event_id: str,
        *,
        now_epoch_ms: int | None = None,
    ) -> bool:
        """Read an unexpired lease without creating schema or mutating the database."""
        cls._validate_identity(event_id, "event_id")
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (cls.TABLE_NAME,),
        ).fetchone()
        if table is None:
            return False
        row = connection.execute(
            f"""
            SELECT expires_at_epoch_ms
            FROM {cls.TABLE_NAME}
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()
        if row is None:
            return False
        if type(row[0]) is not int or row[0] < 0:
            raise ValueError("Persisted candidate schedule execution lease expiry is malformed.")
        return row[0] > cls._now(now_epoch_ms)

    @classmethod
    def is_active_for_event_path(
        cls,
        database_path: str,
        event_id: str,
        *,
        now_epoch_ms: int | None = None,
    ) -> bool:
        """Read a sidecar lease database without creating it or mutating either database."""
        if not isinstance(database_path, str) or not database_path:
            raise ValueError("database_path must be non-empty text.")
        if not Path(database_path).exists():
            return False
        database = SQLiteDatabase.open_read_only(database_path)
        try:
            return cls.is_active_for_event(
                database.connection, event_id, now_epoch_ms=now_epoch_ms
            )
        finally:
            database.close()

    @classmethod
    def renew(
        cls,
        database: SQLiteDatabase,
        operation_id: str,
        lease_token: str,
        *,
        now_epoch_ms: int | None = None,
        lease_ttl_ms: int = DEFAULT_LEASE_TTL_MS,
    ) -> bool:
        """Extend an unexpired lease without allowing an expired owner to revive it."""
        cls._validate_identity(operation_id, "operation_id")
        cls._validate_identity(lease_token, "lease_token")
        now = cls._now(now_epoch_ms)
        ttl = cls._ttl(lease_ttl_ms)
        connection = database.connection
        if connection.in_transaction:
            raise RuntimeError("Execution leases require an owned SQLite transaction.")
        connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = connection.execute(
                f"""
                UPDATE {cls.TABLE_NAME}
                SET expires_at_epoch_ms = ?, updated_at_epoch_ms = ?
                WHERE operation_id = ?
                  AND lease_token = ?
                  AND expires_at_epoch_ms > ?
                """,
                (now + ttl, now, operation_id, lease_token, now),
            )
            connection.commit()
            return cursor.rowcount == 1
        except Exception:
            connection.rollback()
            raise

    def _create_schema(self) -> None:
        connection = self._database.connection
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.TABLE_NAME} (
                operation_id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL UNIQUE,
                lease_token TEXT NOT NULL,
                expires_at_epoch_ms INTEGER NOT NULL,
                updated_at_epoch_ms INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_candidate_schedule_leases_expiry
            ON {self.TABLE_NAME}(expires_at_epoch_ms)
            """
        )
        connection.commit()

    @staticmethod
    def _validate_identity(value: str, field: str) -> None:
        if not isinstance(value, str) or not value or value.strip() != value:
            raise ValueError(f"{field} must be non-empty canonical text.")

    @staticmethod
    def _now(value: int | None) -> int:
        if value is None:
            return time.time_ns() // 1_000_000
        if type(value) is not int or value < 0:
            raise ValueError("now_epoch_ms must be a non-negative integer.")
        return value

    @staticmethod
    def _ttl(value: int) -> int:
        if type(value) is not int or value <= 0:
            raise ValueError("lease_ttl_ms must be a positive integer.")
        return value


class SQLiteCandidateScheduleExecutionLease:
    """Hold and heartbeat an event-scoped SQLite lease around provider execution."""

    DEFAULT_LEASE_TTL_MS = SQLiteCandidateScheduleExecutionLeaseRepository.DEFAULT_LEASE_TTL_MS
    HEARTBEAT_INTERVAL_SECONDS = 5.0

    def __init__(
        self,
        database: SQLiteDatabase,
        database_path: str,
        operation_id: str,
        event_id: str,
        *,
        lease_ttl_ms: int | None = None,
        heartbeat_interval_seconds: float | None = None,
    ) -> None:
        self._database = database
        self._database_path = database_path
        self._operation_id = operation_id
        self._event_id = event_id
        self._lease_token = secrets.token_urlsafe(32)
        self._repository = SQLiteCandidateScheduleExecutionLeaseRepository(database)
        self._lease_ttl_ms = (
            self.DEFAULT_LEASE_TTL_MS if lease_ttl_ms is None else lease_ttl_ms
        )
        self._heartbeat_interval_seconds = (
            self.HEARTBEAT_INTERVAL_SECONDS
            if heartbeat_interval_seconds is None
            else heartbeat_interval_seconds
        )
        if type(self._lease_ttl_ms) is not int or self._lease_ttl_ms <= 0:
            raise ValueError("lease_ttl_ms must be a positive integer.")
        if (
            isinstance(self._heartbeat_interval_seconds, bool)
            or not isinstance(self._heartbeat_interval_seconds, (int, float))
            or not math.isfinite(self._heartbeat_interval_seconds)
            or self._heartbeat_interval_seconds <= 0
        ):
            raise ValueError("heartbeat_interval_seconds must be finite and positive.")
        self._stop = Event()
        self._thread: Thread | None = None
        self._acquired = False

    def acquire(self) -> bool:
        """Acquire the durable lease and start renewal for a file-backed database."""
        self._acquired = self._repository.acquire(
            self._operation_id,
            self._event_id,
            self._lease_token,
            lease_ttl_ms=self._lease_ttl_ms,
        )
        if self._acquired and not self._database.is_memory:
            self._thread = Thread(
                target=self._heartbeat,
                name="candidate-schedule-lease-heartbeat",
                daemon=True,
            )
            self._thread.start()
        return self._acquired

    def release(self) -> None:
        """Stop renewal and release only this request's lease."""
        if self._thread is not None:
            self._stop.set()
            self._thread.join(timeout=self._heartbeat_interval_seconds + 1.0)
            self._thread = None
        if self._acquired:
            self._repository.release(self._operation_id, self._lease_token)
            self._acquired = False

    def _heartbeat(self) -> None:
        while not self._stop.wait(self._heartbeat_interval_seconds):
            database = SQLiteDatabase(self._database_path)
            try:
                renewed = SQLiteCandidateScheduleExecutionLeaseRepository.renew(
                    database,
                    self._operation_id,
                    self._lease_token,
                    lease_ttl_ms=self._lease_ttl_ms,
                )
                if not renewed:
                    return
            except Exception:
                return
            finally:
                database.close()
