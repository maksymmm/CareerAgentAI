"""Provider timestamp isolation with valid neighboring candidate opportunities."""

import pytest

from career_agent_ai.application.search.providers.arbeitnow_provider import ArbeitnowProvider


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("created_at", True),
        ("created_at", False),
        ("created_at", []),
        ("created_at", {"epoch": 1}),
        ("created_at", "1700000000"),
        ("created_at", float("inf")),
        ("created_at", float("nan")),
        ("created_at", 10**100),
        ("created_at_iso", []),
        ("created_at_iso", {"date": "2026-01-01"}),
        ("created_at_iso", ""),
        ("created_at_iso", "not-a-date"),
    ],
)
def test_malformed_timestamp_isolated_without_losing_valid_sibling(
    monkeypatch, field, invalid
):
    valid = {
        "slug": "valid", "title": "Python Engineer", "company_name": "Example",
        "location": "Berlin", "description": "Python", "created_at": 1700000000,
    }
    malformed = {**valid, "slug": "malformed", field: invalid}
    # For ISO-only malformed cases, keep the otherwise valid epoch so that
    # fallback cannot mask a malformed explicitly supplied ISO value.
    payload = {"data": [malformed, valid], "links": {"next": None}}

    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, size=-1):
            import json
            return json.dumps(payload).encode("utf-8")[:size if size >= 0 else None]

    monkeypatch.setattr(
        "career_agent_ai.application.search.providers.arbeitnow_provider.urlopen",
        lambda request, timeout: Response(),
    )
    jobs = ArbeitnowProvider(max_pages=1).search("python")
    assert len(jobs) == 1
    assert jobs[0].job_id == "arbeitnow:valid"


def test_valid_iso_and_epoch_timestamp_are_preserved():
    from datetime import UTC, datetime
    iso = ArbeitnowProvider._extract_created_at(
        {"created_at_iso": "2026-08-20T10:00:00Z"}
    )
    epoch = ArbeitnowProvider._extract_created_at({"created_at": 1700000000})
    assert iso == datetime(2026, 8, 20, 10, tzinfo=UTC)
    assert epoch == datetime.fromtimestamp(1700000000, tz=UTC)
