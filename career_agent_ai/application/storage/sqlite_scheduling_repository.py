"""SQLite persistence for scheduling events."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Sequence

from career_agent_ai.application.scheduling.models import (
    ScheduleEvent,
    ScheduleEventType,
    ScheduleStatus,
    normalize_aware_datetime,
    validate_schedule_identifier,
)
from career_agent_ai.application.scheduling.scheduling_repository import (
    SchedulingConflictError,
    SchedulingOperationConflict,
)
from career_agent_ai.application.storage.sqlite_database import SQLiteDatabase


class SQLiteSchedulingRepository:
    """Restart-safe, concurrency-aware scheduling persistence."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database
        self._create_schema()

    def create(self, event: ScheduleEvent) -> ScheduleEvent:
        """Insert one event idempotently while rejecting duplicate provider identity."""
        try:
            self._database.connection.execute(
                """
                INSERT INTO scheduling_events (
                    event_id, candidate_id, employer_name, event_type, location,
                    start_at, start_epoch_us, end_at, end_epoch_us, timezone_name,
                    status, provider_event_id, application_id, version,
                    active_action_operation_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                self._insert_values(event),
            )
            self._database.connection.commit()
            return event
        except sqlite3.IntegrityError as exc:
            self._database.connection.rollback()
        existing = self.get(event.event_id)
        if existing is not None and existing == event:
            return existing
        raise SchedulingConflictError(
            "Scheduling event identity is already bound to different data."
        ) from exc

    def get(self, event_id: str) -> ScheduleEvent | None:
        normalized = validate_schedule_identifier(event_id, "event_id")
        row = self._database.connection.execute(
            """
            SELECT event_id, candidate_id, employer_name, event_type, location,
                   start_at, start_epoch_us, end_at, end_epoch_us, timezone_name,
                   status, provider_event_id, application_id, version,
                   created_at, updated_at
            FROM scheduling_events WHERE event_id = ?
            """,
            (normalized,),
        ).fetchone()
        if row is None:
            return None
        try:
            event = ScheduleEvent(
                event_id=row[0],
                candidate_id=row[1],
                employer_name=row[2],
                event_type=ScheduleEventType(row[3]),
                location=row[4],
                start_at=datetime.fromisoformat(row[5]),
                end_at=None if row[7] is None else datetime.fromisoformat(row[7]),
                timezone_name=row[9],
                status=ScheduleStatus(row[10]),
                provider_event_id=row[11],
                application_id=row[12],
                version=row[13],
                created_at=datetime.fromisoformat(row[14]),
                updated_at=datetime.fromisoformat(row[15]),
            )
            if row[6] != self._epoch_microseconds(event.start_at):
                raise ValueError("Persisted start timestamp index is inconsistent.")
            expected_end = (
                None
                if event.end_at is None
                else self._epoch_microseconds(event.end_at)
            )
            if row[8] != expected_end:
                raise ValueError("Persisted end timestamp index is inconsistent.")
            return event
        except (TypeError, ValueError) as exc:
            raise ValueError("Persisted scheduling event is malformed.") from exc

    def list_candidate(self, candidate_id: str) -> tuple[ScheduleEvent, ...]:
        normalized = validate_schedule_identifier(candidate_id, "candidate_id")
        rows = self._database.connection.execute(
            """
            SELECT event_id FROM scheduling_events
            WHERE candidate_id = ?
            ORDER BY start_epoch_us, event_id
            """,
            (normalized,),
        ).fetchall()
        events = tuple(self.get(row[0]) for row in rows)
        if any(event is None for event in events):
            raise ValueError("Scheduling events changed while being read.")
        return tuple(event for event in events if event is not None)

    def find_conflicts(
        self,
        candidate_id: str,
        start_at: datetime,
        end_at: datetime | None,
        *,
        exclude_event_id: str | None = None,
    ) -> tuple[ScheduleEvent, ...]:
        start = normalize_aware_datetime(start_at, "start_at")
        end = None if end_at is None else normalize_aware_datetime(end_at, "end_at")
        if end is not None and end <= start:
            raise ValueError("end_at must be later than start_at.")
        excluded = (
            None
            if exclude_event_id is None
            else validate_schedule_identifier(exclude_event_id, "exclude_event_id")
        )
        conflicts = [
            event
            for event in self.list_candidate(candidate_id)
            if event.event_id != excluded
            and event.status not in {ScheduleStatus.DECLINED, ScheduleStatus.CANCELLED}
            and self._overlaps(event.start_at, event.end_at, start, end)
        ]
        return tuple(conflicts)

    def claim_action(
        self,
        event_id: str,
        operation_id: str,
        allowed_statuses: Sequence[ScheduleStatus],
    ) -> ScheduleEvent:
        event_id = validate_schedule_identifier(event_id, "event_id")
        operation_id = validate_schedule_identifier(operation_id, "operation_id")
        statuses = tuple(allowed_statuses)
        if not statuses or any(not isinstance(status, ScheduleStatus) for status in statuses):
            raise ValueError("allowed_statuses must contain ScheduleStatus values.")
        placeholders = ",".join("?" for _ in statuses)
        params = (
            operation_id,
            event_id,
            *(status.value for status in statuses),
            operation_id,
        )
        try:
            cursor = self._database.connection.execute(
                f"""
                UPDATE scheduling_events
                SET active_action_operation_id = ?
                WHERE event_id = ?
                  AND status IN ({placeholders})
                  AND (
                    active_action_operation_id IS NULL
                    OR active_action_operation_id = ?
                  )
                """,
                params,
            )
            self._database.connection.commit()
        except sqlite3.IntegrityError as exc:
            self._database.connection.rollback()
            raise SchedulingOperationConflict(
                "Scheduling operation is already bound to another event."
            ) from exc
        if cursor.rowcount != 1:
            raise SchedulingOperationConflict(
                "Event is not claimable in its current state."
            )
        event = self.get(event_id)
        if event is None:
            raise SchedulingOperationConflict("Claimed event disappeared.")
        return event

    def release_action(self, event_id: str, operation_id: str) -> None:
        event_id = validate_schedule_identifier(event_id, "event_id")
        operation_id = validate_schedule_identifier(operation_id, "operation_id")
        cursor = self._database.connection.execute(
            """
            UPDATE scheduling_events
            SET active_action_operation_id = NULL
            WHERE event_id = ? AND active_action_operation_id = ?
            """,
            (event_id, operation_id),
        )
        self._database.connection.commit()
        if cursor.rowcount != 1:
            raise SchedulingOperationConflict(
                "Scheduling action claim is no longer releasable."
            )

    def complete_action(
        self,
        event: ScheduleEvent,
        operation_id: str,
        *,
        expected_version: int,
    ) -> ScheduleEvent:
        operation_id = validate_schedule_identifier(operation_id, "operation_id")
        if event.version != expected_version + 1:
            raise ValueError("Completed event must advance version exactly once.")
        current = self.get(event.event_id)
        if current is None:
            raise SchedulingOperationConflict("Scheduling event no longer exists.")
        if self._immutable(current) != self._immutable(event):
            raise ValueError("Completed scheduling event changed immutable fields.")
        try:
            cursor = self._database.connection.execute(
                """
                UPDATE scheduling_events
                SET start_at = ?, start_epoch_us = ?, end_at = ?, end_epoch_us = ?,
                    timezone_name = ?, status = ?, provider_event_id = ?,
                    version = ?, updated_at = ?, active_action_operation_id = NULL
                WHERE event_id = ? AND version = ?
                  AND active_action_operation_id = ?
                """,
                (
                    event.start_at.isoformat(),
                    self._epoch_microseconds(event.start_at),
                    None if event.end_at is None else event.end_at.isoformat(),
                    (
                        None
                        if event.end_at is None
                        else self._epoch_microseconds(event.end_at)
                    ),
                    event.timezone_name,
                    event.status.value,
                    event.provider_event_id,
                    event.version,
                    event.updated_at.isoformat(),
                    event.event_id,
                    expected_version,
                    operation_id,
                ),
            )
            self._database.connection.commit()
        except sqlite3.IntegrityError as exc:
            self._database.connection.rollback()
            raise SchedulingOperationConflict(
                "Provider event identity conflicts with another scheduling event."
            ) from exc
        if cursor.rowcount != 1:
            raise SchedulingOperationConflict(
                "Scheduling event changed or lost its action claim."
            )
        persisted = self.get(event.event_id)
        if persisted is None:
            raise SchedulingOperationConflict("Completed scheduling event disappeared.")
        return persisted

    def _create_schema(self) -> None:
        connection = self._database.connection
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduling_events (
                event_id TEXT PRIMARY KEY,
                candidate_id TEXT NOT NULL,
                employer_name TEXT NOT NULL,
                event_type TEXT NOT NULL CHECK(event_type IN (
                    'interview', 'trial_day', 'phone_call', 'video_call', 'other'
                )),
                location TEXT NOT NULL,
                start_at TEXT NOT NULL,
                start_epoch_us INTEGER NOT NULL,
                end_at TEXT,
                end_epoch_us INTEGER,
                timezone_name TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN (
                    'proposed', 'accepted', 'declined',
                    'reschedule_requested', 'cancelled'
                )),
                provider_event_id TEXT,
                application_id TEXT,
                version INTEGER NOT NULL CHECK(version >= 1),
                active_action_operation_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_scheduling_provider_event
               ON scheduling_events(provider_event_id)
               WHERE provider_event_id IS NOT NULL"""
        )
        connection.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_scheduling_active_action
               ON scheduling_events(active_action_operation_id)
               WHERE active_action_operation_id IS NOT NULL"""
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_scheduling_candidate_start
               ON scheduling_events(candidate_id, start_epoch_us, event_id)"""
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_scheduling_candidate_status
               ON scheduling_events(candidate_id, status)"""
        )
        connection.commit()

    @staticmethod
    def _insert_values(event: ScheduleEvent) -> tuple[object, ...]:
        return (
            event.event_id,
            event.candidate_id,
            event.employer_name,
            event.event_type.value,
            event.location,
            event.start_at.isoformat(),
            SQLiteSchedulingRepository._epoch_microseconds(event.start_at),
            None if event.end_at is None else event.end_at.isoformat(),
            (
                None
                if event.end_at is None
                else SQLiteSchedulingRepository._epoch_microseconds(event.end_at)
            ),
            event.timezone_name,
            event.status.value,
            event.provider_event_id,
            event.application_id,
            event.version,
            event.created_at.isoformat(),
            event.updated_at.isoformat(),
        )

    @staticmethod
    def _immutable(event: ScheduleEvent) -> tuple[object, ...]:
        return (
            event.event_id,
            event.candidate_id,
            event.employer_name,
            event.event_type,
            event.location,
            event.application_id,
            event.created_at,
        )

    @staticmethod
    def _overlaps(
        first_start: datetime,
        first_end: datetime | None,
        second_start: datetime,
        second_end: datetime | None,
    ) -> bool:
        first_stop = first_start if first_end is None else first_end
        second_stop = second_start if second_end is None else second_end
        if first_stop == first_start and second_stop == second_start:
            return first_start == second_start
        if first_stop == first_start:
            return second_start <= first_start < second_stop
        if second_stop == second_start:
            return first_start <= second_start < first_stop
        return first_start < second_stop and second_start < first_stop

    @staticmethod
    def _epoch_microseconds(value: datetime) -> int:
        utc_value = normalize_aware_datetime(value, "timestamp")
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        delta = utc_value - epoch
        return (
            delta.days * 86_400_000_000
            + delta.seconds * 1_000_000
            + delta.microseconds
        )
