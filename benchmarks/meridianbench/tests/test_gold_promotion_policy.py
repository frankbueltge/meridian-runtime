"""task-packets/N1-T02.yaml AT4's promotion half: with no thresholds set, the
gold-classification decision is ``hold``, every check says WHY, and the
existing MB-NUM/MB-CIT decision is untouched by any of it.
"""

from __future__ import annotations

from benchmarks.meridianbench.promotion import (
    EvaluationProfile,
    MetricsReport,
    decide_gold_classification_promotion,
    decide_promotion,
)
from benchmarks.meridianbench.targets import (
    FALSE_SUPPORT_ON_MB_CLS_TARGET,
    GOLD_CLASSIFICATION_KAPPA_TARGET,
    GOLD_CLASSIFICATION_MACRO_F1_TARGET,
    NO_THRESHOLD_SET_REASON,
)

PROFILE = EvaluationProfile(
    system_id="synthetic-scripted-classifier",
    prompt_version="n/a",
    fixture_set_id="mb-cls-v1-synthetic@sha256:" + "0" * 64,
)


def test_the_three_gold_targets_are_unset_until_an_encounter_sets_them() -> None:
    # This is an assertion about governance, not about arithmetic: the owner
    # decided on 2026-08-01 that an encounter with another practice sets these
    # numbers. A builder filling one in would be setting the practice's own
    # standard for "better", which is the one thing the ordering forbids.
    assert GOLD_CLASSIFICATION_KAPPA_TARGET is None
    assert GOLD_CLASSIFICATION_MACRO_F1_TARGET is None
    assert FALSE_SUPPORT_ON_MB_CLS_TARGET is None


def test_at4_unset_thresholds_hold_and_say_why_even_with_excellent_metrics() -> None:
    # Deliberately near-perfect measurements. They must still not promote:
    # nobody has said what good means, so there is nothing to have met.
    metrics = MetricsReport(
        gold_classification_kappa=0.99,
        gold_classification_macro_f1=0.99,
        false_support_rate_mb_cls=0.0,
    )
    decision = decide_gold_classification_promotion(metrics, PROFILE)

    assert decision.outcome == "hold"
    assert len(decision.target_checks) == 3
    for check in decision.target_checks:
        assert check.passed is False
        assert check.target_value is None
        assert check.reason == NO_THRESHOLD_SET_REASON


def test_an_unmeasured_metric_reads_differently_from_an_unset_threshold() -> None:
    # AGENTS.md's prohibited shortcuts include "collapsing unknown, not_found,
    # contradicted, and failed into one generic error". "Nobody set a bar" and
    # "we did not measure" are different states and must stay legible as such.
    decision = decide_gold_classification_promotion(MetricsReport(), PROFILE)
    reasons = {check.reason for check in decision.target_checks}
    assert reasons == {NO_THRESHOLD_SET_REASON}
    # With no threshold set, the threshold's absence is the binding reason —
    # it is checked first, so an unmeasured metric never masks it.
    assert all(check.metric_value is None for check in decision.target_checks)


def test_the_decision_carries_the_frozen_set_it_was_computed_against() -> None:
    decision = decide_gold_classification_promotion(MetricsReport(), PROFILE)
    assert decision.evaluation_profile.fixture_set_id.startswith("mb-cls-v1-synthetic@sha256:")


def test_deciding_twice_returns_an_equal_decision() -> None:
    metrics = MetricsReport(gold_classification_kappa=0.5)
    assert decide_gold_classification_promotion(
        metrics, PROFILE
    ) == decide_gold_classification_promotion(metrics, PROFILE)


def test_the_existing_mb_num_mb_cit_decision_is_not_vetoed_by_the_unset_gold_targets() -> None:
    # The reason decide_gold_classification_promotion is a separate function.
    # Folding the three unset targets into decide_promotion would have made
    # `promote` unreachable for a fully measured, fully passing MB-NUM/MB-CIT
    # run — an unrelated, unset threshold silently vetoing a real result.
    passing = MetricsReport(
        numeric_accuracy=0.99,
        valid_anchor_resolution_rate=0.99,
        false_support_rate=0.0,
    )
    decision = decide_promotion(passing, PROFILE)
    assert decision.outcome == "promote"
    assert len(decision.target_checks) == 3
    assert {check.name for check in decision.target_checks} == {
        "numeric_verification_accuracy",
        "valid_citation_anchor_resolution",
        "false_support_on_mb_cit",
    }
