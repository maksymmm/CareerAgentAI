from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def test_ci_has_independent_coverage_threshold_step() -> None:
    """Keep the CI coverage gate independent from pytest-cov's exit status."""
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "python -m pytest" in workflow
    assert "--cov-fail-under=90" in workflow
    assert "python -m coverage report --fail-under=90" in workflow


def test_coverage_cli_fails_when_measured_coverage_is_below_threshold(
    tmp_path: Path,
) -> None:
    """Prove the standalone coverage command rejects a below-threshold report."""
    source = tmp_path / "coverage_fixture.py"
    source.write_text(
        "def covered():\n"
        "    return 1\n"
        "\n"
        "def uncovered():\n"
        "    return 2\n"
        "\n"
        "covered()\n",
        encoding="utf-8",
    )
    data_file = tmp_path / ".coverage-gate"
    environment = os.environ.copy()
    environment["COVERAGE_FILE"] = str(data_file)

    measured = subprocess.run(
        [
            sys.executable,
            "-m",
            "coverage",
            "run",
            "--rcfile=/dev/null",
            str(source),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert measured.returncode == 0, measured.stdout + measured.stderr

    report = subprocess.run(
        [
            sys.executable,
            "-m",
            "coverage",
            "report",
            "--rcfile=/dev/null",
            "--fail-under=90",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert report.returncode != 0
    assert "FAIL Required test coverage" in report.stdout
