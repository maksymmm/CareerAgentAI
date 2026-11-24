"""Canonical URL-safe cursors for bounded operational action projections."""

from __future__ import annotations

import base64
import binascii
import re


def encode_action_cursor(operation_id: str) -> str:
    """Encode one validated operation key without placing it in a response field."""
    if (
        not isinstance(operation_id, str)
        or not operation_id
        or len(operation_id) > 200
        or operation_id != operation_id.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in operation_id)
    ):
        raise ValueError("operation_id is not a valid operational action key.")
    cursor = base64.urlsafe_b64encode(operation_id.encode("utf-8")).decode("ascii").rstrip("=")
    if len(cursor) > 1200:
        raise ValueError("operation_id cannot be represented by a bounded cursor.")
    return cursor


def decode_action_cursor(value: str) -> str:
    """Decode and validate one canonical, bounded operational action cursor."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 1200
        or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None
    ):
        raise ValueError("after is not a valid operational action cursor.")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise ValueError("after is not a valid operational action cursor.") from exc
    if encode_action_cursor(decoded) != value:
        raise ValueError("after is not a valid operational action cursor.")
    return decoded
