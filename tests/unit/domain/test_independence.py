"""Unit tests for mrr.domain.independence (task-packets/E3-T05.yaml).

Acceptance-test mapping (task-packets/E3-T05.yaml):

- "a verifier with a different principal and an altered reasoning path is
  independent of the producer" ->
  ``test_different_principal_and_altered_reasoning_path_is_independent``.
- "a verifier sharing the producer's principal and unaltered reasoning path
  is NOT independent" ->
  ``test_same_principal_and_unaltered_reasoning_path_is_not_independent``,
  ``test_identical_profile_is_not_independent``.
- "boundary cases (same principal but altered model_family -> independent,
  per your rule; document)" ->
  ``test_same_principal_but_one_altered_reasoning_dimension_is_independent``
  (parametrized over model_family/prompt_family/code_path), plus
  ``test_different_principal_but_unaltered_reasoning_path_is_independent``
  for the mirror-image boundary.
- "two verifications with identical independence profiles count as one
  (MRR-FR-076); with differing profiles count as two" ->
  ``test_dedup_collapses_identical_profiles``,
  ``test_distinct_profiles_counted_separately``.
"""

from __future__ import annotations

from typing import Any

import pytest
from mrr.contracts.verification_result import IndependenceProfile
from mrr.domain.independence import (
    distinct_independent_reviews,
    has_independent_verification,
    is_independent_of_producer,
)


def _profile(**overrides: Any) -> IndependenceProfile:
    data: dict[str, Any] = {
        "principal": "urn:mrr:person:producer-principal",
        "model_family": "claude-x",
        "prompt_family": "prompt-v1",
        "retrieval_path": "crawl-a",
        "code_path": "notebook-a",
        "data_access_path": "dataset-a",
    }
    data.update(overrides)
    return IndependenceProfile(**data)


PRODUCER = _profile()


# ---------------------------------------------------------------------------
# The core independence rule (domain 2.13).
# ---------------------------------------------------------------------------


def test_different_principal_and_altered_reasoning_path_is_independent() -> None:
    verifier = _profile(
        principal="urn:mrr:person:someone-else",
        model_family="other-model",
        prompt_family="other-prompt",
        code_path="other-notebook",
    )
    assert is_independent_of_producer(verifier, PRODUCER) is True


def test_same_principal_and_unaltered_reasoning_path_is_not_independent() -> None:
    """Only the evidence-access dimensions (retrieval_path/data_access_path)
    differ from the producer — per this module's documented reading, those
    are NOT part of "reasoning path", so the verifier is still disqualified.
    """
    verifier = _profile(retrieval_path="different-crawl", data_access_path="different-dataset")
    assert is_independent_of_producer(verifier, PRODUCER) is False


def test_identical_profile_is_not_independent() -> None:
    assert is_independent_of_producer(_profile(), PRODUCER) is False


# ---------------------------------------------------------------------------
# Boundary cases: same principal, exactly ONE reasoning-path dimension
# altered -> independent (per the documented rule: altering ANY ONE
# reasoning-path dimension is sufficient, since disqualification requires
# ALL of principal + model_family + prompt_family + code_path to match).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "override",
    [
        {"model_family": "different-model"},
        {"prompt_family": "different-prompt"},
        {"code_path": "different-notebook"},
    ],
    ids=["model_family", "prompt_family", "code_path"],
)
def test_same_principal_but_one_altered_reasoning_dimension_is_independent(
    override: dict[str, str],
) -> None:
    verifier = _profile(**override)
    assert is_independent_of_producer(verifier, PRODUCER) is True


def test_different_principal_but_unaltered_reasoning_path_is_independent() -> None:
    verifier = _profile(principal="urn:mrr:person:someone-else")
    assert is_independent_of_producer(verifier, PRODUCER) is True


def test_independence_verdict_is_symmetric_in_argument_order() -> None:
    """Both conjuncts (principal equality, reasoning-path equality) are
    symmetric equality checks, so swapping which profile is "verifier" and
    which is "producer" must not change the verdict."""
    verifier = _profile(principal="urn:mrr:person:someone-else")
    assert is_independent_of_producer(verifier, PRODUCER) == is_independent_of_producer(
        PRODUCER, verifier
    )


# ---------------------------------------------------------------------------
# FR-076 dedup.
# ---------------------------------------------------------------------------


def test_dedup_collapses_identical_profiles() -> None:
    independent_a = _profile(principal="urn:mrr:person:reviewer-a")
    independent_b = _profile(principal="urn:mrr:person:reviewer-a")  # identical profile
    assert distinct_independent_reviews(PRODUCER, [independent_a, independent_b]) == 1


def test_distinct_profiles_counted_separately() -> None:
    independent_a = _profile(principal="urn:mrr:person:reviewer-a")
    independent_b = _profile(principal="urn:mrr:person:reviewer-b")
    assert distinct_independent_reviews(PRODUCER, [independent_a, independent_b]) == 2


def test_dedup_key_is_the_full_six_dimension_profile() -> None:
    """Two verifiers sharing every dimension except retrieval_path (both
    still independent of the producer, since their principal differs from
    the producer's) must count as TWO — the dedup key is the full
    six-dimension profile, not a narrower model/prompt/code-only subset."""
    independent_a = _profile(principal="urn:mrr:person:reviewer-a", retrieval_path="crawl-x")
    independent_b = _profile(principal="urn:mrr:person:reviewer-a", retrieval_path="crawl-y")
    assert distinct_independent_reviews(PRODUCER, [independent_a, independent_b]) == 2


def test_non_independent_verifiers_are_excluded_from_the_count() -> None:
    non_independent = _profile()  # identical to producer -> not independent
    independent = _profile(principal="urn:mrr:person:reviewer-a")
    assert distinct_independent_reviews(PRODUCER, [non_independent, independent]) == 1


def test_empty_verifiers_counts_zero() -> None:
    assert distinct_independent_reviews(PRODUCER, []) == 0


# ---------------------------------------------------------------------------
# has_independent_verification.
# ---------------------------------------------------------------------------


def test_has_independent_verification_respects_minimum() -> None:
    independent_a = _profile(principal="urn:mrr:person:reviewer-a")
    independent_b = _profile(principal="urn:mrr:person:reviewer-b")
    assert has_independent_verification(PRODUCER, [independent_a], minimum=1) is True
    assert has_independent_verification(PRODUCER, [independent_a], minimum=2) is False
    assert has_independent_verification(PRODUCER, [independent_a, independent_b], minimum=2) is True


def test_has_independent_verification_default_minimum_is_one() -> None:
    assert has_independent_verification(PRODUCER, []) is False
    assert (
        has_independent_verification(PRODUCER, [_profile(principal="urn:mrr:person:reviewer-a")])
        is True
    )


def test_has_independent_verification_rejects_negative_minimum() -> None:
    with pytest.raises(ValueError, match="minimum must be >= 0"):
        has_independent_verification(PRODUCER, [], minimum=-1)
