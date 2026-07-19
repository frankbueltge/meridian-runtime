"""MB-NUM acceptance tests (task-packets/E4-T07.yaml): the fixture suite
scored by ``mrr.services.verifier.numeric.recompute_numeric_claim`` yields a
numeric-accuracy metric; an all-correct system meets the >= 0.95 target and
an all-wrong system does not; the non-agent baseline never matches;
scoring is deterministic.
"""

from __future__ import annotations

from mrr.services.verifier.numeric import recompute_numeric_claim

from benchmarks.meridianbench.report import evaluate_numeric_system
from benchmarks.meridianbench.suites.mb_num import MB_NUM_CASES, score_numeric_suite
from benchmarks.meridianbench.systems.baselines import abstaining_numeric_baseline
from benchmarks.meridianbench.systems.scripted_agent import make_numeric_agent
from benchmarks.meridianbench.targets import NUMERIC_VERIFICATION_ACCURACY_TARGET

# ---------------------------------------------------------------------------
# Fixture self-consistency: every declared correct_value really is what the
# reused verifier computes from that case's own input.
# ---------------------------------------------------------------------------


def test_every_fixture_correct_value_matches_the_reused_verifier() -> None:
    for case in MB_NUM_CASES:
        recomputation = recompute_numeric_claim(
            operation=case.input.operation,
            claimed_value=case.expected.correct_value,
            inputs=case.input.numeric_inputs,
            tolerance=case.input.tolerance,
        )
        assert recomputation.impossible_reason is None, (
            f"{case.case_id}: {recomputation.impossible_reason}"
        )
        assert recomputation.matches_claimed_value is True, (
            f"{case.case_id}: declared correct_value {case.expected.correct_value!r} does not "
            f"match recomputed {recomputation.recomputed_value!r}"
        )


# ---------------------------------------------------------------------------
# All-correct system meets the target; all-wrong system does not.
# ---------------------------------------------------------------------------


def test_all_correct_scripted_agent_meets_the_numeric_accuracy_target() -> None:
    agent = make_numeric_agent(MB_NUM_CASES, correct=True)

    metrics = evaluate_numeric_system(agent.system, MB_NUM_CASES)

    assert metrics.numeric_accuracy == 1.0
    assert metrics.numeric_accuracy >= NUMERIC_VERIFICATION_ACCURACY_TARGET
    assert metrics.correct_count == metrics.case_count == len(MB_NUM_CASES)


def test_all_wrong_scripted_agent_does_not_meet_the_numeric_accuracy_target() -> None:
    agent = make_numeric_agent(MB_NUM_CASES, correct=False)

    metrics = evaluate_numeric_system(agent.system, MB_NUM_CASES)

    assert metrics.numeric_accuracy == 0.0
    assert metrics.numeric_accuracy < NUMERIC_VERIFICATION_ACCURACY_TARGET
    assert metrics.correct_count == 0


# ---------------------------------------------------------------------------
# Non-agent baseline: always abstains, never scored as a match.
# ---------------------------------------------------------------------------


def test_abstaining_baseline_never_matches() -> None:
    metrics = evaluate_numeric_system(abstaining_numeric_baseline, MB_NUM_CASES)

    assert metrics.numeric_accuracy == 0.0
    assert metrics.correct_count == 0
    assert all(not scored.is_correct for scored in metrics.scored_cases)


# ---------------------------------------------------------------------------
# Deterministic scoring.
# ---------------------------------------------------------------------------


def test_scoring_the_same_suite_twice_with_the_same_system_is_identical() -> None:
    agent = make_numeric_agent(MB_NUM_CASES, correct=True)
    outputs = tuple(agent.system(case.input) for case in MB_NUM_CASES)

    first = score_numeric_suite(MB_NUM_CASES, outputs)
    second = score_numeric_suite(MB_NUM_CASES, outputs)

    assert first == second
