from pathlib import Path


def test_ci_enforces_independent_coverage_after_pytest_even_when_pytest_fails():
    workflow_path = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
    )
    workflow = workflow_path.read_text(encoding="utf-8")

    pytest_step = "      - name: Run test suite\n        id: pytest\n"
    coverage_step = "      - name: Enforce independent coverage threshold\n"
    assert pytest_step in workflow
    assert coverage_step in workflow
    assert workflow.index(pytest_step) < workflow.index(coverage_step)

    gate = workflow[workflow.index(coverage_step):]
    assert (
        "if: ${{ always() && !cancelled() && steps.pytest.outcome != 'skipped' }}"
        in gate
    )
    assert "python -m coverage report --precision=2 --fail-under=90" in gate
    assert "--cov-fail-under=90" in workflow[
        workflow.index(pytest_step):workflow.index(coverage_step)
    ]
