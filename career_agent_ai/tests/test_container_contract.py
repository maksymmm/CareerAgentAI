import os
import stat
from pathlib import Path

import pytest

from career_agent_ai.application.runtime.container_entrypoint import (
    database_filesystem_path,
    secure_storage,
    validate_environment,
)


ROOT = Path(__file__).resolve().parents[2]


def test_candidate_container_runs_non_root_with_safe_defaults():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert (
        "FROM python:3.12.15-slim-bookworm@sha256:"
        "34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258"
        in dockerfile
    )
    assert "USER 10001:10001" in dockerfile
    assert "CAREER_AGENT_DB_PATH=/var/lib/careeragent/candidate.sqlite" in dockerfile
    assert "CAREER_AGENT_ALLOW_NETWORK_PROVIDERS=false" in dockerfile
    assert "CAREER_AGENT_ALLOW_CONSEQUENTIAL_ACTIONS=false" in dockerfile
    assert "CAREER_AGENT_CANDIDATE_SANDBOX=true" in dockerfile
    assert "CAREER_AGENT_CANDIDATE_BEARER_TOKEN" not in dockerfile
    assert "CAREER_AGENT_CANDIDATE_USER_ID" not in dockerfile
    assert "CAREER_AGENT_CANDIDATE_PORT" in dockerfile
    assert ".get('CAREER_AGENT_CANDIDATE_HOST', '0.0.0.0').strip()" in dockerfile
    assert ".get('CAREER_AGENT_CANDIDATE_PORT', '8080').strip()" in dockerfile
    assert "build_opener(urllib.request.ProxyHandler({})).open" in dockerfile
    assert "probe='127.0.0.1' if bind == '0.0.0.0'" in dockerfile
    assert "else '::1' if bind == '::' else bind" in dockerfile
    assert "http://{host}:{port}/healthz" in dockerfile
    entrypoint = (ROOT / "container-entrypoint.sh").read_text(encoding="utf-8")
    assert "umask 077" in entrypoint
    assert "exec python -m career_agent_ai.application.runtime.container_entrypoint" in entrypoint


def _valid_environment(database_path: Path) -> dict[str, str]:
    return {
        "CAREER_AGENT_ENV": "production",
        "CAREER_AGENT_DB_PATH": str(database_path),
        "CAREER_AGENT_CANDIDATE_SANDBOX": "true",
        "CAREER_AGENT_CANDIDATE_ID_CUTOVER": "drain-and-replace-scoped-v2",
        "CAREER_AGENT_ALLOW_NETWORK_PROVIDERS": "false",
        "CAREER_AGENT_ALLOW_CONSEQUENTIAL_ACTIONS": "false",
        "CAREER_AGENT_CANDIDATE_HOST": "127.0.0.1",
        "CAREER_AGENT_CANDIDATE_PORT": "8080",
        "CAREER_AGENT_CANDIDATE_BEARER_TOKEN": "a" * 32,
        "CAREER_AGENT_CANDIDATE_USER_ID": "candidate-1",
    }


@pytest.mark.parametrize("key", ["CAREER_AGENT_CANDIDATE_HOST", "CAREER_AGENT_CANDIDATE_PORT"])
def test_container_rejects_explicit_empty_listener_setting_before_chmod(tmp_path, key):
    database = tmp_path / "data" / "candidate.sqlite"
    database.parent.mkdir(mode=0o755)
    environ = _valid_environment(database)
    environ[key] = ""

    with pytest.raises(ValueError):
        validate_environment(environ)

    assert stat.S_IMODE(database.parent.stat().st_mode) == 0o755


def test_container_validates_all_policy_before_chmod(tmp_path):
    database = tmp_path / "data" / "candidate.sqlite"
    database.parent.mkdir(mode=0o755)
    environ = _valid_environment(database)
    environ["CAREER_AGENT_ALLOW_NETWORK_PROVIDERS"] = "invalid"

    with pytest.raises(ValueError):
        validate_environment(environ)

    assert stat.S_IMODE(database.parent.stat().st_mode) == 0o755


@pytest.mark.parametrize("target_kind", ["directory", "symlink", "fifo"])
def test_container_rejects_non_regular_database_without_mutation(tmp_path, target_kind):
    data = tmp_path / "data"
    data.mkdir(mode=0o755)
    database = data / "candidate.sqlite"
    if target_kind == "directory":
        database.mkdir(mode=0o755)
    elif target_kind == "symlink":
        destination = tmp_path / "real.sqlite"
        destination.write_bytes(b"")
        database.symlink_to(destination)
    else:
        os.mkfifo(database)

    expected = "ancestors" if target_kind == "symlink" else "regular file"
    with pytest.raises(ValueError, match=expected):
        secure_storage(str(database))

    assert stat.S_IMODE(data.stat().st_mode) == 0o755


