from pathlib import Path


def test_ci_enforces_independent_coverage_after_pytest_even_on_failure():
    workflow_path = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
    workflow = workflow_path.read_text(encoding="utf-8")

    pytest_step = workflow.index("- name: Run test suite")
    independent_step = workflow.index("- name: Enforce independent coverage threshold")
    assert pytest_step < independent_step
    assert "id: pytest" in workflow[pytest_step:independent_step]
    assert (
        "if: always() && !cancelled() && steps.pytest.outcome != 'skipped'"
        in workflow[independent_step:]
    )
    assert (
        "python -m coverage report --precision=2 --fail-under=90"
        in workflow[independent_step:]
    )


def test_ci_pytest_coverage_gate_remains_at_least_ninety_percent():
    workflow_path = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
    workflow = workflow_path.read_text(encoding="utf-8")
    assert "--cov=career_agent_ai" in workflow
    assert "--cov-fail-under=90" in workflow
