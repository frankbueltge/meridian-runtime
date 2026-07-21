"""Unit tests for ``mrr.domain.claim_ceiling`` (task-packets/K1-T02.yaml,
MRR-MTH-004/005/006), run entirely DB-free and framework-free — plain string
arguments, no repository, no engine.

Acceptance-test mapping (task-packets/K1-T02.yaml):

- [re-pins method-causal-claim-gate.feature, MRR-MTH-004/006] a causal claim
  below the causal-licensing tier is rejected regardless of profile ->
  ``test_causal_claim_below_causal_licensing_tier_violates_regardless_of_profile_max``.
- [re-pins MRR-MTH-006 for the first real profile] a ruling exceeding its
  own profile's declared max is rejected; an identical claim against a
  licensing ceiling under the SAME profile succeeds ->
  ``test_ruling_exceeding_its_own_profile_max_violates``,
  ``test_ruling_at_or_below_profile_max_is_licensed``.
- [MRR-MTH-005 structural proof] ->
  ``test_signature_has_exactly_the_three_documented_keyword_only_parameters``.
- causal claim at a licensing tier under a permissive profile succeeds ->
  ``test_causal_claim_at_a_licensing_tier_under_a_permissive_profile_passes``.
- non-causal claim types are never subject to the causal-specific check ->
  ``test_non_causal_claim_types_are_never_subject_to_the_causal_specific_check``.
- invalid ceiling values fail closed rather than silently pass ->
  ``test_unknown_ruled_ceiling_raises_value_error``,
  ``test_unknown_profile_max_ceiling_raises_value_error``.
"""

from __future__ import annotations

import inspect

import pytest
from mrr.contracts.method_profile import CLAIM_CEILING_ORDER
from mrr.domain.claim_ceiling import ceiling_violation_reason

# ---------------------------------------------------------------------------
# Universal check: ruling must not exceed its own profile's declared max.
# ---------------------------------------------------------------------------


def test_ruling_exceeding_its_own_profile_max_violates() -> None:
    reason = ceiling_violation_reason(
        claim_type="observational",
        ruled_ceiling="causal_bounded",
        profile_max_ceiling="associational_unadjusted",
    )
    assert reason is not None
    assert "causal_bounded" in reason
    assert "associational_unadjusted" in reason


def test_ruling_at_or_below_profile_max_is_licensed() -> None:
    assert (
        ceiling_violation_reason(
            claim_type="observational",
            ruled_ceiling="descriptive",
            profile_max_ceiling="associational_unadjusted",
        )
        is None
    )
    assert (
        ceiling_violation_reason(
            claim_type="observational",
            ruled_ceiling="associational_unadjusted",
            profile_max_ceiling="associational_unadjusted",
        )
        is None
    )


# ---------------------------------------------------------------------------
# Causal-specific check: claim_type == "causal" requires a causal-licensing
# ceiling, independent of the universal check.
# ---------------------------------------------------------------------------


def test_causal_claim_below_causal_licensing_tier_violates_regardless_of_profile_max() -> None:
    for profile_max in CLAIM_CEILING_ORDER:
        reason = ceiling_violation_reason(
            claim_type="causal",
            ruled_ceiling="associational_adjusted",
            profile_max_ceiling=profile_max,
        )
        assert reason is not None, profile_max


def test_causal_claim_at_a_licensing_tier_under_a_permissive_profile_passes() -> None:
    for ruled_ceiling in ("causal_local", "causal_bounded"):
        assert (
            ceiling_violation_reason(
                claim_type="causal",
                ruled_ceiling=ruled_ceiling,
                profile_max_ceiling="causal_bounded",
            )
            is None
        )


def test_non_causal_claim_types_are_never_subject_to_the_causal_specific_check() -> None:
    for claim_type in (
        "observational",
        "statistical",
        "methodological",
        "interpretive",
        "normative",
        "speculative",
    ):
        assert (
            ceiling_violation_reason(
                claim_type=claim_type,
                ruled_ceiling="descriptive",
                profile_max_ceiling="descriptive",
            )
            is None
        )


# ---------------------------------------------------------------------------
# Fail-closed on garbage ceiling values (both must be schema-valid strings).
# ---------------------------------------------------------------------------


def test_unknown_ruled_ceiling_raises_value_error() -> None:
    with pytest.raises(ValueError, match="ruled_ceiling"):
        ceiling_violation_reason(
            claim_type="observational",
            ruled_ceiling="not-a-real-ceiling",
            profile_max_ceiling="descriptive",
        )


def test_unknown_profile_max_ceiling_raises_value_error() -> None:
    with pytest.raises(ValueError, match="profile_max_ceiling"):
        ceiling_violation_reason(
            claim_type="observational",
            ruled_ceiling="descriptive",
            profile_max_ceiling="not-a-real-ceiling",
        )


# ---------------------------------------------------------------------------
# MRR-MTH-005: structural proof, not policy prose.
# ---------------------------------------------------------------------------


def test_signature_has_exactly_the_three_documented_keyword_only_parameters() -> None:
    """MRR-MTH-005 ("statistical significance, model confidence, or output
    fluency MUST NOT raise a ceiling") is satisfied structurally: nothing
    resembling a confidence/significance/fluency-shaped parameter exists on
    this function's signature at all, proven by introspection rather than
    merely asserted in prose.
    """
    signature = inspect.signature(ceiling_violation_reason)
    parameters = signature.parameters

    assert list(parameters) == ["claim_type", "ruled_ceiling", "profile_max_ceiling"]
    for parameter in parameters.values():
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert not any(
        p.kind is inspect.Parameter.VAR_KEYWORD or p.kind is inspect.Parameter.VAR_POSITIONAL
        for p in parameters.values()
    )
