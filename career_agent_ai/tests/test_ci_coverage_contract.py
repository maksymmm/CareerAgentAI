from pathlib import Path


def test_ci_runs_independent_coverage_gate_after_the_full_pytest_suite():
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    test_job = workflow.split("  release-evidence:", 1)[0]
    pytest_command = "python -m pytest"
    coverage_command = "python -m coverage report --fail-under=90"

    assert pytest_command in test_job
    assert coverage_command in test_job
    assert test_job.index(pytest_command) < test_job.index(coverage_command)
