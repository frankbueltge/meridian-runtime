"""MB-CIT acceptance tests (task-packets/E4-T07.yaml): the fixture suite
scored by ``mrr.services.verifier.source.validate_evidence_anchor`` yields a
valid-anchor-resolution rate and a false-support rate; a system that reports
pass on an inaccessible-source case raises the false-support rate, and a
source that cannot be opened is never scored as a valid resolution;
scoring is deterministic.
"""

from __future__ import annotations

from benchmarks.meridianbench.report import evaluate_citation_system
from benchmarks.meridianbench.suites.mb_cit import (
    MB_CIT_CASES,
    CitationMetrics,
    ScoredCitationCase,
    score_citation_suite,
)
from benchmarks.meridianbench.systems.baselines import abstaining_citation_baseline
from benchmarks.meridianbench.systems.scripted_agent import make_citation_agent
from benchmarks.meridianbench.targets import (
    FALSE_SUPPORT_ON_MB_CIT_TARGET,
    VALID_CITATION_ANCHOR_RESOLUTION_TARGET,
)

_INACCESSIBLE_CASE_ID = "mb-cit-002-cited-but-inaccessible"


def _find_scored(metrics: CitationMetrics, case_id: str) -> ScoredCitationCase:
    for scored in metrics.scored_cases:
        if scored.case_id == case_id:
            return scored
    raise AssertionError(f"no scored case with id {case_id!r}")


# ---------------------------------------------------------------------------
# A faithful agent: reports the ground truth, so it never falsely supports.
# ---------------------------------------------------------------------------


def test_faithful_scripted_agent_has_zero_false_support_and_meets_targets() -> None:
    agent = make_citation_agent(MB_CIT_CASES, faithful=True)

    metrics = evaluate_citation_system(agent.system, MB_CIT_CASES)

    assert metrics.false_support_rate == 0.0
    assert metrics.false_support_rate <= FALSE_SUPPORT_ON_MB_CIT_TARGET
    assert metrics.valid_anchor_resolution_rate == 1.0
    assert metrics.valid_anchor_resolution_rate >= VALID_CITATION_ANCHOR_RESOLUTION_TARGET


# ---------------------------------------------------------------------------
# An overclaiming agent (always reports "pass") raises false support — the
# literal acceptance test — while the inaccessible case is still never
# scored as a valid resolution, independent of what the system reports.
# ---------------------------------------------------------------------------


def test_overclaiming_agent_raises_false_support_on_the_inaccessible_case() -> None:
    faithful_metrics = evaluate_citation_system(
        make_citation_agent(MB_CIT_CASES, faithful=True).system, MB_CIT_CASES
    )
    overclaiming_metrics = evaluate_citation_system(
        make_citation_agent(MB_CIT_CASES, faithful=False).system, MB_CIT_CASES
    )

    assert overclaiming_metrics.false_support_rate > faithful_metrics.false_support_rate

    scored = _find_scored(overclaiming_metrics, _INACCESSIBLE_CASE_ID)
    assert scored.system_verdict == "pass"
    assert scored.is_false_support is True
    # A source that cannot be opened is never scored as a valid resolution,
    # regardless of what the system reported for it.
    assert scored.is_valid_resolution is False
    assert scored.anchor_validation_status == "unvalidated"
    assert scored.source_access_outcome == "unverified_source_access"


def test_the_inaccessible_case_is_never_a_valid_resolution_for_any_system() -> None:
    for agent_system in (
        make_citation_agent(MB_CIT_CASES, faithful=True).system,
        make_citation_agent(MB_CIT_CASES, faithful=False).system,
        abstaining_citation_baseline,
    ):
        metrics = evaluate_citation_system(agent_system, MB_CIT_CASES)
        scored = _find_scored(metrics, _INACCESSIBLE_CASE_ID)
        assert scored.is_valid_resolution is False


# ---------------------------------------------------------------------------
# Non-agent baseline: never reports "pass", so it never falsely supports;
# its valid-anchor-resolution rate is identical to the faithful agent's,
# since that metric never depends on what a system reports.
# ---------------------------------------------------------------------------


def test_abstaining_baseline_never_falsely_supports() -> None:
    metrics = evaluate_citation_system(abstaining_citation_baseline, MB_CIT_CASES)

    assert metrics.false_support_rate == 0.0
    assert all(not scored.is_false_support for scored in metrics.scored_cases)
    assert all(scored.system_verdict == "unknown" for scored in metrics.scored_cases)


def test_valid_anchor_resolution_rate_is_independent_of_the_system() -> None:
    faithful_metrics = evaluate_citation_system(
        make_citation_agent(MB_CIT_CASES, faithful=True).system, MB_CIT_CASES
    )
    baseline_metrics = evaluate_citation_system(abstaining_citation_baseline, MB_CIT_CASES)

    assert (
        faithful_metrics.valid_anchor_resolution_rate
        == baseline_metrics.valid_anchor_resolution_rate
    )


# ---------------------------------------------------------------------------
# Deterministic scoring.
# ---------------------------------------------------------------------------


def test_scoring_the_same_suite_twice_with_the_same_system_is_identical() -> None:
    agent = make_citation_agent(MB_CIT_CASES, faithful=True)
    outputs = tuple(agent.system(case.input) for case in MB_CIT_CASES)

    first = score_citation_suite(MB_CIT_CASES, outputs)
    second = score_citation_suite(MB_CIT_CASES, outputs)

    assert first == second
