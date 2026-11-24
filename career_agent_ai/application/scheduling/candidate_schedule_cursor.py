"""Canonical keyset cursors bound to candidate schedule filters."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re

from .models import ScheduleEventType, ScheduleStatus, validate_schedule_identifier

_CURSOR = re.compile(r"^sc1~\.([A-Za-z0-9_-]{1,267})\.([0-9a-f]{64})$")


def _filter_digest(status: ScheduleStatus | None, event_type: ScheduleEventType | None) -> str:
    if status is not None and not isinstance(status, ScheduleStatus):
        raise TypeError("status must be a ScheduleStatus.")
    if event_type is not None and not isinstance(event_type, ScheduleEventType):
        raise TypeError("event_type must be a ScheduleEventType.")
    values = ("" if status is None else status.value, "" if event_type is None else event_type.value)
    return hashlib.sha256((values[0] + "\0" + values[1]).encode("ascii")).hexdigest()


def encode_schedule_event_cursor(
    event_id: str, *, status: ScheduleStatus | None = None,
    event_type: ScheduleEventType | None = None,
) -> str:
    """Encode a canonical cursor and bind it to the exact active filters."""
    identifier = validate_schedule_identifier(event_id, "event_id")
    if status is None and event_type is None:
        return identifier
    digest = _filter_digest(status, event_type)
    encoded = base64.urlsafe_b64encode(identifier.encode("ascii")).decode("ascii").rstrip("=")
    return f"sc1~.{encoded}.{digest}"


def decode_schedule_event_cursor(
    cursor: str, *, status: ScheduleStatus | None = None,
    event_type: ScheduleEventType | None = None,
) -> str:
    """Return the last event ID, rejecting cursors replayed under other filters."""
    if not isinstance(cursor, str):
        raise TypeError("after must be text.")
    if not cursor or len(cursor) > 600 or cursor != cursor.strip():
        raise ValueError("after is malformed.")
    match = _CURSOR.fullmatch(cursor)
    if match is not None:
        if status is None and event_type is None:
            raise ValueError("A filtered cursor cannot be used without its filters.")
        encoded, supplied_digest = match.groups()
        if not hmac.compare_digest(supplied_digest, _filter_digest(status, event_type)):
            raise ValueError("Cursor filters do not match the request.")
        try:
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            identifier = raw.decode("ascii")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("after is malformed.") from exc
        if base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != encoded:
            raise ValueError("after is not canonical.")
        normalized = validate_schedule_identifier(identifier, "after")
        if normalized != identifier:
            raise ValueError("after is not canonical.")
        return normalized
    # Preserve already-issued status-only cursors during rollout. New filtered
    # pages always emit sc1 cursors; event-type filtering never accepts a legacy ID.
    if event_type is not None:
        raise ValueError("An event-type filter requires a filter-bound cursor.")
    normalized = validate_schedule_identifier(cursor, "after")
    if normalized != cursor:
        raise ValueError("after is not canonical.")
    return normalized
