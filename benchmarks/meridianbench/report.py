"""Orchestration: run a system over the MB-NUM/MB-CIT fixture suites, compose
both suites' metrics into one ``promotion.MetricsReport``, and run the
agent-under-test alongside the non-agent baseline on the SAME suites — the
Epic E4 exit criterion ("MB-CIT and MB-NUM targets are evaluated against a
non-agent baseline").

Every function here is a thin, deterministic composition of
``harness.run_suite`` and each suite's own ``score_*_suite`` — no new
scoring logic lives in this module.
"""

from __future__ import annotations

from dataclasses import dataclass

from benchmarks.meridianbench.harness import SystemUnderTest, run_suite
from benchmarks.meridianbench.promotion import MetricsReport
from benchmarks.meridianbench.suites.mb_cit import (
    MB_CIT_CASES,
    CitationCase,
    CitationCaseInput,
    CitationMetrics,
    CitationSystemOutput,
    score_citation_suite,
)
from benchmarks.meridianbench.suites.mb_num import (
    MB_NUM_CASES,
    NumericCase,
    NumericCaseInput,
    NumericMetrics,
    NumericSystemOutput,
    score_numeric_suite,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class BenchmarkRunReport:
    """One system's metrics across both populated suites."""

    numeric: NumericMetrics
    citation: CitationMetrics

    def to_metrics_report(self) -> MetricsReport:
        """Compose this run's two suite metrics into the flat
        ``promotion.MetricsReport`` shape ``promotion.decide_promotion``
        expects.
        """
        return MetricsReport(
            numeric_accuracy=self.numeric.numeric_accuracy,
            valid_anchor_resolution_rate=self.citation.valid_anchor_resolution_rate,
            false_support_rate=self.citation.false_support_rate,
        )


def evaluate_numeric_system(
    system: SystemUnderTest[NumericCaseInput, NumericSystemOutput],
    cases: tuple[NumericCase, ...] = MB_NUM_CASES,
) -> NumericMetrics:
    outputs = tuple(run_suite(system, cases))
    return score_numeric_suite(cases, outputs)


def evaluate_citation_system(
    system: SystemUnderTest[CitationCaseInput, CitationSystemOutput],
    cases: tuple[CitationCase, ...] = MB_CIT_CASES,
) -> CitationMetrics:
    outputs = tuple(run_suite(system, cases))
    return score_citation_suite(cases, outputs)


def run_full_benchmark(
    *,
    numeric_system: SystemUnderTest[NumericCaseInput, NumericSystemOutput],
    citation_system: SystemUnderTest[CitationCaseInput, CitationSystemOutput],
    numeric_cases: tuple[NumericCase, ...] = MB_NUM_CASES,
    citation_cases: tuple[CitationCase, ...] = MB_CIT_CASES,
) -> BenchmarkRunReport:
    """Run one system (a matched pair of numeric/citation callables — e.g.
    both the scripted agent's two suite-specific systems, or both the
    baseline's) over both suites.
    """
    return BenchmarkRunReport(
        numeric=evaluate_numeric_system(numeric_system, numeric_cases),
        citation=evaluate_citation_system(citation_system, citation_cases),
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ComparisonReport:
    """The agent-under-test's ``BenchmarkRunReport`` and the non-agent
    baseline's, over the SAME suites — reported side by side (the Epic E4
    exit criterion).
    """

    agent: BenchmarkRunReport
    baseline: BenchmarkRunReport


def run_agent_vs_baseline(
    *,
    agent_numeric_system: SystemUnderTest[NumericCaseInput, NumericSystemOutput],
    agent_citation_system: SystemUnderTest[CitationCaseInput, CitationSystemOutput],
    baseline_numeric_system: SystemUnderTest[NumericCaseInput, NumericSystemOutput],
    baseline_citation_system: SystemUnderTest[CitationCaseInput, CitationSystemOutput],
    numeric_cases: tuple[NumericCase, ...] = MB_NUM_CASES,
    citation_cases: tuple[CitationCase, ...] = MB_CIT_CASES,
) -> ComparisonReport:
    """Run both the agent-under-test and the non-agent baseline over the
    identical MB-NUM/MB-CIT fixture suites and return both reports together.
    """
    agent = run_full_benchmark(
        numeric_system=agent_numeric_system,
        citation_system=agent_citation_system,
        numeric_cases=numeric_cases,
        citation_cases=citation_cases,
    )
    baseline = run_full_benchmark(
        numeric_system=baseline_numeric_system,
        citation_system=baseline_citation_system,
        numeric_cases=numeric_cases,
        citation_cases=citation_cases,
    )
    return ComparisonReport(agent=agent, baseline=baseline)


__all__ = [
    "BenchmarkRunReport",
    "ComparisonReport",
    "evaluate_citation_system",
    "evaluate_numeric_system",
    "run_agent_vs_baseline",
    "run_full_benchmark",
]
