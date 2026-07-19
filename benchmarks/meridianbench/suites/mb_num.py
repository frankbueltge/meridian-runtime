"""MB-NUM — numeric fidelity (docs/spec/05_EVALUATION_AND_ACCEPTANCE.md
section 3.1, "MB-NUM — Numeric fidelity"). Scored entirely by
``mrr.services.verifier.numeric.recompute_numeric_claim`` (E4-T05), reused
verbatim — this module never recomputes an arithmetic result itself.

--- The label is never needed by the scorer's own arithmetic ---------------

A case's ``NumericCaseInput`` already carries everything
``recompute_numeric_claim`` needs to recompute the TRUE value (``operation``
and ``numeric_inputs``) — the same information the system under test itself
receives. ``score_numeric_case`` therefore calls the verifier with the
SYSTEM's claimed value against the case's own input, and never reads
``case.expected`` at all for the metric itself: label isolation holds even
more strongly than the bare minimum the harness enforces structurally. The
label (``NumericExpectation.correct_value``) exists so a suite's own fixtures
are independently checkable (see ``tests/test_mb_num.py``'s fixture
self-consistency test, which asserts every declared ``correct_value`` really
is what ``recompute_numeric_claim`` produces from that case's own input) and
so a benchmark report can show a case's true answer alongside what each
system claimed — never so a system under test can read it.

--- Fixture set -------------------------------------------------------------

Six cases spanning docs/spec/05's own MB-NUM case list: a recomputable sum, a
numerator/denominator ratio, a percentage, a percentage-point figure (the
"percentage vs percentage-point confusion" case family), a unit conversion,
and a quotient. Every ``correct_value`` below is exact under
``decimal.Decimal`` (no repeating fractions), so an all-correct scripted
system matches with zero rounding slack (default tolerance).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from mrr.services.verifier.numeric import NumberLike, recompute_numeric_claim

from benchmarks.meridianbench.harness import BenchmarkCase


@dataclass(frozen=True, slots=True, kw_only=True)
class NumericCaseInput:
    """What a system under test receives for one MB-NUM case: a numeric
    claim to verify, stated as free text (``claim_text``, used to build the
    agent's prompt) plus the exact named ``operation``/``numeric_inputs``
    ``mrr.services.verifier.numeric.recompute_numeric_claim`` itself expects.
    Never includes the correct answer.
    """

    claim_text: str
    operation: str
    numeric_inputs: Mapping[str, NumberLike]
    tolerance: NumberLike | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class NumericExpectation:
    """The label: the correct recomputed value for this case, reachable only
    by the scorer/this suite's own tests — never by a system under test. See
    the module docstring for why the numeric-accuracy metric itself does not
    even need to read this field.
    """

    correct_value: str


NumericCase = BenchmarkCase[NumericCaseInput, NumericExpectation]


@dataclass(frozen=True, slots=True, kw_only=True)
class NumericSystemOutput:
    """What a system under test reports for one MB-NUM case: either a
    claimed value, or an explicit abstention (``claimed_value=None`` — the
    non-agent baseline's only ever output; see ``systems.baselines``).
    """

    claimed_value: NumberLike | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ScoredNumericCase:
    """One case's full scoring detail — for a human-readable report, not
    itself part of the pass/fail decision (that is ``is_correct`` alone).
    """

    case_id: str
    claimed_value: NumberLike | None
    correct_value: str
    is_correct: bool


def score_numeric_case(case: NumericCase, output: NumericSystemOutput) -> ScoredNumericCase:
    """Score one case by calling ``recompute_numeric_claim`` (E4-T05,
    reused verbatim) with the SYSTEM's claimed value against the case's own
    ``operation``/``numeric_inputs``. An abstention (``claimed_value is
    None``) is never a match — there is nothing to recompute against.
    """
    is_correct = False
    if output.claimed_value is not None:
        recomputation = recompute_numeric_claim(
            operation=case.input.operation,
            claimed_value=output.claimed_value,
            inputs=case.input.numeric_inputs,
            tolerance=case.input.tolerance,
        )
        is_correct = bool(recomputation.matches_claimed_value)
    return ScoredNumericCase(
        case_id=case.case_id,
        claimed_value=output.claimed_value,
        correct_value=case.expected.correct_value,
        is_correct=is_correct,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class NumericMetrics:
    """The MB-NUM metrics report: exact numeric accuracy over the suite, per
    docs/spec/05 section 3.1's "exact numeric accuracy" metric.
    """

    numeric_accuracy: float
    case_count: int
    correct_count: int
    scored_cases: tuple[ScoredNumericCase, ...]


def score_numeric_suite(
    cases: tuple[NumericCase, ...], outputs: tuple[NumericSystemOutput, ...]
) -> NumericMetrics:
    """Score an entire MB-NUM suite run: one output per case, same order.
    Deterministic — the same cases and the same outputs always yield the
    same metrics (``recompute_numeric_claim`` performs no I/O and no model
    call of any kind).
    """
    if len(cases) != len(outputs):
        raise ValueError(
            f"expected one output per case, got {len(outputs)} outputs for {len(cases)} cases"
        )
    scored = tuple(
        score_numeric_case(case, output) for case, output in zip(cases, outputs, strict=True)
    )
    correct_count = sum(1 for s in scored if s.is_correct)
    case_count = len(scored)
    accuracy = correct_count / case_count if case_count else 0.0
    return NumericMetrics(
        numeric_accuracy=accuracy,
        case_count=case_count,
        correct_count=correct_count,
        scored_cases=scored,
    )


#: The MB-NUM fixture suite — six cases spanning docs/spec/05's numerator/
#: denominator, percentage, percentage-point, unit-conversion, and plain
#: recomputable-analysis case families. Every ``correct_value`` is exact
#: (see ``tests/test_mb_num.py``'s fixture self-consistency test).
#:
#: Every ``claim_text`` below deliberately describes the ANALYSIS TASK
#: rather than restating its own numeric answer (and every ``numeric_inputs``
#: value is chosen so no correct_value is a coincidental digit-substring of
#: an input) — a case's true answer must never appear anywhere in what a
#: system under test is shown, not even by accident (see
#: ``tests/test_harness_label_isolation.py``).
MB_NUM_CASES: tuple[NumericCase, ...] = (
    BenchmarkCase(
        case_id="mb-num-001-sum",
        input=NumericCaseInput(
            claim_text="Combined enrollment across both cohorts, tallied for the quarterly report.",
            operation="sum",
            numeric_inputs={"cohort_a": "120", "cohort_b": "330"},
        ),
        expected=NumericExpectation(correct_value="450"),
        metadata={"category": "recomputable analysis with known output"},
    ),
    BenchmarkCase(
        case_id="mb-num-002-ratio",
        input=NumericCaseInput(
            claim_text="The ratio of successes to attempts recorded this quarter.",
            operation="ratio",
            numeric_inputs={"numerator": "30", "denominator": "120"},
        ),
        expected=NumericExpectation(correct_value="0.25"),
        metadata={"category": "numerator/denominator swap"},
    ),
    BenchmarkCase(
        case_id="mb-num-003-percentage",
        input=NumericCaseInput(
            claim_text="The share of surveyed respondents who agreed to participate.",
            operation="percentage",
            numeric_inputs={"part": "45", "whole": "180"},
        ),
        expected=NumericExpectation(correct_value="25"),
        metadata={"category": "table extraction error"},
    ),
    BenchmarkCase(
        case_id="mb-num-004-percentage-point",
        input=NumericCaseInput(
            claim_text="The percentage-point change in support between the two survey waves.",
            operation="percentage_point",
            numeric_inputs={"value_a": "55", "value_b": "53"},
        ),
        expected=NumericExpectation(correct_value="2"),
        metadata={"category": "percentage vs percentage-point confusion"},
    ),
    BenchmarkCase(
        case_id="mb-num-005-unit-conversion",
        input=NumericCaseInput(
            claim_text="The trip distance converted from kilometers to miles.",
            operation="unit_conversion",
            numeric_inputs={"value": "100", "factor": "0.62137"},
        ),
        expected=NumericExpectation(correct_value="62.137"),
        metadata={"category": "unit conversion"},
    ),
    BenchmarkCase(
        case_id="mb-num-006-quotient",
        input=NumericCaseInput(
            claim_text="The dividend split evenly across the reporting groups.",
            operation="quotient",
            numeric_inputs={"dividend": "84", "divisor": "4"},
        ),
        expected=NumericExpectation(correct_value="21"),
        metadata={"category": "different population or time window"},
    ),
)

__all__ = [
    "MB_NUM_CASES",
    "NumericCase",
    "NumericCaseInput",
    "NumericExpectation",
    "NumericMetrics",
    "NumericSystemOutput",
    "ScoredNumericCase",
    "score_numeric_case",
    "score_numeric_suite",
]
