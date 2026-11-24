"""Canonical URL-safe cursors for bounded operational action projections."""

from __future__ import annotations

import base64
import binascii
import re

_FILTER_CURSOR_PREFIX = "\x00candidate-run-filter:"
_ACTION_FILTER_CURSOR_PREFIX = "\x00candidate-action-filter:"
_REFERENCE_PATTERN = re.compile(r"[a-f0-9]{24}\Z")
_ACTION_STATUSES = frozenset({
    "prepared", "in_progress", "succeeded", "failed", "reconciliation_required",
})
_ACTION_FAMILIES = frozenset({
    "application_submission", "communication", "calendar", "other",
})


def _validate_action_filters(status: str | None, action_family: str | None) -> None:
    """Validate the exact public filter vocabulary before cursor encoding."""
    if status is not None and (
        not isinstance(status, str) or status not in _ACTION_STATUSES
    ):
        raise ValueError("status is not a supported candidate action status.")
    if action_family is not None and (
        not isinstance(action_family, str) or action_family not in _ACTION_FAMILIES
    ):
        raise ValueError("action_family is not a supported candidate action family.")


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
    status: str | None = None,
    action_family: str | None = None,
) -> str:
    """Encode an operation key bound to its candidate run and exact filter set."""
    _validate_operation_id(operation_id)
    _validate_action_filters(status, action_family)
    payload = operation_id
    if status is not None or action_family is not None:
        if candidate_run_ref is None:
            raise ValueError("candidate action filters require candidate_run_ref.")
        _validate_candidate_run_ref(candidate_run_ref)
        status_token = status if status is not None else "*"
        family_token = action_family if action_family is not None else "*"
        payload = (
            f"{_ACTION_FILTER_CURSOR_PREFIX}{candidate_run_ref}:"
            f"{status_token}:{family_token}:{operation_id}"
        )
    elif candidate_run_ref is not None:
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
    status: str | None = None,
    action_family: str | None = None,
) -> str:
    """Decode a canonical cursor and reject reuse across run or action filters."""
    _validate_action_filters(status, action_family)
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

    if decoded.startswith(_ACTION_FILTER_CURSOR_PREFIX):
        if candidate_run_ref is None or (status is None and action_family is None):
            raise ValueError("after cursor belongs to a filtered candidate action query.")
        rest = decoded[len(_ACTION_FILTER_CURSOR_PREFIX):]
        digest, separator, rest = rest.partition(":")
        status_token, status_separator, rest = rest.partition(":")
        family_token, family_separator, operation_id = rest.partition(":")
        if not separator or not status_separator or not family_separator:
            raise ValueError("after is not a valid operational action cursor.")
        _validate_candidate_run_ref(digest)
        expected_status = status if status is not None else "*"
        expected_family = action_family if action_family is not None else "*"
        if (
            digest != candidate_run_ref
            or status_token != expected_status
            or family_token != expected_family
        ):
            raise ValueError("after cursor does not match the candidate action filters.")
        if encode_action_cursor(
            operation_id,
            candidate_run_ref=digest,
            status=status,
            action_family=action_family,
        ) != value:
            raise ValueError("after is not a valid operational action cursor.")
        return operation_id

    if decoded.startswith(_FILTER_CURSOR_PREFIX):
        if candidate_run_ref is None or status is not None or action_family is not None:
            raise ValueError("after cursor is not bound to the candidate action filters.")
        digest, separator, operation_id = decoded[len(_FILTER_CURSOR_PREFIX):].partition(":")
        if not separator:
            raise ValueError("after is not a valid operational action cursor.")
        _validate_candidate_run_ref(digest)
        if digest != candidate_run_ref:
            raise ValueError("after cursor does not match the candidate-run filter.")
        if encode_action_cursor(operation_id, candidate_run_ref=digest) != value:
            raise ValueError("after is not a valid operational action cursor.")
        return operation_id

    if candidate_run_ref is not None or status is not None or action_family is not None:
        raise ValueError("after cursor is not bound to the candidate action filters.")
    if encode_action_cursor(decoded) != value:
        raise ValueError("after is not a valid operational action cursor.")
    return decoded
