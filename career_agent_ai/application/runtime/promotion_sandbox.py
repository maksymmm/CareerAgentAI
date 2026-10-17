"""Request-local SQLite composition for the no-I/O operator promotion API."""

from __future__ import annotations

import os
import sqlite3
from uuid import uuid4
from typing import Any, Callable, Iterable, Mapping

from career_agent_ai.application.api import PromotionApprovalWSGIApp, ReleaseOperator
from career_agent_ai.application.external_actions import ExternalActionOperation, ExternalActionStatus
from career_agent_ai.application.release import PromotionApprovalReceiptService
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase
from career_agent_ai.application.storage.sqlite_external_action_repository import SQLiteExternalActionOperationRepository
from .config import RuntimeConfig


class _RequestOperations:
    """Open storage lazily, after routing and operator authorization succeed."""

    def __init__(self, path: str, *, read_only: bool = False) -> None:
        self._path = path
        self._read_only = read_only
        self._database: SQLiteDatabase | None = None
        self._repository: SQLiteExternalActionOperationRepository | None = None

    def _store(self) -> SQLiteExternalActionOperationRepository:
        if self._repository is None:
            self._database = (SQLiteDatabase.open_read_only(self._path) if self._read_only
                              else SQLiteDatabase(self._path))
            if self._database.is_memory:
                raise ValueError("Promotion sandbox requires durable storage")
            self._repository = SQLiteExternalActionOperationRepository(self._database)
        return self._repository

    def get(self, operation_id: str) -> ExternalActionOperation | None:
        """Read one operation on the request-owned connection."""
        return self._store().get(operation_id)

    def create(self, operation: ExternalActionOperation) -> ExternalActionOperation:
        """Persist an immutable approval on the request-owned connection."""
        return self._store().create(operation)

    def transition(self, operation_id: str, expected_status: ExternalActionStatus,
                   status: ExternalActionStatus, *, result: Mapping[str, Any] | None = None,
                   error: str | None = None) -> ExternalActionOperation:
        """Apply an atomic lifecycle transition using the durable repository."""
        return self._store().transition(operation_id, expected_status, status, result=result, error=error)

    def close(self) -> None:
        """Release storage even when validation, response, or persistence fails."""
        if self._database is not None:
            self._database.close()


class RuntimePromotionSandboxApp:
    """Serve approvals and explicit dry runs with no shared SQLite connections."""

    def __init__(self, *, database_path: str,
                 resolve_bearer: Callable[[str], ReleaseOperator | None]) -> None:
        self._database_path = database_path
        self._resolve_bearer = resolve_bearer
        self._closed = False

    def __call__(self, environ: Mapping[str, Any], start_response: Callable) -> Iterable[bytes]:
        """Authenticate before opening request-local storage and always close it."""
        if self._closed:
            raise RuntimeError("Promotion sandbox runtime is closed")
        operations = _RequestOperations(self._database_path,
            read_only=str(environ.get("PATH_INFO", "")).strip("/").endswith("/status"))
        try:
            application = PromotionApprovalWSGIApp(operations,
                receipts=PromotionApprovalReceiptService(operations),
                resolve_bearer=self._resolve_bearer)
            return tuple(application(environ, start_response))
        finally:
            operations.close()

    def close(self) -> None:
        """Prevent new requests; active requests own their connection lifecycle."""
        self._closed = True

    def __enter__(self) -> RuntimePromotionSandboxApp:
        """Return this open sandbox runtime."""
        if self._closed:
            raise RuntimeError("Promotion sandbox runtime is closed")
        return self

    def __exit__(self, *_: object) -> None:
        """Close the runtime on context exit."""
        self.close()


def build_promotion_sandbox_app_from_env(*,
        resolve_bearer: Callable[[str], ReleaseOperator | None],
        environ: Mapping[str, str] | None = None) -> RuntimePromotionSandboxApp:
    """Compose only fixed no-I/O adapters behind explicit sandbox policy gates."""
    source = os.environ if environ is None else environ
    if not RuntimeConfig._parse_bool(source.get("CAREER_AGENT_PROMOTION_SANDBOX", "false"),
                                     "CAREER_AGENT_PROMOTION_SANDBOX"):
        raise PermissionError("Promotion sandbox must be explicitly enabled")
    config = RuntimeConfig.from_env(source)
    if config.allow_network_providers or config.allow_consequential_actions:
        raise ValueError("Promotion sandbox forbids network providers and consequential actions")
    if RuntimeConfig._is_sqlite_memory_path(config.database_path):
        raise ValueError("Promotion sandbox requires durable storage")
    if not callable(resolve_bearer):
        raise TypeError("resolve_bearer must be callable")
    # Validate SQLite's actual backing before allowing any request-local composition.
    database = SQLiteDatabase(config.database_path)
    try:
        if database.is_memory:
            raise ValueError("Promotion sandbox requires durable storage")
        try:
            # Exercise the main database, not TEMP: TEMP can be writable on a
            # read-only connection. Explicit BEGIN makes SQLite DDL rollback-safe.
            database.connection.execute("BEGIN IMMEDIATE")
            probe_name = "promotion_write_probe_" + uuid4().hex
            database.connection.execute(f'CREATE TABLE "{probe_name}" (value INTEGER)')
        except sqlite3.Error as exc:
            raise ValueError("Promotion sandbox requires writable SQLite storage") from exc
        finally:
            database.connection.rollback()
    finally:
        database.close()
    return RuntimePromotionSandboxApp(database_path=config.database_path, resolve_bearer=resolve_bearer)
