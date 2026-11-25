"""Canonical URL-safe cursors for bounded operational action projections."""

from __future__ import annotations

import base64
import binascii
import re

_FILTER_CURSOR_PREFIX = "\x00candidate-run-filter:"
_REFERENCE_PATTERN = re.compile(r"[a-f0-9]{24}\Z")


def _validate_operation_id(operation_id: str) -> None:
    """Reject operation keys that cannot be safely represented in a cursor."""
    if (
        not isinstance(operation_id, str)
        or not operation_id
        or len(operation_id) > 200
        or operation_id != operation_id.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in operation_id)
        or any(0xD800 <= ord(character) <= 0xDFFF for character in operation_id)
    ):
        raise ValueError("operation_id is not a valid operational action key.")


def _validate_candidate_run_ref(candidate_run_ref: str) -> None:
    """Require the exact public reference format used by candidate run status."""
    if (
        not isinstance(candidate_run_ref, str)
        or _REFERENCE_PATTERN.fullmatch(candidate_run_ref) is None
    ):
        raise ValueError("candidate_run_ref must be a 24-character lowercase hexadecimal reference.")


def encode_action_cursor(
    operation_id: str,
    *,
    candidate_run_ref: str | None = None,
) -> str:
    """Encode an operation key and bind it to the optional candidate-run filter."""
    _validate_operation_id(operation_id)
    payload = operation_id
    if candidate_run_ref is not None:
        _validate_candidate_run_ref(candidate_run_ref)
        payload = f"{_FILTER_CURSOR_PREFIX}{candidate_run_ref}:{operation_id}"
    cursor = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
    if len(cursor) > 1200:
        raise ValueError("operation_id cannot be represented by a bounded cursor.")
    return cursor


def decode_action_cursor(
    value: str,
    *,
    candidate_run_ref: str | None = None,
) -> str:
    """Decode a canonical cursor and reject reuse across candidate-run filters."""
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

    if decoded.startswith(_FILTER_CURSOR_PREFIX):
        if candidate_run_ref is None:
            raise ValueError("after cursor belongs to a filtered operational action query.")
        digest, separator, operation_id = decoded[len(_FILTER_CURSOR_PREFIX):].partition(":")
        if not separator:
            raise ValueError("after is not a valid operational action cursor.")
        _validate_candidate_run_ref(digest)
        if digest != candidate_run_ref:
            raise ValueError("after cursor does not match the candidate-run filter.")
        if encode_action_cursor(operation_id, candidate_run_ref=digest) != value:
            raise ValueError("after is not a valid operational action cursor.")
        return operation_id

    if candidate_run_ref is not None:
        raise ValueError("after cursor is not bound to the candidate-run filter.")
    if encode_action_cursor(decoded) != value:
        raise ValueError("after is not a valid operational action cursor.")
    return decoded
