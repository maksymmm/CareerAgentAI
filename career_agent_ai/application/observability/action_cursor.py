"""Canonical URL-safe cursors for bounded operational action projections."""

from __future__ import annotations

import base64
import binascii
import hashlib
import re

ACTION_STATUSES = frozenset({
    "prepared",
    "in_progress",
    "failed",
    "reconciliation_required",
})
ACTION_FAMILIES = frozenset({
    "application_submission",
    "communication",
    "calendar",
    "other",
})
_FILTER_CURSOR_PREFIX = "\x00v2:"


def validate_action_filters(
    status: str | None,
    action_family: str | None,
) -> None:
    """Validate the closed filter vocabulary before any storage access."""
    if status is not None and (
        not isinstance(status, str) or status not in ACTION_STATUSES
    ):
        raise ValueError("status is not a supported operational action status.")
    if action_family is not None and (
        not isinstance(action_family, str) or action_family not in ACTION_FAMILIES
    ):
        raise ValueError("action_family is not a supported operational action family.")


def _validate_operation_id(operation_id: str) -> None:
    if (
        not isinstance(operation_id, str)
        or not operation_id
        or len(operation_id) > 200
        or operation_id != operation_id.strip()
        or any(
            ord(character) < 32
            or ord(character) == 127
            or 0xD800 <= ord(character) <= 0xDFFF
            for character in operation_id
        )
    ):
        raise ValueError("operation_id is not a valid operational action key.")


def _filter_digest(status: str | None, action_family: str | None) -> str:
    canonical = f"{status or ''}\x00{action_family or ''}".encode("ascii")
    return hashlib.sha256(canonical).hexdigest()[:24]


def encode_action_cursor(
    operation_id: str,
    *,
    status: str | None = None,
    action_family: str | None = None,
) -> str:
    """Encode one keyset key and bind filtered cursors to their exact query."""
    validate_action_filters(status, action_family)
    _validate_operation_id(operation_id)
    payload = operation_id
    if status is not None or action_family is not None:
        payload = (
            _FILTER_CURSOR_PREFIX
            + _filter_digest(status, action_family)
            + ":"
            + operation_id
        )
    try:
        cursor = (
            base64.urlsafe_b64encode(payload.encode("utf-8"))
            .decode("ascii")
            .rstrip("=")
        )
    except UnicodeEncodeError as exc:
        raise ValueError("operation_id is not valid UTF-8 text.") from exc
    if len(cursor) > 1200:
        raise ValueError("operation_id cannot be represented by a bounded cursor.")
    return cursor


def decode_action_cursor(
    value: str,
    *,
    status: str | None = None,
    action_family: str | None = None,
) -> str:
    """Decode a canonical cursor and reject reuse with a different filter query."""
    validate_action_filters(status, action_family)
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 1200
        or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None
    ):
        raise ValueError("after is not a valid operational action cursor.")
    try:
        decoded = base64.urlsafe_b64decode(
            value + "=" * (-len(value) % 4)
        ).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise ValueError("after is not a valid operational action cursor.") from exc

    if decoded.startswith(_FILTER_CURSOR_PREFIX):
        if status is None and action_family is None:
            raise ValueError("after cursor belongs to a filtered operational action query.")
        digest, separator, operation_id = decoded[len(_FILTER_CURSOR_PREFIX):].partition(":")
        if not separator or re.fullmatch(r"[a-f0-9]{24}", digest) is None:
            raise ValueError("after is not a valid operational action cursor.")
        _validate_operation_id(operation_id)
        if digest != _filter_digest(status, action_family):
            raise ValueError("after cursor does not match the operational action filters.")
        if encode_action_cursor(
            operation_id,
            status=status,
            action_family=action_family,
        ) != value:
            raise ValueError("after is not a valid operational action cursor.")
        return operation_id

    _validate_operation_id(decoded)
    if status is not None or action_family is not None:
        raise ValueError("after cursor is not bound to the operational action filters.")
    if encode_action_cursor(decoded) != value:
        raise ValueError("after is not a valid operational action cursor.")
    return decoded
