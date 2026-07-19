"""The E4 exit criterion (task-packets/E4-T07.yaml): MB-CIT and MB-NUM are
evaluated against a non-agent baseline, run on the same suites as the
scripted agent-under-test, reported side by side.
"""

from __future__ import annotations

from benchmarks.meridianbench.promotion import EvaluationProfile, decide_promotion
from benchmarks.meridianbench.report import ComparisonReport, run_agent_vs_baseline
from benchmarks.meridianbench.suites.mb_cit import MB_CIT_CASES
from benchmarks.meridianbench.suites.mb_num import MB_NUM_CASES
from benchmarks.meridianbench.systems.baselines import (
    abstaining_citation_baseline,
    abstaining_numeric_baseline,
)
from benchmarks.meridianbench.systems.scripted_agent import make_citation_agent, make_numeric_agent


def _faithful_comparison() -> ComparisonReport:
    numeric_agent = make_numeric_agent(MB_NUM_CASES, correct=True)
    citation_agent = make_citation_agent(MB_CIT_CASES, faithful=True)
    return run_agent_vs_baseline(
        agent_numeric_system=numeric_agent.system,
        agent_citation_system=citation_agent.system,
        baseline_numeric_system=abstaining_numeric_baseline,
        baseline_citation_system=abstaining_citation_baseline,
    )


def test_agent_and_baseline_are_evaluated_on_the_same_fixture_suites() -> None:
    comparison = _faithful_comparison()

    assert comparison.agent.numeric.case_count == len(MB_NUM_CASES)
    assert comparison.baseline.numeric.case_count == len(MB_NUM_CASES)
    assert comparison.agent.citation.case_count == len(MB_CIT_CASES)
    assert comparison.baseline.citation.case_count == len(MB_CIT_CASES)


def test_the_harness_measures_a_real_difference_between_agent_and_baseline() -> None:
    comparison = _faithful_comparison()

    assert comparison.agent.numeric.numeric_accuracy == 1.0
    assert comparison.baseline.numeric.numeric_accuracy == 0.0
    assert comparison.agent.numeric.numeric_accuracy > comparison.baseline.numeric.numeric_accuracy


def test_a_faithful_agents_full_report_promotes_and_the_baselines_does_not() -> None:
    comparison = _faithful_comparison()
    profile_agent = _profile("scripted-agent-v1")
    profile_baseline = _profile("abstaining-baseline-v1")

    agent_decision = decide_promotion(comparison.agent.to_metrics_report(), profile_agent)
    baseline_decision = decide_promotion(comparison.baseline.to_metrics_report(), profile_baseline)

    assert agent_decision.outcome == "promote"
    assert baseline_decision.outcome == "hold"


def _profile(system_id: str) -> EvaluationProfile:
    return EvaluationProfile(
        system_id=system_id, prompt_version="prompt-v1", fixture_set_id="meridianbench-demo-v1"
    )
