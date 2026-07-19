"""Property tests for mrr.services.verifier.numeric (task-packets/
E4-T05.yaml, MRR-FR-073).

Acceptance-test mapping (task-packets/E4-T05.yaml): "numeric recomputation
never uses eval; an unknown/unsupported operation yields an explicit
impossible_reason ... never a silent pass" ->
``test_unknown_operation_is_always_impossible``; the "never neither"
NumericRecomputation invariant, over arbitrary closed-set operations and
inputs -> ``test_recomputation_never_carries_neither_value_nor_reason``;
determinism, over arbitrary inputs -> ``test_recomputation_is_deterministic``;
the ``ratio``/``quotient`` division arithmetic itself, over arbitrary
nonzero denominators -> ``test_ratio_of_claimed_correct_value_always_matches``.
"""

from __future__ import annotations

import decimal
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st
from mrr.services.verifier.numeric import NUMERIC_OPERATIONS, recompute_numeric_claim

#: Small, exact integers -- kept modest so decimal division never needs more
#: than this module's own 50-digit local precision to represent exactly
#: enough for a round-trip comparison.
_small_ints = st.integers(min_value=-1_000, max_value=1_000)
_nonzero_small_ints = st.integers(min_value=-1_000, max_value=1_000).filter(lambda n: n != 0)

_operation_names = st.text(min_size=1, max_size=20)


@given(numerator=_small_ints, denominator=_nonzero_small_ints)
def test_ratio_of_claimed_correct_value_always_matches(numerator: int, denominator: int) -> None:
    # Computed at a lower precision than recompute_numeric_claim's own
    # (higher) internal precision, so a non-terminating ratio (e.g. 1/3)
    # rounds slightly differently on each side -- a small explicit tolerance
    # absorbs exactly that, and nothing more.
    with decimal.localcontext() as ctx:
        ctx.prec = 25
        exact_ratio = Decimal(numerator) / Decimal(denominator)
    result = recompute_numeric_claim(
        operation="ratio",
        claimed_value=str(exact_ratio),
        inputs={"numerator": numerator, "denominator": denominator},
        tolerance="1e-20",
    )
    assert result.matches_claimed_value is True
    assert result.impossible_reason is None


@given(numerator=_small_ints, denominator=_nonzero_small_ints, claimed=_small_ints)
def test_ratio_recomputation_is_deterministic(
    numerator: int, denominator: int, claimed: int
) -> None:
    first = recompute_numeric_claim(
        operation="ratio",
        claimed_value=claimed,
        inputs={"numerator": numerator, "denominator": denominator},
    )
    second = recompute_numeric_claim(
        operation="ratio",
        claimed_value=claimed,
        inputs={"numerator": numerator, "denominator": denominator},
    )
    assert first == second


@given(operation=_operation_names)
def test_unknown_operation_is_always_impossible(operation: str) -> None:
    """Any operation name outside the closed set -- however it is spelled,
    including strings that look like code -- is rejected as impossible,
    never executed and never a silent pass.
    """
    if operation in NUMERIC_OPERATIONS:
        return
    result = recompute_numeric_claim(operation=operation, claimed_value="0", inputs={"a": "0"})
    assert result.impossible_reason is not None
    assert result.recomputed_value is None
    assert result.matches_claimed_value is None


@given(
    operation=st.sampled_from(NUMERIC_OPERATIONS),
    numerator=_small_ints,
    denominator=_small_ints,
    claimed=_small_ints,
)
def test_recomputation_never_carries_neither_value_nor_reason(
    operation: str, numerator: int, denominator: int, claimed: int
) -> None:
    """MRR-FR-073's own invariant, over every closed-set operation name and
    arbitrary (including degenerate, e.g. zero-denominator) inputs -- always
    exactly one of (recomputed_value present) or (impossible_reason
    present), regardless of which operation-specific named inputs this
    particular operation actually needed.
    """
    result = recompute_numeric_claim(
        operation=operation,
        claimed_value=claimed,
        inputs={
            "numerator": numerator,
            "denominator": denominator,
            "dividend": numerator,
            "divisor": denominator,
            "minuend": numerator,
            "subtrahend": denominator,
            "part": numerator,
            "whole": denominator,
            "value_a": numerator,
            "value_b": denominator,
            "value": numerator,
            "factor": denominator,
        },
    )
    has_value = result.recomputed_value is not None
    has_reason = result.impossible_reason is not None
    assert has_value != has_reason
