"""Validated runtime configuration sourced from environment variables."""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping


class RuntimeEnvironment(str, Enum):
    """Supported deployment environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


@dataclass(frozen=True)
class RuntimeConfig:
    """Validated application configuration without embedded credentials."""

    environment: RuntimeEnvironment
    database_path: str
    log_level: int
    allow_network_providers: bool
    allow_consequential_actions: bool

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "RuntimeConfig":
        """Build a strict configuration from CAREER_AGENT_* environment variables."""
        source = os.environ if environ is None else environ
        raw_environment = source.get("CAREER_AGENT_ENV", "development").strip().lower()
        try:
            environment = RuntimeEnvironment(raw_environment)
        except ValueError as exc:
            raise ValueError("CAREER_AGENT_ENV must be development, test, or production.") from exc

        database_path = source.get("CAREER_AGENT_DB_PATH", ":memory:").strip()
        if not database_path:
            raise ValueError("CAREER_AGENT_DB_PATH must not be empty.")
        if "\x00" in database_path:
            raise ValueError("CAREER_AGENT_DB_PATH contains a forbidden NUL byte.")
        raw_level = source.get("CAREER_AGENT_LOG_LEVEL", "INFO").strip().upper()
        level = logging.getLevelName(raw_level)
        if not isinstance(level, int):
            raise ValueError("CAREER_AGENT_LOG_LEVEL is not a recognized logging level.")

        allow_network = cls._parse_bool(
            source.get("CAREER_AGENT_ALLOW_NETWORK_PROVIDERS", "false"),
            "CAREER_AGENT_ALLOW_NETWORK_PROVIDERS",
        )
        allow_actions = cls._parse_bool(
            source.get("CAREER_AGENT_ALLOW_CONSEQUENTIAL_ACTIONS", "false"),
            "CAREER_AGENT_ALLOW_CONSEQUENTIAL_ACTIONS",
        )
        if environment == RuntimeEnvironment.TEST and (allow_network or allow_actions):
            raise ValueError("Test environment cannot enable external network or consequential actions.")

        if database_path != ":memory:" and not database_path.startswith("file:"):
            path = Path(database_path).expanduser()
            if path.exists() and path.is_dir():
                raise ValueError("CAREER_AGENT_DB_PATH must reference a file, not a directory.")
            database_path = str(path)

        if (
            environment == RuntimeEnvironment.PRODUCTION
            and cls._is_sqlite_memory_path(database_path)
        ):
            raise ValueError("Production requires a durable CAREER_AGENT_DB_PATH.")

        return cls(
            environment=environment,
            database_path=database_path,
            log_level=level,
            allow_network_providers=allow_network,
            allow_consequential_actions=allow_actions,
        )

    @staticmethod
    def _is_sqlite_memory_path(value: str) -> bool:
        """Return whether SQLite would treat the configured path as in-memory."""
        if value == ":memory:":
            return True
        if not value.startswith("file:"):
            return False
        try:
            with closing(sqlite3.connect(value, uri=True)) as connection:
                return not connection.execute("PRAGMA database_list").fetchone()[2]
        except sqlite3.Error as exc:
            raise ValueError("CAREER_AGENT_DB_PATH is not an openable SQLite URI.") from exc

    @staticmethod
    def _parse_bool(value: str, field: str) -> bool:
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"{field} must be a boolean value.")
