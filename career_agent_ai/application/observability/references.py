"""Stable non-reversible references for safe cross-surface correlation."""

from __future__ import annotations

from hashlib import sha256


def stable_reference(value: str) -> str:
    """Return a compact stable reference without exposing the source identifier."""
    if not isinstance(value, str) or not value:
        raise ValueError("reference source must be non-empty text.")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError("reference source must be valid Unicode text.") from exc
    return sha256(encoded).hexdigest()[:24]
