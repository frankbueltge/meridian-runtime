"""Property tests for mrr.services.verifier.orchestrator (task-packets/
E4-T05.yaml).

Acceptance-test mapping (task-packets/E4-T05.yaml): "determinism - identical
inputs (with identical caller-supplied identity) yield an identical
VerificationResult (same content_hash)", over arbitrary numeric operations
and inputs, not just one fixed fixture ->
``test_numeric_verification_result_is_deterministic_over_arbitrary_inputs``;
"the tool-outcome -> Recommendation mapping ... every tool outcome maps to
exactly one of pass/fail/inconclusive" ->
``test_numeric_result_recommendation_is_always_one_of_the_three_values``.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from datetime import UTC, datetime

from hypothesis import given
from hypothesis import strategies as st
from mrr.contracts.verification_result import IndependenceProfile
from mrr.domain.identity import new_urn
from mrr.services.verifier.orchestrator import build_numeric_verification_result

_PRACTICE_ID = new_urn("practice")
_REVIEWER_ID = new_urn("agent-role")
_TARGET_ID = new_urn("claim")
_FIXED_INSTANT = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)

_INDEPENDENCE_PROFILE = IndependenceProfile(
    principal="deterministic-verifier-tool",
    model_family="n/a — deterministic tool, no model invoked",
    prompt_family="n/a — deterministic tool, no prompt",
    retrieval_path="independent local artifact supplied by caller",
    code_path="mrr.services.verifier",
    data_access_path="read-only, caller-supplied local artifact",
)

_small_ints = st.integers(min_value=-1_000, max_value=1_000)


def _sequential_id_factory() -> Callable[[], str]:
    counter = itertools.count(1)

    def factory() -> str:
        return f"urn:mrr:verification:{next(counter):026d}"

    return factory


def _fixed_clock() -> datetime:
    return _FIXED_INSTANT


@given(numerator=_small_ints, denominator=_small_ints, claimed=_small_ints)
def test_numeric_verification_result_is_deterministic_over_arbitrary_inputs(
    numerator: int, denominator: int, claimed: int
) -> None:
    def _build() -> object:
        return build_numeric_verification_result(
            target_id=_TARGET_ID,
            target_kind="claim",
            reviewer_id=_REVIEWER_ID,
            independence_profile=_INDEPENDENCE_PROFILE,
            practice_id=_PRACTICE_ID,
            operation="ratio",
            claimed_value=claimed,
            inputs={"numerator": numerator, "denominator": denominator},
            id_factory=_sequential_id_factory(),
            clock=_fixed_clock,
        )

    first = _build()
    second = _build()
    assert first == second  # includes content_hash, since both are full model equality


@given(numerator=_small_ints, denominator=_small_ints, claimed=_small_ints)
def test_numeric_result_recommendation_is_always_one_of_the_three_values(
    numerator: int, denominator: int, claimed: int
) -> None:
    result = build_numeric_verification_result(
        target_id=_TARGET_ID,
        target_kind="claim",
        reviewer_id=_REVIEWER_ID,
        independence_profile=_INDEPENDENCE_PROFILE,
        practice_id=_PRACTICE_ID,
        operation="ratio",
        claimed_value=claimed,
        inputs={"numerator": numerator, "denominator": denominator},
        id_factory=_sequential_id_factory(),
        clock=_fixed_clock,
    )
    assert result.recommendation in {"pass", "fail", "inconclusive"}
    # A zero denominator is arithmetically impossible, never silently a match.
    if denominator == 0:
        assert result.recommendation == "inconclusive"
