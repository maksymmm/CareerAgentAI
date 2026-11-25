"""Canonical run-bound cursors that never disclose provider operation identifiers."""

from __future__ import annotations

import base64
import binascii
import re

from career_agent_ai.application.observability.references import stable_reference

_FILTER_CURSOR_PREFIX = "\x00candidate-run-filter:"
_REFERENCE_PATTERN = re.compile(r"[a-f0-9]{24}\Z")


def _validate_operation_id(operation_id: str) -> None:
    """Reject operation keys that cannot be safely represented by a stable reference."""
    if (
        not isinstance(operation_id, str)
        or not operation_id
        or len(operation_id) > 200
        or operation_id != operation_id.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in operation_id)
        or any(0xD800 <= ord(character) <= 0xDFFF for character in operation_id)
    ):
        raise ValueError("operation_id is not a valid operational action key.")


def _validate_reference(value: str, field: str) -> None:
    """Require the compact, non-reversible reference format used by the API."""
    if not isinstance(value, str) or _REFERENCE_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field} must be a 24-character lowercase hexadecimal reference.")


def _encode_reference_cursor(operation_ref: str, candidate_run_ref: str) -> str:
    _validate_reference(operation_ref, "operation_ref")
    _validate_reference(candidate_run_ref, "candidate_run_ref")
    payload = f"{_FILTER_CURSOR_PREFIX}{candidate_run_ref}:{operation_ref}"
    return base64.urlsafe_b64encode(payload.encode("ascii")).decode("ascii").rstrip("=")


def encode_action_cursor(
    operation_id: str,
    *,
    candidate_run_ref: str,
) -> str:
    """Encode only stable references; the raw operation identifier never enters the cursor."""
    _validate_operation_id(operation_id)
    _validate_reference(candidate_run_ref, "candidate_run_ref")
    cursor = _encode_reference_cursor(
        stable_reference(operation_id),
        candidate_run_ref,
    )
    if len(cursor) > 1200:
        raise ValueError("operation reference cannot be represented by a bounded cursor.")
    return cursor


def decode_action_cursor(
    value: str,
    *,
    candidate_run_ref: str,
) -> str:
    """Return the operation reference from a canonical cursor bound to one candidate run."""
    _validate_reference(candidate_run_ref, "candidate_run_ref")
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 1200
        or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None
    ):
        raise ValueError("after is not a valid operational action cursor.")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("ascii")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise ValueError("after is not a valid operational action cursor.") from exc

    if not decoded.startswith(_FILTER_CURSOR_PREFIX):
        raise ValueError("after cursor is not bound to a candidate-run filter.")
    digest, separator, operation_ref = decoded[len(_FILTER_CURSOR_PREFIX):].partition(":")
    if not separator:
        raise ValueError("after is not a valid operational action cursor.")
    _validate_reference(digest, "candidate_run_ref")
    _validate_reference(operation_ref, "operation_ref")
    if digest != candidate_run_ref:
        raise ValueError("after cursor does not match the candidate-run filter.")
    if _encode_reference_cursor(operation_ref, digest) != value:
        raise ValueError("after is not a valid operational action cursor.")
    return operation_ref
