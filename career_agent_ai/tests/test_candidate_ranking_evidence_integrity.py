"""Reject malformed adaptive ranking metadata before durable candidate selection."""

from types import SimpleNamespace

import pytest

from career_agent_ai.application.career.autonomous_career_loop import AutonomousCareerLoop


def _selection(**overrides):
    fields = dict(relevance_score=1.0, outcome_adjustment=0.0, score=1.0,
                  evidence_count=0, reason="No prior evidence.")
    fields.update(overrides)
    return SimpleNamespace(**fields)


@pytest.mark.parametrize("field", ["relevance_score", "outcome_adjustment", "score"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), True, "1.0", None])
def test_ranker_rejects_invalid_score_scalars(field, bad):
    with pytest.raises(ValueError, match=field):
        AutonomousCareerLoop._validate_ranker_selection(_selection(**{field: bad}))


@pytest.mark.parametrize("bad", [-1, True, 1.5, "2", None])
def test_ranker_rejects_invalid_evidence_count(bad):
    with pytest.raises(ValueError, match="evidence_count"):
        AutonomousCareerLoop._validate_ranker_selection(_selection(evidence_count=bad))


@pytest.mark.parametrize("bad", ["", "   ", "bad\nreason", "bad\x00reason", "\ud800", "x" * 1001, None])
def test_ranker_rejects_invalid_reason(bad):
    with pytest.raises(ValueError, match="reason"):
        AutonomousCareerLoop._validate_ranker_selection(_selection(reason=bad))


def test_ranker_accepts_valid_bounded_evidence():
    AutonomousCareerLoop._validate_ranker_selection(_selection())
