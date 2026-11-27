"""Versioned candidate schema validation before durable provider composition."""

from __future__ import annotations

from typing import Any


CANDIDATE_SCHEMA_VERSION = 1

_REQUIRED_LOOP_COLUMNS = frozenset(
    {
        "run_id",
        "owner_user_id",
        "phase",
        "payload_json",
        "updated_at",
        "version",
        "execution_claim_owner",
        "execution_claim_expires_at",
    }
)

_REQUIRED_CANDIDATE_TABLE_COLUMNS = {
    "job_applications": frozenset(
        {
            "application_id",
            "user_id",
            "job_id",
            "company_id",
            "status",
            "created_at",
            "updated_at",
            "version",
            "serialization_version",
            "submission_claim_operation_id",
            "submission_claim_owner_id",
            "job_title",
            "job_skills_json",
        }
    ),
    "application_external_actions": frozenset({"application_id", "operation_id"}),
    "application_timeline": frozenset(
        {
            "event_id",
            "application_id",
            "from_status",
            "to_status",
            "occurred_at",
            "operation_id",
            "note",
            "metadata_json",
            "serialization_version",
            "sequence",
        }
    ),
    "external_action_operations": frozenset(
        {
            "operation_id",
            "action_type",
            "payload_json",
            "status",
            "result_json",
            "error",
            "created_at",
            "updated_at",
        }
    ),
    "communication_messages": frozenset(
        {
            "message_id",
            "thread_id",
            "sender",
            "recipient",
            "subject",
            "body",
            "direction",
            "created_at",
            "created_at_epoch_us",
            "in_reply_to",
            "delivery_operation_id",
        }
    ),
    "scheduling_events": frozenset(
        {
            "event_id",
            "candidate_id",
            "employer_name",
            "event_type",
            "location",
            "start_at",
            "start_epoch_us",
            "end_at",
            "end_epoch_us",
            "timezone_name",
            "status",
            "provider_event_id",
            "application_id",
            "version",
            "active_action_operation_id",
            "reservation_start_epoch_us",
            "reservation_end_epoch_us",
            "created_at",
            "updated_at",
        }
    ),
    "sandbox_submission_intents": frozenset({"idempotency_key", "fingerprint"}),
    "sandbox_submission_attempts": frozenset(
        {"idempotency_key", "attempt_no", "outcome"}
    ),
    "sandbox_submission_ledger": frozenset(
        {
            "idempotency_key",
            "fingerprint",
            "job_id",
            "application_id",
            "artifact_sha256",
            "candidate_snapshot_sha256",
            "provider_submission_id",
            "replays",
        }
    ),
    "sandbox_communication_messages": frozenset(
        {
            "message_id",
            "thread_id",
            "sender",
            "recipient",
            "subject",
            "body",
            "direction",
            "created_at",
            "in_reply_to",
        }
    ),
    "sandbox_communication_intents": frozenset({"operation_id", "fingerprint"}),
    "sandbox_communication_attempts": frozenset(
        {"operation_id", "attempt_no", "outcome"}
    ),
    "sandbox_communication_deliveries": frozenset(
        {"operation_id", "fingerprint", "message_id", "action", "replays"}
    ),
    "sandbox_calendar_intents": frozenset({"operation_id", "fingerprint"}),
    "sandbox_calendar_attempts": frozenset(
        {"operation_id", "attempt_no", "outcome"}
    ),
    "sandbox_calendar_effects": frozenset(
        {"operation_id", "fingerprint", "action", "event_json", "replays"}
    ),
}

_REQUIRED_CANDIDATE_TABLES = (
    "autonomous_career_loops",
    *_REQUIRED_CANDIDATE_TABLE_COLUMNS,
)

_REQUIRED_PROVIDER_LEDGER_TABLES = frozenset(
    {
        "external_action_operations",
        "sandbox_submission_intents",
        "sandbox_submission_attempts",
        "sandbox_submission_ledger",
        "sandbox_communication_messages",
        "sandbox_communication_intents",
        "sandbox_communication_attempts",
        "sandbox_communication_deliveries",
        "sandbox_calendar_intents",
        "sandbox_calendar_attempts",
        "sandbox_calendar_effects",
    }
)

_CANDIDATE_SCHEMA_MANIFEST = "candidate_schema_manifest"


def _table_exists(connection: Any, table: str) -> bool:
    """Return whether a required object is a real SQLite table."""
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        is not None
    )


