"""Candidate readiness requires a complete durable career-loop schema."""

import sqlite3

import pytest

from career_agent_ai.application.runtime.candidate_service import _validate_loop_schema


REQUIRED = (
    "run_id",
    "owner_user_id",
    "phase",
    "payload_json",
    "updated_at",
    "version",
    "execution_claim_owner",
    "execution_claim_expires_at",
)


@pytest.mark.parametrize("missing", REQUIRED)
def test_incomplete_candidate_loop_schema_is_rejected(missing: str) -> None:
    connection = sqlite3.connect(":memory:")
    try:
        columns = ", ".join(f"{name} TEXT" for name in REQUIRED if name != missing)
        connection.execute(f"CREATE TABLE autonomous_career_loops ({columns})")
        with pytest.raises(RuntimeError, match="candidate schema is unavailable"):
            _validate_loop_schema(connection)
    finally:
        connection.close()


def test_complete_candidate_loop_schema_is_accepted() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        columns = ", ".join(f"{name} TEXT" for name in REQUIRED)
        connection.execute(f"CREATE TABLE autonomous_career_loops ({columns})")
        _validate_loop_schema(connection)
    finally:
        connection.close()
