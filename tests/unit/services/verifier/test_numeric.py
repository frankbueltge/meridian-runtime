"""Unit tests for mrr.services.verifier.numeric (task-packets/E4-T05.yaml,
MRR-FR-073): the closed, safe numeric operation set, exact decimal
recomputation, and the "never neither, never eval" impossible-reason
guarantees.

Acceptance-test mapping (task-packets/E4-T05.yaml):

- "numeric match -> NumericRecomputation(matches_claimed_value=True) ...;
  mismatch -> matches_claimed_value=False ...; recomputation impossible
  (unknown operation / missing input) -> impossible_reason set" ->
  the match/mismatch/impossible sections below.
- "MB-NUM cases recompute deterministically and classify correctly: a
  numerator/denominator swap and a percentage-vs-percentage-point confusion
  each yield a mismatch; a correct unit conversion yields a match" ->
  ``test_mb_num_numerator_denominator_swap_yields_mismatch``,
  ``test_mb_num_percentage_vs_percentage_point_confusion_yields_mismatch``,
  ``test_mb_num_correct_unit_conversion_yields_match``.
- "numeric recomputation never uses eval; an unknown/unsupported operation
  yields an explicit impossible_reason ... never a silent pass" ->
  ``test_unknown_operation_never_falls_back_to_eval``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from mrr.services.verifier.numeric import NUMERIC_OPERATIONS, recompute_numeric_claim

# ---------------------------------------------------------------------------
# The closed operation set itself.
# ---------------------------------------------------------------------------


def test_numeric_operations_is_exactly_the_closed_set_named_in_the_packet() -> None:
    assert set(NUMERIC_OPERATIONS) == {
        "sum",
        "difference",
        "product",
        "quotient",
        "ratio",
        "percentage",
        "percentage_point",
        "unit_conversion",
    }


# ---------------------------------------------------------------------------
# Match / mismatch / impossible — the packet's own three-way acceptance test.
# ---------------------------------------------------------------------------


def test_matching_recomputation_yields_match_true() -> None:
    result = recompute_numeric_claim(
        operation="sum", claimed_value="6", inputs={"a": "1", "b": "2", "c": "3"}
    )
    assert result.impossible_reason is None
    assert result.recomputed_value == "6"
    assert result.matches_claimed_value is True


def test_mismatched_recomputation_yields_match_false() -> None:
    result = recompute_numeric_claim(
        operation="sum", claimed_value="7", inputs={"a": "1", "b": "2", "c": "3"}
    )
    assert result.impossible_reason is None
    assert result.recomputed_value == "6"
    assert result.matches_claimed_value is False


def test_unknown_operation_yields_impossible_reason() -> None:
    result = recompute_numeric_claim(operation="logarithm", claimed_value="1", inputs={"a": "1"})
    assert result.impossible_reason is not None
    assert "logarithm" in result.impossible_reason
    assert result.recomputed_value is None
    assert result.matches_claimed_value is None


def test_missing_required_input_yields_impossible_reason() -> None:
    result = recompute_numeric_claim(
        operation="ratio", claimed_value="1", inputs={"numerator": "10"}
    )
    assert result.impossible_reason is not None
    assert "denominator" in result.impossible_reason
    assert result.recomputed_value is None


def test_recomputation_never_yields_neither_value_nor_impossible_reason() -> None:
    """The E3-T04 NumericRecomputation contract's own invariant, exercised
    over every branch this module can take.
    """
    for result in (
        recompute_numeric_claim(operation="sum", claimed_value="1", inputs={"a": "1"}),
        recompute_numeric_claim(operation="nonsense", claimed_value="1", inputs={}),
        recompute_numeric_claim(operation="quotient", claimed_value="1", inputs={}),
        recompute_numeric_claim(
            operation="quotient", claimed_value="1", inputs={"dividend": "1", "divisor": "0"}
        ),
    ):
        has_value = result.recomputed_value is not None
        has_reason = result.impossible_reason is not None
        assert has_value != has_reason  # exactly one, never both, never neither


# ---------------------------------------------------------------------------
# Each closed operation, on its own.
# ---------------------------------------------------------------------------


def test_sum_requires_at_least_one_input() -> None:
    result = recompute_numeric_claim(operation="sum", claimed_value="0", inputs={})
    assert result.impossible_reason is not None


def test_product_of_named_inputs() -> None:
    result = recompute_numeric_claim(
        operation="product", claimed_value="24", inputs={"a": "2", "b": "3", "c": "4"}
    )
    assert result.matches_claimed_value is True


def test_difference_minuend_subtrahend() -> None:
    result = recompute_numeric_claim(
        operation="difference", claimed_value="5", inputs={"minuend": "12", "subtrahend": "7"}
    )
    assert result.matches_claimed_value is True


def test_quotient_dividend_divisor() -> None:
    result = recompute_numeric_claim(
        operation="quotient", claimed_value="4", inputs={"dividend": "12", "divisor": "3"}
    )
    assert result.matches_claimed_value is True


def test_quotient_division_by_zero_is_impossible_not_a_mismatch() -> None:
    result = recompute_numeric_claim(
        operation="quotient", claimed_value="4", inputs={"dividend": "12", "divisor": "0"}
    )
    assert result.impossible_reason is not None
    assert result.matches_claimed_value is None


def test_ratio_numerator_denominator() -> None:
    result = recompute_numeric_claim(
        operation="ratio", claimed_value="0.25", inputs={"numerator": "30", "denominator": "120"}
    )
    assert result.matches_claimed_value is True


def test_ratio_division_by_zero_is_impossible() -> None:
    result = recompute_numeric_claim(
        operation="ratio", claimed_value="1", inputs={"numerator": "1", "denominator": "0"}
    )
    assert result.impossible_reason is not None


def test_percentage_part_whole() -> None:
    result = recompute_numeric_claim(
        operation="percentage", claimed_value="25", inputs={"part": "30", "whole": "120"}
    )
    assert result.matches_claimed_value is True


def test_percentage_division_by_zero_is_impossible() -> None:
    result = recompute_numeric_claim(
        operation="percentage", claimed_value="1", inputs={"part": "1", "whole": "0"}
    )
    assert result.impossible_reason is not None


def test_percentage_point_value_a_value_b() -> None:
    result = recompute_numeric_claim(
        operation="percentage_point",
        claimed_value="5",
        inputs={"value_a": "15", "value_b": "10"},
    )
    assert result.matches_claimed_value is True


def test_unit_conversion_multiplicative() -> None:
    # 10 km * 0.621371 = 6.21371 miles.
    result = recompute_numeric_claim(
        operation="unit_conversion",
        claimed_value="6.21371",
        inputs={"value": "10", "factor": "0.621371"},
    )
    assert result.matches_claimed_value is True


def test_unit_conversion_affine_with_offset() -> None:
    # 20 Celsius -> Fahrenheit: 20 * 9/5 + 32 = 68.
    result = recompute_numeric_claim(
        operation="unit_conversion",
        claimed_value="68",
        inputs={"value": "20", "factor": "1.8", "offset": "32"},
    )
    assert result.matches_claimed_value is True


def test_unit_conversion_offset_defaults_to_zero() -> None:
    result = recompute_numeric_claim(
        operation="unit_conversion",
        claimed_value="20",
        inputs={"value": "10", "factor": "2"},
    )
    assert result.matches_claimed_value is True


# ---------------------------------------------------------------------------
# MB-NUM cases (docs/spec/05_EVALUATION_AND_ACCEPTANCE.md).
# ---------------------------------------------------------------------------


def test_mb_num_numerator_denominator_swap_yields_mismatch() -> None:
    """The claim asserts the SWAPPED (wrong) value; the verifier is given
    the correct numerator/denominator roles and recomputes the true ratio,
    which does not match the swapped claim.
    """
    correct_ratio = recompute_numeric_claim(
        operation="ratio", claimed_value="0.25", inputs={"numerator": "30", "denominator": "120"}
    )
    assert correct_ratio.matches_claimed_value is True

    swapped_claim = recompute_numeric_claim(
        operation="ratio",
        claimed_value="4",  # what a numerator/denominator swap (120/30) would wrongly assert
        inputs={"numerator": "30", "denominator": "120"},
    )
    assert swapped_claim.matches_claimed_value is False


def test_mb_num_percentage_vs_percentage_point_confusion_yields_mismatch() -> None:
    """A value went from 10% to 15%: a 5 PERCENTAGE-POINT increase, but a
    50% RELATIVE increase. A claim asserting the percentage-point value was
    actually a relative-percentage claim (or vice versa) mismatches when
    recomputed against the correct interpretation.
    """
    percentage_point_claim = recompute_numeric_claim(
        operation="percentage_point",
        claimed_value="50",  # this is the RELATIVE percentage change, not the point difference
        inputs={"value_a": "15", "value_b": "10"},
    )
    assert percentage_point_claim.matches_claimed_value is False
    assert percentage_point_claim.recomputed_value == "5"


def test_mb_num_correct_unit_conversion_yields_match() -> None:
    result = recompute_numeric_claim(
        operation="unit_conversion",
        claimed_value="6.21371",
        inputs={"value": "10", "factor": "0.621371"},
    )
    assert result.matches_claimed_value is True


# ---------------------------------------------------------------------------
# Exactness: decimal.Decimal, never float; never eval.
# ---------------------------------------------------------------------------


def test_float_input_is_rejected_as_impossible_not_silently_converted() -> None:
    # A caller passing a `float` at all violates the NumberLike type hint —
    # this test exercises the runtime guard that exists for exactly that
    # "should never typecheck, but must still fail closed" case.
    inputs: dict[str, object] = {"a": 0.1, "b": 0.2}
    result = recompute_numeric_claim(
        operation="sum",
        claimed_value="0.3",
        inputs=inputs,  # type: ignore[arg-type]
    )
    assert result.impossible_reason is not None
    assert "float" in result.impossible_reason


def test_bool_input_is_rejected() -> None:
    result = recompute_numeric_claim(operation="sum", claimed_value="1", inputs={"a": True})
    assert result.impossible_reason is not None


def test_unparsable_string_input_is_impossible() -> None:
    result = recompute_numeric_claim(
        operation="sum", claimed_value="1", inputs={"a": "not-a-number"}
    )
    assert result.impossible_reason is not None


def test_decimal_input_is_accepted_directly() -> None:
    result = recompute_numeric_claim(
        operation="sum", claimed_value=Decimal("3"), inputs={"a": Decimal("1"), "b": Decimal("2")}
    )
    assert result.matches_claimed_value is True


def test_negative_tolerance_is_impossible() -> None:
    result = recompute_numeric_claim(
        operation="sum", claimed_value="1", inputs={"a": "1"}, tolerance="-0.1"
    )
    assert result.impossible_reason is not None


def test_tolerance_permits_a_legitimately_rounded_claim() -> None:
    result = recompute_numeric_claim(
        operation="ratio",
        claimed_value="0.33",
        inputs={"numerator": "1", "denominator": "3"},
        tolerance="0.01",
    )
    assert result.matches_claimed_value is True


def test_no_tolerance_defaults_to_exact_equality() -> None:
    result = recompute_numeric_claim(
        operation="ratio", claimed_value="0.33", inputs={"numerator": "1", "denominator": "3"}
    )
    assert result.matches_claimed_value is False


@pytest.mark.parametrize(
    "malicious_operation",
    [
        "__import__('os').system('echo pwned')",
        "eval('1+1')",
        "exec('import os')",
        "1+1",
    ],
)
def test_unknown_operation_never_falls_back_to_eval(malicious_operation: str) -> None:
    """A caller-declared operation string outside the closed set is looked
    up in a plain dict and rejected -- never passed to eval/exec/compile of
    any kind, regardless of what it looks like.
    """
    result = recompute_numeric_claim(
        operation=malicious_operation, claimed_value="2", inputs={"a": "1"}
    )
    assert result.impossible_reason is not None
    assert result.recomputed_value is None


def test_module_source_contains_no_eval_exec_or_compile_call() -> None:
    import ast
    from pathlib import Path

    module_path = (
        Path(__file__).resolve().parents[4]
        / "services"
        / "control_plane"
        / "mrr"
        / "services"
        / "verifier"
        / "numeric.py"
    )
    tree = ast.parse(module_path.read_text())
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert called_names.isdisjoint({"eval", "exec", "compile", "__import__"})


# ---------------------------------------------------------------------------
# Determinism.
# ---------------------------------------------------------------------------


def test_recomputation_is_deterministic() -> None:
    first = recompute_numeric_claim(
        operation="ratio", claimed_value="0.25", inputs={"numerator": "30", "denominator": "120"}
    )
    second = recompute_numeric_claim(
        operation="ratio", claimed_value="0.25", inputs={"numerator": "30", "denominator": "120"}
    )
    assert first == second
