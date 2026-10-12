from pathlib import Path


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
    assert "probe='127.0.0.1' if bind == '0.0.0.0'" in dockerfile
    assert "else '::1' if bind == '::' else bind" in dockerfile
    assert "http://{host}:{port}/healthz" in dockerfile
    entrypoint = (ROOT / "container-entrypoint.sh").read_text(encoding="utf-8")
    assert "umask 077" in entrypoint
    assert "print(sys.argv[1].strip())" in entrypoint
    assert 'export CAREER_AGENT_DB_PATH="$normalized_db"' in entrypoint
    assert 'unquote(urlsplit(value).path) if value.startswith("file:") else value' in entrypoint
    assert 'if "\\x00" in path:' in entrypoint
    assert "CAREER_AGENT_DB_PATH must not contain NUL bytes" in entrypoint
    assert "print(path)" in entrypoint
    assert "CAREER_AGENT_DB_PATH must resolve to an absolute filesystem path" in entrypoint
    assert "if not raw.isascii() or not raw.isdecimal():" in entrypoint
    assert "if not 1 <= port <= 65535:" in entrypoint
    assert 'export CAREER_AGENT_CANDIDATE_PORT="$candidate_port"' in entrypoint
    assert "CAREER_AGENT_DB_PATH must use a writable mounted parent directory" in entrypoint
    assert 'export CAREER_AGENT_CANDIDATE_HOST="$normalized_host"' in entrypoint
    assert 'chmod 0700 "$data_dir"' in entrypoint
    assert 'chmod 0600 "$db_path"' in entrypoint
    assert "exec python -m career_agent_ai.application.runtime.candidate_service" in entrypoint


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
