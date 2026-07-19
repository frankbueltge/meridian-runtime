"""The non-agent baseline (task-packets/E4-T07.yaml): a trivial,
deterministic system that always abstains — run on the SAME suites as the
scripted agent-under-test (``systems.scripted_agent``) so MB-CIT and MB-NUM
are "evaluated against a non-agent baseline" (the Epic E4 exit criterion).

Deliberately NOT built on ``mrr.domain.model_adapter.ModelAdapter`` — this is
the point of a *non-agent* baseline: no prompt, no invocation, no model of
any kind, just a plain function always returning the same "unknown" answer.
Comparing this against the scripted agent's metrics is what demonstrates the
harness actually measures a difference between a system that tries and one
that does not.
"""

from __future__ import annotations

from benchmarks.meridianbench.suites.mb_cit import CitationCaseInput, CitationSystemOutput
from benchmarks.meridianbench.suites.mb_num import NumericCaseInput, NumericSystemOutput


def abstaining_numeric_baseline(case_input: NumericCaseInput) -> NumericSystemOutput:
    """Always abstains: never claims a value, so it can never be scored as
    a correct match (``suites.mb_num.score_numeric_case`` never calls the
    verifier for an abstention).
    """
    del case_input  # a non-agent baseline reads nothing from its input
    return NumericSystemOutput(claimed_value=None)


def abstaining_citation_baseline(case_input: CitationCaseInput) -> CitationSystemOutput:
    """Always reports ``"unknown"``: never reports ``"pass"``, so it can
    never contribute to the false-support rate.
    """
    del case_input  # a non-agent baseline reads nothing from its input
    return CitationSystemOutput(verdict="unknown")


__all__ = [
    "abstaining_citation_baseline",
    "abstaining_numeric_baseline",
]