def test_container_rejects_hard_linked_database_without_mutation(tmp_path):
    data = tmp_path / "data"
    data.mkdir(mode=0o755)
    outside = tmp_path / "outside.sqlite"
    outside.write_bytes(b"")
    outside.chmod(0o644)
    database = data / "candidate.sqlite"
    database.hardlink_to(outside)

    with pytest.raises(ValueError, match="hard-linked"):
        secure_storage(str(database))

    assert stat.S_IMODE(data.stat().st_mode) == 0o755
    assert stat.S_IMODE(outside.stat().st_mode) == 0o644


def test_container_preserves_non_utf8_uri_path_bytes(tmp_path):
    data = os.fsencode(tmp_path) + b"/data"
    os.mkdir(data, 0o755)
    database = data + b"/candidate-\xff.sqlite"
    with open(database, "wb"):
        pass
    uri = "file:" + os.fsdecode(data) + "/candidate-%FF.sqlite"

    assert database_filesystem_path(uri) == database
    secure_storage(uri)

    assert stat.S_IMODE(os.stat(data).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(database).st_mode) == 0o600


def test_container_rejects_remote_sqlite_uri_authority():
    with pytest.raises(ValueError, match="authority"):
        database_filesystem_path("file://remote/var/lib/candidate.sqlite?mode=rwc")


def test_container_preserves_literal_uri_control_bytes(tmp_path):
    database = os.fsencode(tmp_path) + b"/candidate\ndata.sqlite"
    uri = "file:" + os.fsdecode(database) + "?mode=rwc"

    assert database_filesystem_path(uri) == database


def test_container_rejects_symlink_in_database_ancestor(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    nested = outside / "nested"
    nested.mkdir(mode=0o755)
    mount = tmp_path / "mount"
    mount.mkdir(mode=0o755)
    (mount / "link").symlink_to(outside, target_is_directory=True)
    database = mount / "link" / "nested" / "candidate.sqlite"

    with pytest.raises(ValueError, match="ancestors"):
        secure_storage(str(database))

    assert stat.S_IMODE(nested.stat().st_mode) == 0o755


def test_container_secures_storage_through_open_descriptors(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir(mode=0o755)
    database = data / "candidate.sqlite"
    database.write_bytes(b"")
    calls = []
    real_open = os.open

    def recording_open(path, flags, *args, **kwargs):
        calls.append((path, flags, kwargs.get("dir_fd")))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", recording_open)
    secure_storage(str(database))

    assert any(flags & os.O_NOFOLLOW and dir_fd is not None for _, flags, dir_fd in calls)
    assert any(flags & os.O_PATH and dir_fd is not None for _, flags, dir_fd in calls)
    assert any(
        flags & os.O_NONBLOCK and not flags & os.O_PATH and dir_fd is not None
        for _, flags, dir_fd in calls
    )


def test_ci_smokes_read_only_candidate_container():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "docker build --tag careeragentai:candidate ." in workflow
    assert "--read-only" in workflow
    assert "--tmpfs /tmp:rw,noexec,nosuid,size=16m" in workflow
    assert "type=volume" in workflow
    assert "docker exec careeragent-candidate-ci id -u" in workflow
    assert "stat -c %a /var/lib/careeragent" in workflow
    assert "stat -c %a /var/lib/careeragent/candidate.sqlite" in workflow
    assert '"/healthz"' in workflow
    assert '"/readyz"' in workflow


def test_ci_generates_and_verifies_pinned_release_evidence():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in workflow
    assert "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97" in workflow
    assert "anchore/sbom-action@e22c389904149dbc22b58101806040fa8d37a610" in workflow
    assert "anchore/scan-action@e1165082ffb1fe366ebaf02d8526e7c4989ea9d2" in workflow
    assert "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9" in workflow
    assert "candidate-image-digest.txt" in workflow
    assert "candidate-sbom.spdx.json" in workflow
    assert "candidate-vulnerabilities.json" in workflow
    assert "source-commit.txt" in workflow
    assert "release-artifacts.sha256" in workflow
    assert "sha256sum --check release-artifacts.sha256" in workflow
    assert "severity-cutoff: critical" in workflow
    assert "only-fixed: true" in workflow
    assert "github.event.pull_request.head.sha || github.sha" in workflow
    assert workflow.index("Smoke candidate service container") < workflow.index(
        "Upload immutable release evidence"
    )
