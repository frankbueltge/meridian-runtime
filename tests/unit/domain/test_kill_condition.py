"""Unit tests for ``mrr.domain.kill_condition`` (task-packets/K1-T02.yaml,
MRR-MTH-010), run entirely DB-free and framework-free — plain string
arguments, no repository, no engine.
"""

from __future__ import annotations

import pytest
from mrr.domain.exceptions import InvalidKillDecisionError
from mrr.domain.kill_condition import assert_licenses_kill


def test_kill_branch_research_decision_passes() -> None:
    assert_licenses_kill(
        research_decision_id="urn:mrr:research-decision:X",
        decision_kind="ResearchDecision",
        decision_type="kill_branch",
    )  # must not raise


@pytest.mark.parametrize(
    "decision_type",
    [
        "continue",
        "revise",
        "narrow_scope",
        "replicate",
        "escalate_human_review",
        "stop_insufficient_evidence",
        None,
    ],
)
def test_research_decision_with_a_non_kill_branch_decision_type_raises(
    decision_type: str | None,
) -> None:
    with pytest.raises(InvalidKillDecisionError) as excinfo:
        assert_licenses_kill(
            research_decision_id="urn:mrr:research-decision:X",
            decision_kind="ResearchDecision",
            decision_type=decision_type,
        )
    assert excinfo.value.research_decision_id == "urn:mrr:research-decision:X"
    assert excinfo.value.actual_kind == "ResearchDecision"
    assert excinfo.value.actual_decision_type == decision_type


def test_a_non_research_decision_object_kind_raises_even_with_a_matching_decision_type() -> None:
    """The kind check is independent of decision_type — a wrong-kind object
    that happens to carry a decision_type == 'kill_branch' field is still
    rejected.
    """
    with pytest.raises(InvalidKillDecisionError) as excinfo:
        assert_licenses_kill(
            research_decision_id="urn:mrr:claim:X",
            decision_kind="Claim",
            decision_type="kill_branch",
        )
    assert excinfo.value.actual_kind == "Claim"


def test_invalid_kill_decision_error_carries_no_canonical_error_code() -> None:
    """This is not one of spec 08 section 3's six canonical error codes —
    it is this packet's own narrower precondition check.
    """
    err = InvalidKillDecisionError(
        "urn:mrr:research-decision:X", actual_kind="Claim", actual_decision_type=None
    )
    assert not hasattr(err, "error_code")
