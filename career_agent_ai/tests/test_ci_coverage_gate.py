from pathlib import Path


def test_ci_runs_independent_coverage_cli_after_pytest_even_on_failure():
    """Keep the CLI coverage threshold independent from pytest's exit status."""
    repository_root = Path(__file__).resolve().parents[2]
    workflow = (repository_root / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    pytest_step = "      - name: Run test suite\n        id: pytest\n"
    coverage_step = (
        "      - name: Run independent coverage threshold\n"
        "        if: always() && !cancelled() && steps.pytest.outcome != 'skipped'\n"
    )
    assert workflow.count(pytest_step) == 1
    assert workflow.count(coverage_step) == 1
    assert workflow.index(coverage_step) > workflow.index(pytest_step)
    assert "python -m pytest --cov=career_agent_ai" in workflow
    assert "--cov-fail-under=90" in workflow
    assert "python -m coverage report --precision=2 --fail-under=90" in workflow
