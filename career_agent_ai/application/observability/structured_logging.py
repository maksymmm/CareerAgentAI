"""Structured JSON logging with correlation IDs and secret redaction."""

from __future__ import annotations

import json
import logging
import math
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Iterator, Mapping
from uuid import uuid4

_correlation_id: ContextVar[str | None] = ContextVar("career_agent_correlation_id", default=None)
_SENSITIVE_TOKENS = ("password", "secret", "token", "api_key", "apikey", "authorization", "cookie")
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_AUTHORIZATION_HEADER_PATTERN = re.compile(
    r"(?im)(\bauthorization\s*:\s*)[^\r\n]+"
)
_SENSITIVE_QUOTED_ASSIGNMENT_PATTERN = re.compile(
    r"""(?ix)
    (
        ["']?
        [a-z0-9_-]*
        (?:password|secret|token|api[_-]?key|apikey|authorization|cookie)
        [a-z0-9_-]*
        ["']?
        \s*[:=]\s*
    )
    (["'])
    .*?
    \2
    """
)
_SENSITIVE_UNQUOTED_ASSIGNMENT_PATTERN = re.compile(
    r"""(?ix)
    (
        ["']?
        [a-z0-9_-]*
        (?:password|secret|token|api[_-]?key|apikey|authorization|cookie)
        [a-z0-9_-]*
        ["']?
        \s*[:=]\s*
    )
    [^\s,;}]+
    """
)


def current_correlation_id() -> str | None:
    """Return the active correlation ID, if one has been established."""
    return _correlation_id.get()


@contextmanager
def correlation_scope(correlation_id: str | None = None) -> Iterator[str]:
    """Bind a validated correlation ID for all logs emitted in the scope."""
    identifier = uuid4().hex if correlation_id is None else correlation_id
    if not isinstance(identifier, str):
        raise TypeError("correlation_id must be text.")
    identifier = identifier.strip()
    if not identifier or len(identifier) > 200:
        raise ValueError("correlation_id must be a non-empty string of at most 200 characters.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in identifier):
        raise ValueError("correlation_id contains forbidden control characters.")
    token = _correlation_id.set(identifier)
    try:
        yield identifier
    finally:
        _correlation_id.reset(token)


class JsonLogFormatter(logging.Formatter):
    """Render one log record as a compact, deterministic JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
            "correlation_id": current_correlation_id(),
        }
        event = getattr(record, "event", None)
        if event is not None:
            payload["event"] = str(event)[:200]
        fields = getattr(record, "fields", None)
        if isinstance(fields, Mapping):
            payload["fields"] = redact_mapping(fields)
        if record.exc_info:
            payload["exception_type"] = (
                record.exc_info[0].__name__ if record.exc_info[0] is not None else "Exception"
            )
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )


def configure_structured_logging(level: int = logging.INFO) -> None:
    """Configure the root logger once with the production JSON formatter."""
    if not isinstance(level, int):
        raise TypeError("level must be an integer logging level.")
    root = logging.getLogger()
    root.setLevel(level)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    root.handlers.clear()
    root.addHandler(handler)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    message: str,
    **fields: Any,
) -> None:
    """Emit a structured application event with redacted contextual fields."""
    if not isinstance(logger, logging.Logger):
        raise TypeError("logger must be a logging.Logger.")
    if not isinstance(event, str) or not event.strip():
        raise ValueError("event must not be empty.")
    logger.log(
        level,
        message,
        extra={"event": event.strip(), "fields": redact_mapping(fields)},
    )


def redact_text(value: str) -> str:
    """Redact common credential patterns from bounded diagnostic text."""
    if not isinstance(value, str):
        raise TypeError("diagnostic text must be text.")
    sanitized = value.replace("\\r", "\\\\r").replace("\\n", "\\\\n")
    sanitized = _AUTHORIZATION_HEADER_PATTERN.sub(
        lambda match: f"{match.group(1)}[REDACTED]",
        sanitized,
    )
    sanitized = _BEARER_PATTERN.sub("Bearer [REDACTED]", sanitized)
    sanitized = _SENSITIVE_QUOTED_ASSIGNMENT_PATTERN.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]{match.group(2)}",
        sanitized,
    )
    sanitized = _SENSITIVE_UNQUOTED_ASSIGNMENT_PATTERN.sub(
        lambda match: f"{match.group(1)}[REDACTED]",
        sanitized,
    )
    return sanitized[:2_000]


def redact_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe mapping with likely credential values redacted."""
    result: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        lowered = key.casefold()
        if any(token in lowered for token in _SENSITIVE_TOKENS):
            result[key] = "[REDACTED]"
        else:
            result[key] = _safe_value(raw_value)
    return result


def _safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else "[NON_FINITE]"
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return redact_mapping(value)
    if isinstance(value, (tuple, list)):
        return [_safe_value(item) for item in value[:100]]
    return redact_text(str(value))
