"""Property tests for ``mrr.domain.claim_ceiling`` (task-packets/K1-T02.yaml,
MRR-MTH-004/005/006).

Acceptance-test mapping: "[MRR-MTH-005 structural proof] ... a property test
constructing many random (claim_type, ruled_ceiling, profile_max_ceiling)
triples confirms the verdict depends ONLY on those three values, in every
combination" -> ``test_verdict_is_a_pure_deterministic_function_of_its_three_arguments``,
``test_verdict_matches_a_reference_reimplementation_for_every_combination``.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st
from mrr.contracts.claim import ClaimType
from mrr.contracts.method_profile import CLAIM_CEILING_ORDER, ClaimCeiling
from mrr.domain.claim_ceiling import ceiling_violation_reason

_CLAIM_TYPES: tuple[ClaimType, ...] = (
    "observational",
    "causal",
    "statistical",
    "methodological",
    "interpretive",
    "normative",
    "speculative",
)

_CEILINGS: tuple[ClaimCeiling, ...] = CLAIM_CEILING_ORDER

_triples = st.tuples(
    st.sampled_from(_CLAIM_TYPES),
    st.sampled_from(_CEILINGS),
    st.sampled_from(_CEILINGS),
)


def _reference_violation_reason(
    claim_type: str, ruled_ceiling: str, profile_max_ceiling: str
) -> str | None:
    """An independent re-derivation of the same two checks, written against
    plain index comparisons rather than calling the function under test —
    the reference this property test cross-checks against, so a bug shared
    between both implementations is less likely than one caught by simply
    re-running the SAME code.
    """
    ruled_rank = _CEILINGS.index(ruled_ceiling)
    max_rank = _CEILINGS.index(profile_max_ceiling)
    if ruled_rank > max_rank:
        return "universal-violation"
    if claim_type == "causal" and ruled_ceiling not in ("causal_local", "causal_bounded"):
        return "causal-violation"
    return None


@given(triple=_triples)
def test_verdict_is_a_pure_deterministic_function_of_its_three_arguments(
    triple: tuple[str, str, str],
) -> None:
    claim_type, ruled_ceiling, profile_max_ceiling = triple

    first = ceiling_violation_reason(
        claim_type=claim_type, ruled_ceiling=ruled_ceiling, profile_max_ceiling=profile_max_ceiling
    )
    second = ceiling_violation_reason(
        claim_type=claim_type, ruled_ceiling=ruled_ceiling, profile_max_ceiling=profile_max_ceiling
    )
    assert first == second


@given(triple=_triples)
def test_verdict_matches_a_reference_reimplementation_for_every_combination(
    triple: tuple[str, str, str],
) -> None:
    claim_type, ruled_ceiling, profile_max_ceiling = triple

    actual = ceiling_violation_reason(
        claim_type=claim_type, ruled_ceiling=ruled_ceiling, profile_max_ceiling=profile_max_ceiling
    )
    expected = _reference_violation_reason(claim_type, ruled_ceiling, profile_max_ceiling)

    assert (actual is None) == (expected is None)
