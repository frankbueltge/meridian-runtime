"""Unit tests for mrr.services.verifier.orchestrator (task-packets/
E4-T05.yaml): the tool-outcome -> Recommendation mapping and the assembled
``VerificationResult``.

Acceptance-test mapping (task-packets/E4-T05.yaml):

- "numeric match -> ... Recommendation 'pass'; mismatch -> ... 'fail';
  recomputation impossible -> ... 'inconclusive'" ->
  ``test_numeric_*_recommendation`` below.
- "source anchor resolving ... -> 'validated' -> 'pass'; a cited source NOT
  locally available ... -> unverified_source_access -> 'inconclusive',
  NEVER 'pass' ...; an anchor pointing to absent content -> 'invalid' ->
  'fail'" -> ``test_source_*_recommendation`` below.
- "determinism - identical inputs (with identical caller-supplied identity)
  yield an identical VerificationResult (same content_hash)" ->
  ``test_numeric_result_is_deterministic``,
  ``test_source_result_is_deterministic``.
- "the built VerificationResult validates against the existing
  verification-result schema and carries the caller-declared
  independence_profile" -> ``test_independence_profile_is_carried_through``.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from mrr.contracts.evidence_anchor import EvidenceAnchor, TextLocator
from mrr.contracts.verification_result import (
    IndependenceProfile,
    NumericRecomputation,
    VerificationResult,
)
from mrr.crypto.hashing import content_hash
from mrr.domain.hashing_policy import compute_content_hash
from mrr.domain.identity import new_urn
from mrr.services.verifier.orchestrator import (
    build_numeric_verification_result,
    build_source_verification_result,
    recommendation_for_anchor_status,
    recommendation_for_numeric_outcome,
)
from mrr.services.verifier.source import LocalTextArtifact

_PRACTICE_ID = new_urn("practice")
_REVIEWER_ID = new_urn("agent-role")
_TARGET_ID = new_urn("claim")
_FIXED_INSTANT = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)


def _independence_profile(**overrides: str) -> IndependenceProfile:
    data = {
        "principal": "deterministic-verifier-tool",
        "model_family": "n/a — deterministic tool, no model invoked",
        "prompt_family": "n/a — deterministic tool, no prompt",
        "retrieval_path": "independent local artifact supplied by caller",
        "code_path": "mrr.services.verifier",
        "data_access_path": "read-only, caller-supplied local artifact",
    }
    data.update(overrides)
    return IndependenceProfile.model_validate(data)


def _sequential_id_factory() -> Callable[[], str]:
    counter = itertools.count(1)

    def factory() -> str:
        return f"urn:mrr:verification:{next(counter):026d}"

    return factory


def _fixed_clock() -> datetime:
    return _FIXED_INSTANT


# ---------------------------------------------------------------------------
# The tool-outcome -> Recommendation mapping, on its own.
# ---------------------------------------------------------------------------


def test_numeric_match_maps_to_pass() -> None:
    recomputation = NumericRecomputation(recomputed_value="6", matches_claimed_value=True)
    assert recommendation_for_numeric_outcome(recomputation) == "pass"


def test_numeric_mismatch_maps_to_fail() -> None:
    recomputation = NumericRecomputation(recomputed_value="6", matches_claimed_value=False)
    assert recommendation_for_numeric_outcome(recomputation) == "fail"


def test_numeric_impossible_maps_to_inconclusive() -> None:
    recomputation = NumericRecomputation(impossible_reason="unknown operation")
    assert recommendation_for_numeric_outcome(recomputation) == "inconclusive"


def test_anchor_validated_maps_to_pass() -> None:
    assert recommendation_for_anchor_status("validated") == "pass"


def test_anchor_unvalidated_maps_to_inconclusive_never_pass() -> None:
    assert recommendation_for_anchor_status("unvalidated") == "inconclusive"


def test_anchor_invalid_maps_to_fail() -> None:
    assert recommendation_for_anchor_status("invalid") == "fail"


def test_anchor_status_mapping_is_total_over_the_three_values() -> None:
    from mrr.contracts.evidence_anchor import AnchorValidationStatus

    for status in AnchorValidationStatus.__args__:  # type: ignore[attr-defined]
        assert recommendation_for_anchor_status(status) in {"pass", "fail", "inconclusive"}


def test_anchor_status_mapping_rejects_an_unknown_status() -> None:
    with pytest.raises(ValueError, match="unknown AnchorValidationStatus"):
        recommendation_for_anchor_status("bogus")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# build_numeric_verification_result.
# ---------------------------------------------------------------------------


def _build_numeric(**overrides: object) -> VerificationResult:
    defaults: dict[str, object] = {
        "target_id": _TARGET_ID,
        "target_kind": "claim",
        "reviewer_id": _REVIEWER_ID,
        "independence_profile": _independence_profile(),
        "practice_id": _PRACTICE_ID,
        "operation": "ratio",
        "claimed_value": "0.25",
        "inputs": {"numerator": "30", "denominator": "120"},
        "id_factory": _sequential_id_factory(),
        "clock": _fixed_clock,
    }
    defaults.update(overrides)
    return build_numeric_verification_result(**defaults)  # type: ignore[arg-type]


def test_numeric_result_matching_claim_is_pass() -> None:
    result = _build_numeric()
    assert result.verification_type == "numeric"
    assert result.recommendation == "pass"
    assert result.numeric_recomputation is not None
    assert result.numeric_recomputation.matches_claimed_value is True
    assert result.evidence_inspected == []
    assert result.target_id == _TARGET_ID
    assert result.reviewer_id == _REVIEWER_ID
    assert result.independence_profile == _independence_profile()
    assert result.confidence == 1.0


def test_numeric_result_mismatched_claim_is_fail() -> None:
    result = _build_numeric(claimed_value="4")
    assert result.recommendation == "fail"


def test_numeric_result_impossible_operation_is_inconclusive() -> None:
    result = _build_numeric(operation="logarithm")
    assert result.recommendation == "inconclusive"
    assert result.numeric_recomputation is not None
    assert result.numeric_recomputation.impossible_reason is not None


def test_numeric_result_created_by_defaults_to_reviewer_id() -> None:
    result = _build_numeric()
    assert result.created_by == _REVIEWER_ID


def test_numeric_result_is_deterministic() -> None:
    first = _build_numeric(id_factory=_sequential_id_factory())
    second = _build_numeric(id_factory=_sequential_id_factory())
    assert first.content_hash == second.content_hash
    assert first.model_dump() == second.model_dump()


def test_numeric_result_content_hash_recomputes_to_the_same_value() -> None:
    result = _build_numeric()
    body = json.loads(result.model_dump_json(exclude_none=True))
    assert compute_content_hash(body) == result.content_hash


# ---------------------------------------------------------------------------
# build_source_verification_result.
# ---------------------------------------------------------------------------

_FULL_TEXT = "The cohort showed a measurable increase in reported outcomes."
_SNAPSHOT_HASH = content_hash(_FULL_TEXT.encode("utf-8"))
_FRAGMENT = _FULL_TEXT[4:10]
_FRAGMENT_HASH = content_hash(_FRAGMENT.encode("utf-8"))


def _anchor(**overrides: object) -> EvidenceAnchor:
    data: dict[str, object] = {
        "id": new_urn("evidence-anchor"),
        "api_version": "mrr/v1alpha1",
        "kind": "EvidenceAnchor",
        "practice_id": _PRACTICE_ID,
        "revision": 1,
        "created_at": _FIXED_INSTANT,
        "created_by": _REVIEWER_ID,
        "content_hash": "sha256:" + "a" * 64,
        "relation": "supports",
        "anchor_kind": "text",
        "extraction_method": "manual review",
        "extractor_id": _REVIEWER_ID,
        "anchor_validation_status": "unvalidated",
        "transformation_chain": [],
        "source_record_id": new_urn("source-record"),
        "snapshot_hash": _SNAPSHOT_HASH,
        "locator": TextLocator(char_start=4, char_end=10),
        "quoted_fragment_hash": _FRAGMENT_HASH,
    }
    data.update(overrides)
    return EvidenceAnchor.model_validate(data)


def _build_source(**overrides: object) -> VerificationResult:
    anchor = overrides.pop("anchor", None) or _anchor()
    defaults: dict[str, object] = {
        "target_id": _TARGET_ID,
        "target_kind": "claim",
        "reviewer_id": _REVIEWER_ID,
        "independence_profile": _independence_profile(),
        "practice_id": _PRACTICE_ID,
        "anchor": anchor,
        "local_text_artifact": LocalTextArtifact(full_text=_FULL_TEXT),
        "id_factory": _sequential_id_factory(),
        "clock": _fixed_clock,
    }
    defaults.update(overrides)
    return build_source_verification_result(**defaults)  # type: ignore[arg-type]


def test_source_result_validated_anchor_is_pass() -> None:
    anchor = _anchor()
    result = _build_source(anchor=anchor)
    assert result.verification_type == "source"
    assert result.recommendation == "pass"
    assert result.numeric_recomputation is None
    assert result.evidence_inspected == [anchor.id]


def test_source_result_unavailable_source_is_inconclusive_never_pass() -> None:
    """The section 4.8 acceptance case, literally: a source that cannot be
    locally opened is NEVER recommended "pass".
    """
    result = _build_source(local_text_artifact=None)
    assert result.recommendation == "inconclusive"


def test_source_result_invalid_anchor_is_fail() -> None:
    anchor = _anchor(quoted_fragment_hash="sha256:" + "f" * 64)
    result = _build_source(anchor=anchor)
    assert result.recommendation == "fail"


def test_source_result_is_deterministic() -> None:
    anchor = _anchor()
    first = _build_source(anchor=anchor, id_factory=_sequential_id_factory())
    second = _build_source(anchor=anchor, id_factory=_sequential_id_factory())
    assert first.content_hash == second.content_hash
    assert first.model_dump() == second.model_dump()


# ---------------------------------------------------------------------------
# Independence, confidence, reviewer role — shared across both builders.
# ---------------------------------------------------------------------------


def test_independence_profile_is_carried_through() -> None:
    profile = _independence_profile(principal="a-specific-declared-principal")
    result = _build_numeric(independence_profile=profile)
    assert result.independence_profile == profile


def test_confidence_is_always_one() -> None:
    matching = _build_numeric()
    mismatching = _build_numeric(claimed_value="999")
    assert matching.confidence == 1.0
    assert mismatching.confidence == 1.0


def test_numeric_and_source_reviewer_roles_are_distinct_and_fixed() -> None:
    numeric_result = _build_numeric()
    source_result = _build_source()
    assert "numeric" in numeric_result.reviewer_role
    assert "source" in source_result.reviewer_role
    assert numeric_result.reviewer_role != source_result.reviewer_role
