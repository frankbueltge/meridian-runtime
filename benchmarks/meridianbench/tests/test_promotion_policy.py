"""Promotion policy acceptance tests (task-packets/E4-T07.yaml): metrics
meeting every target promote; a report failing any one target holds; the
decision names the evaluation profile and every target's own pass/fail; a
boundary metric exactly at its target passes; the policy is pure (mutates
nothing, and is deterministic/reversible).
"""

from __future__ import annotations

import pytest

from benchmarks.meridianbench.promotion import (
    EvaluationProfile,
    MetricsReport,
    decide_promotion,
)
from benchmarks.meridianbench.targets import (
    FALSE_SUPPORT_ON_MB_CIT_TARGET,
    NUMERIC_VERIFICATION_ACCURACY_TARGET,
    VALID_CITATION_ANCHOR_RESOLUTION_TARGET,
)

_PROFILE = EvaluationProfile(
    system_id="meridianbench-scripted-agent-v1",
    prompt_version="prompt-v1",
    fixture_set_id="meridianbench-mb-num-mb-cit-v1",
)

_ALL_TARGETS_MET = MetricsReport(
    numeric_accuracy=1.0,
    valid_anchor_resolution_rate=1.0,
    false_support_rate=0.0,
)


def test_metrics_meeting_every_target_promotes() -> None:
    decision = decide_promotion(_ALL_TARGETS_MET, _PROFILE)

    assert decision.outcome == "promote"
    assert all(check.passed for check in decision.target_checks)
    assert len(decision.target_checks) == 3


@pytest.mark.parametrize(
    "metrics",
    [
        MetricsReport(
            numeric_accuracy=0.5, valid_anchor_resolution_rate=1.0, false_support_rate=0.0
        ),
        MetricsReport(
            numeric_accuracy=1.0, valid_anchor_resolution_rate=0.5, false_support_rate=0.0
        ),
        MetricsReport(
            numeric_accuracy=1.0, valid_anchor_resolution_rate=1.0, false_support_rate=0.5
        ),
        MetricsReport(
            numeric_accuracy=None, valid_anchor_resolution_rate=1.0, false_support_rate=0.0
        ),
    ],
)
def test_a_report_failing_any_one_target_holds(metrics: MetricsReport) -> None:
    decision = decide_promotion(metrics, _PROFILE)

    assert decision.outcome == "hold"
    assert any(not check.passed for check in decision.target_checks)


def test_decision_names_the_evaluation_profile_and_every_target_pass_fail() -> None:
    decision = decide_promotion(_ALL_TARGETS_MET, _PROFILE)

    assert decision.evaluation_profile == _PROFILE
    names = {check.name for check in decision.target_checks}
    assert names == {
        "numeric_verification_accuracy",
        "valid_citation_anchor_resolution",
        "false_support_on_mb_cit",
    }


# ---------------------------------------------------------------------------
# Boundary metrics exactly at target pass — section 4's own >=/<= notation.
# ---------------------------------------------------------------------------


def test_numeric_accuracy_exactly_at_target_passes() -> None:
    metrics = MetricsReport(
        numeric_accuracy=NUMERIC_VERIFICATION_ACCURACY_TARGET,
        valid_anchor_resolution_rate=1.0,
        false_support_rate=0.0,
    )
    decision = decide_promotion(metrics, _PROFILE)
    assert decision.outcome == "promote"


def test_valid_anchor_resolution_exactly_at_target_passes() -> None:
    metrics = MetricsReport(
        numeric_accuracy=1.0,
        valid_anchor_resolution_rate=VALID_CITATION_ANCHOR_RESOLUTION_TARGET,
        false_support_rate=0.0,
    )
    decision = decide_promotion(metrics, _PROFILE)
    assert decision.outcome == "promote"


def test_false_support_exactly_at_target_passes() -> None:
    metrics = MetricsReport(
        numeric_accuracy=1.0,
        valid_anchor_resolution_rate=1.0,
        false_support_rate=FALSE_SUPPORT_ON_MB_CIT_TARGET,
    )
    decision = decide_promotion(metrics, _PROFILE)
    assert decision.outcome == "promote"


def test_just_below_the_accuracy_target_holds() -> None:
    metrics = MetricsReport(
        numeric_accuracy=NUMERIC_VERIFICATION_ACCURACY_TARGET - 0.01,
        valid_anchor_resolution_rate=1.0,
        false_support_rate=0.0,
    )
    decision = decide_promotion(metrics, _PROFILE)
    assert decision.outcome == "hold"


def test_just_above_the_false_support_target_holds() -> None:
    metrics = MetricsReport(
        numeric_accuracy=1.0,
        valid_anchor_resolution_rate=1.0,
        false_support_rate=FALSE_SUPPORT_ON_MB_CIT_TARGET + 0.01,
    )
    decision = decide_promotion(metrics, _PROFILE)
    assert decision.outcome == "hold"


# ---------------------------------------------------------------------------
# Pure / deterministic / reversible: no mutation, repeatable.
# ---------------------------------------------------------------------------


def test_decide_promotion_is_pure_and_deterministic() -> None:
    first = decide_promotion(_ALL_TARGETS_MET, _PROFILE)
    second = decide_promotion(_ALL_TARGETS_MET, _PROFILE)

    assert first == second
    # The inputs themselves are frozen dataclasses — calling the function
    # cannot have mutated them either.
    assert (
        MetricsReport(
            numeric_accuracy=1.0, valid_anchor_resolution_rate=1.0, false_support_rate=0.0
        )
        == _ALL_TARGETS_MET
    )


def test_evaluation_profile_rejects_a_blank_field() -> None:
    with pytest.raises(ValueError, match="system_id"):
        EvaluationProfile(system_id="", prompt_version="v1", fixture_set_id="fixtures-v1")