def _table_columns(connection: Any, table: str) -> frozenset[str]:
    """Return columns only for an existing table, never for a view or absent object."""
    if not _table_exists(connection, table):
        return frozenset()
    rows = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    return frozenset(row[1] for row in rows)


def _validate_loop_schema(connection: Any) -> None:
    """Reject missing or partially migrated candidate loop storage."""
    columns = _table_columns(connection, "autonomous_career_loops")
    if not _REQUIRED_LOOP_COLUMNS.issubset(columns):
        raise RuntimeError("candidate schema is unavailable")


def _validate_candidate_tables(connection: Any, *, full: bool) -> None:
    """Validate durable tables and provider-ledger columns before legacy migration."""
    for table in _REQUIRED_CANDIDATE_TABLES:
        columns = _table_columns(connection, table)
        if not columns:
            raise RuntimeError("candidate schema is unavailable")
        required = _REQUIRED_CANDIDATE_TABLE_COLUMNS.get(table, frozenset())
        if full or table in _REQUIRED_PROVIDER_LEDGER_TABLES:
            if not required.issubset(columns):
                raise RuntimeError("candidate schema is unavailable")


def _validate_schema_manifest(connection: Any) -> None:
    """Reject an absent, malformed, or unsupported durable schema marker."""
    manifest_columns = _table_columns(connection, _CANDIDATE_SCHEMA_MANIFEST)
    if not {"singleton_id", "schema_version"}.issubset(manifest_columns):
        raise RuntimeError("candidate schema is unavailable")
    rows = connection.execute(
        f'SELECT singleton_id, schema_version FROM "{_CANDIDATE_SCHEMA_MANIFEST}" '
        "ORDER BY singleton_id"
    ).fetchall()
    if rows != [(1, CANDIDATE_SCHEMA_VERSION)]:
        raise RuntimeError("candidate schema is unavailable")


def _validate_candidate_schema_without_manifest(connection: Any) -> None:
    """Validate every candidate table and column before recording the manifest."""
    _validate_loop_schema(connection)
    _validate_candidate_tables(connection, full=True)


def _validate_candidate_schema(connection: Any) -> None:
    """Validate the complete candidate schema and its durable version marker."""
    _validate_candidate_schema_without_manifest(connection)
    _validate_schema_manifest(connection)


def _candidate_database_has_history(connection: Any) -> bool:
    """Detect durable candidate rows before deciding whether missing tables are safe to bootstrap."""
    for table in _REQUIRED_CANDIDATE_TABLES:
        if not _table_exists(connection, table):
            continue
        try:
            if connection.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone() is not None:
                return True
        except Exception as exc:
            raise RuntimeError("candidate schema is unavailable") from exc
    return False


def _prepare_candidate_schema(connection: Any) -> None:
    """Reject ledger loss before CREATE TABLE IF NOT EXISTS can hide it."""
    if _table_exists(connection, _CANDIDATE_SCHEMA_MANIFEST):
        _validate_schema_manifest(connection)
        # Repositories retain their additive core-table migrations. Provider
        # ledgers are immutable evidence stores and must be complete before
        # any constructor can recreate or alter them.
        _validate_candidate_tables(connection, full=False)
        return
    if _candidate_database_has_history(connection):
        # Existing candidate data may adopt the manifest only if every durable
        # table is present and all provider-ledger columns are already intact.
        # Additive core-table migrations remain available to the repositories.
        _validate_candidate_tables(connection, full=False)


def _ensure_candidate_schema_manifest(connection: Any) -> None:
    """Record the schema version only after every repository and provider has initialized."""
    _validate_candidate_schema_without_manifest(connection)
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_CANDIDATE_SCHEMA_MANIFEST} (
            singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
            schema_version INTEGER NOT NULL CHECK(schema_version >= 1)
        )
        """
    )
    rows = connection.execute(
        f'SELECT singleton_id, schema_version FROM "{_CANDIDATE_SCHEMA_MANIFEST}" '
        "ORDER BY singleton_id"
    ).fetchall()
    if not rows:
        connection.execute(
            f"INSERT INTO {_CANDIDATE_SCHEMA_MANIFEST}(singleton_id, schema_version) "
            "VALUES (1, ?)",
            (CANDIDATE_SCHEMA_VERSION,),
        )
    elif rows != [(1, CANDIDATE_SCHEMA_VERSION)]:
        raise RuntimeError("candidate schema is unavailable")
    connection.commit()
    _validate_candidate_schema(connection)
