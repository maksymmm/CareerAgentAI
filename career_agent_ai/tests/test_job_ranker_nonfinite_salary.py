from math import inf, nan
from types import SimpleNamespace

import pytest

from career_agent_ai.application.jobs.job_ranker import JobRanker


@pytest.mark.parametrize("invalid", [nan, inf, -inf, "NaN", "Infinity", "-Infinity"])
def test_legacy_score_ignores_nonfinite_salary(invalid):
    job = SimpleNamespace(salary=SimpleNamespace(maximum=invalid), location=None)
    assert JobRanker._legacy_score(job) == 0.0
    assert JobRanker._salary_value(job) == 0.0


def test_nonfinite_salary_does_not_outrank_valid_salary():
    invalid = SimpleNamespace(salary=SimpleNamespace(maximum=float("nan")), location=None)
    valid = SimpleNamespace(salary=SimpleNamespace(maximum=80000), location=None)
    ranked = JobRanker().rank((invalid, valid))
    assert ranked[0].job is valid
    assert ranked[1].job is invalid
