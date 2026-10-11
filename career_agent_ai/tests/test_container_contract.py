from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_candidate_container_runs_non_root_with_safe_defaults():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.12.15-slim-bookworm" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "CAREER_AGENT_DB_PATH=/var/lib/careeragent/candidate.sqlite" in dockerfile
    assert "CAREER_AGENT_ALLOW_NETWORK_PROVIDERS=false" in dockerfile
    assert "CAREER_AGENT_ALLOW_CONSEQUENTIAL_ACTIONS=false" in dockerfile
    assert "CAREER_AGENT_CANDIDATE_SANDBOX=true" in dockerfile
    assert "CAREER_AGENT_CANDIDATE_BEARER_TOKEN" not in dockerfile
    assert "CAREER_AGENT_CANDIDATE_USER_ID" not in dockerfile
    assert "http://127.0.0.1:8080/healthz" in dockerfile


def test_ci_smokes_read_only_candidate_container():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "docker build --tag careeragentai:candidate ." in workflow
    assert "--read-only" in workflow
    assert "--tmpfs /tmp:rw,noexec,nosuid,size=16m" in workflow
    assert "type=volume" in workflow
    assert "docker exec careeragent-candidate-ci id -u" in workflow
    assert '"/healthz"' in workflow
    assert '"/readyz"' in workflow
