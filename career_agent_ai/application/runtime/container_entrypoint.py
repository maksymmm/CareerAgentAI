"""Fail-closed container startup before repairing mounted SQLite permissions."""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Mapping
from urllib.parse import unquote_to_bytes, urlsplit

from .candidate_service import CandidateServiceConfig
from .composition import validate_candidate_sandbox_environment


def validate_environment(environ: Mapping[str, str]) -> tuple[CandidateServiceConfig, str]:
    """Validate every service and sandbox setting without touching the filesystem."""
    service = CandidateServiceConfig.from_env(environ)
    if service.port == 0:
        raise ValueError("CAREER_AGENT_CANDIDATE_PORT must be between 1 and 65535.")
    runtime = validate_candidate_sandbox_environment(environ)
    return service, runtime.database_path


def database_filesystem_path(database_path: str) -> bytes:
    """Return the byte-exact local path represented by a validated SQLite value."""
    if database_path.startswith("file:"):
        path = unquote_to_bytes(urlsplit(database_path).path)
    else:
        path = os.fsencode(database_path)
    if b"\0" in path:
        raise ValueError("CAREER_AGENT_DB_PATH must not contain NUL bytes.")
    if not os.path.isabs(path):
        raise ValueError("CAREER_AGENT_DB_PATH must resolve to an absolute filesystem path.")
    return path


def secure_storage(database_path: str) -> None:
    """Repair only a regular database file in a real mounted parent directory."""
    path = database_filesystem_path(database_path)
    parent = os.path.dirname(path)
    if parent in {b"", os.fsencode(os.path.sep)}:
        raise ValueError("CAREER_AGENT_DB_PATH must use a writable mounted parent directory.")
    parent_stat = os.lstat(parent)
    if stat.S_ISLNK(parent_stat.st_mode) or not stat.S_ISDIR(parent_stat.st_mode):
        raise ValueError("CAREER_AGENT_DB_PATH parent must be a non-symlink directory.")
    try:
        target_stat = os.lstat(path)
    except FileNotFoundError:
        target_stat = None
    if target_stat is not None and not stat.S_ISREG(target_stat.st_mode):
        raise ValueError("CAREER_AGENT_DB_PATH must be a regular file.")
    os.chmod(parent, 0o700, follow_symlinks=False)
    if target_stat is not None:
        os.chmod(path, 0o600, follow_symlinks=False)


def main() -> None:
    """Validate first, secure storage second, then replace PID 1 with the service."""
    service, database_path = validate_environment(os.environ)
    secure_storage(database_path)
    os.environ["CAREER_AGENT_CANDIDATE_HOST"] = service.host
    os.environ["CAREER_AGENT_CANDIDATE_PORT"] = str(service.port)
    os.environ["CAREER_AGENT_DB_PATH"] = database_path
    os.execvpe(
        sys.executable,
        [sys.executable, "-m", "career_agent_ai.application.runtime.candidate_service"],
        os.environ,
    )


if __name__ == "__main__":
    main()
