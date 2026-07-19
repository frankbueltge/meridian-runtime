"""Contract tests for ``VerificationResult`` (task-packets/E3-T04.yaml)
beyond the generic example-driven checks tests/contract/test_examples.py
already runs.

examples/verification-result.example.json (picked up automatically by
test_examples.py) uses a single ``verification_type`` value ("numeric") and
a single ``target_kind`` value ("claim"). This module covers the remaining
domain-2.13/MRR-FR-071 vocabulary values inline (adding more files under
examples/ would break test_examples.py's "exactly one example per schema"
check, ``test_every_schema_has_an_example_and_a_model``) — mirroring
tests/contract/test_source_family_variants.py's own precedent.

The malformed-urn, unknown-recommendation, and missing-independence-dimension
failure cases are covered by
tests/contract/fixtures/invalid/verification-result-malformed-urn.json,
verification-result-unknown-recommendation.json, and
verification-result-missing-independence-dimension.json via
tests/contract/test_negative_fixtures.py, not duplicated here.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from mrr.contracts import Recommendation, TargetKind, VerificationResult, VerificationType
from pydantic import ValidationError

from scripts.check_contracts import SCHEMAS_DIR, build_registry, build_validator_for_schema

_ALL_TARGET_KINDS: tuple[TargetKind, ...] = ("claim", "run", "artifact")
_ALL_RECOMMENDATIONS: tuple[Recommendation, ...] = ("pass", "fail", "inconclusive")


def _independence_profile(**overrides: Any) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "principal": "urn:mrr:person:01J00000000000000000000036",
        "model_family": "human-reviewer (no model invoked)",
        "prompt_family": "n/a — manual review checklist v3",
        "retrieval_path": "independent re-fetch via publisher API, not the original crawl",
        "code_path": "independent recomputation script, not the original analysis notebook",
        "data_access_path": "read-only snapshot corpus, separate credential from the proposer's",
    }
    profile.update(overrides)
    return profile


def _base_document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "id": "urn:mrr:verification:01J00000000000000000000010",
        "api_version": "mrr/v1alpha1",
        "kind": "VerificationResult",
        "practice_id": "urn:mrr:practice:01J00000000000000000000001",
        "revision": 1,
        "created_at": "2026-07-18T11:00:00Z",
        "created_by": "urn:mrr:person:01J00000000000000000000036",
        "content_hash": "sha256:" + "9" * 64,
        "target_id": "urn:mrr:claim:01J00000000000000000000011",
        "target_kind": "claim",
        "reviewer_id": "urn:mrr:person:01J00000000000000000000036",
        "reviewer_role": "independent reviewer",
        "independence_profile": _independence_profile(),
        "verification_type": "skeptic",
        "checks_performed": ["Searched for counterevidence and alternative explanations"],
        "evidence_inspected": [],
        "numeric_recomputation": None,
        "findings": [],
        "recommendation": "pass",
        "confidence": 0.8,
        "rationale": "Fixture rationale for a contract-level variant check.",
        "conflicts_of_interest": [],
        "adjudication_relation": None,
    }
    document.update(overrides)
    return document


def _validate_against_schema(document: dict[str, Any]) -> None:
    schema = json.loads((SCHEMAS_DIR / "verification-result.schema.json").read_text())
    registry = build_registry()
    build_validator_for_schema(schema, registry).validate(document)


# ---------------------------------------------------------------------------
# target_kind: every value validates; an unknown value fails both validators.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("target_kind", _ALL_TARGET_KINDS)
def test_each_target_kind_validates(target_kind: TargetKind) -> None:
    document = _base_document(target_kind=target_kind)

    _validate_against_schema(document)
    result = VerificationResult.model_validate(document)

    assert result.target_kind == target_kind


def test_unknown_target_kind_rejected_at_model_level() -> None:
    document = _base_document(target_kind="dataset")

    with pytest.raises(ValidationError, match="target_kind"):
        VerificationResult.model_validate(document)


# ---------------------------------------------------------------------------
# recommendation: every value validates; an unknown value fails both.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("recommendation", _ALL_RECOMMENDATIONS)
def test_each_recommendation_validates(recommendation: Recommendation) -> None:
    document = _base_document(recommendation=recommendation)

    _validate_against_schema(document)
    result = VerificationResult.model_validate(document)

    assert result.recommendation == recommendation


def test_unknown_recommendation_rejected_at_model_level() -> None:
    document = _base_document(recommendation="looks_fine_i_guess")

    with pytest.raises(ValidationError, match="recommendation"):
        VerificationResult.model_validate(document)


# ---------------------------------------------------------------------------
# verification_type: source/numeric carry their own MRR-FR-072/073 gates;
# skeptic/reproduction/other carry none.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verification_type", ["skeptic", "reproduction", "other"])
def test_ungated_verification_types_validate_with_no_evidence_or_recomputation(
    verification_type: VerificationType,
) -> None:
    document = _base_document(verification_type=verification_type)

    _validate_against_schema(document)
    result = VerificationResult.model_validate(document)

    assert result.verification_type == verification_type


def test_unknown_verification_type_rejected_at_model_level() -> None:
    document = _base_document(verification_type="vibes")

    with pytest.raises(ValidationError, match="verification_type"):
        VerificationResult.model_validate(document)


def test_source_verification_requires_at_least_one_evidence_inspected() -> None:
    """MRR-FR-072: 'Source verification MUST retrieve or locally inspect the
    cited source and validate the evidence anchor' — a 'source'
    verification_type with an empty evidence_inspected fails closed, both
    at the schema level (if/then) and the model level (model_validator).
    """
    document = _base_document(verification_type="source", evidence_inspected=[])

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="evidence_inspected"):
        VerificationResult.model_validate(document)


def test_source_verification_with_evidence_inspected_succeeds() -> None:
    document = _base_document(
        verification_type="source",
        evidence_inspected=["urn:mrr:evidence-anchor:01J00000000000000000000018"],
    )

    _validate_against_schema(document)
    result = VerificationResult.model_validate(document)

    assert result.evidence_inspected == ["urn:mrr:evidence-anchor:01J00000000000000000000018"]


def test_numeric_verification_requires_non_null_numeric_recomputation() -> None:
    """MRR-FR-073: 'Numeric verification MUST recompute the value or
    explicitly record why recomputation is impossible' — a 'numeric'
    verification_type with a null numeric_recomputation fails closed, both
    at the schema level and the model level.
    """
    document = _base_document(verification_type="numeric", numeric_recomputation=None)

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="numeric_recomputation"):
        VerificationResult.model_validate(document)


def test_numeric_verification_with_recomputed_value_succeeds() -> None:
    document = _base_document(
        verification_type="numeric",
        numeric_recomputation={"recomputed_value": 42.0, "matches_claimed_value": True},
    )

    _validate_against_schema(document)
    result = VerificationResult.model_validate(document)

    assert result.numeric_recomputation is not None
    assert result.numeric_recomputation.recomputed_value == 42.0


def test_numeric_verification_with_impossible_reason_succeeds() -> None:
    """MRR-FR-073's escape: recomputation can be explicitly declared
    impossible instead of performed."""
    document = _base_document(
        verification_type="numeric",
        numeric_recomputation={"impossible_reason": "Original raw dataset was not preserved."},
    )

    _validate_against_schema(document)
    result = VerificationResult.model_validate(document)

    assert result.numeric_recomputation is not None
    assert result.numeric_recomputation.recomputed_value is None
    assert result.numeric_recomputation.impossible_reason == (
        "Original raw dataset was not preserved."
    )


def test_numeric_recomputation_with_neither_value_nor_reason_rejected() -> None:
    document = _base_document(
        verification_type="numeric",
        numeric_recomputation={"matches_claimed_value": False},
    )

    with pytest.raises(ValidationError, match="numeric_recomputation"):
        VerificationResult.model_validate(document)


# ---------------------------------------------------------------------------
# independence_profile: all six MRR-FR-071 dimensions are required.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dimension",
    [
        "principal",
        "model_family",
        "prompt_family",
        "retrieval_path",
        "code_path",
        "data_access_path",
    ],
)
def test_independence_profile_missing_any_single_dimension_rejected(dimension: str) -> None:
    profile = _independence_profile()
    del profile[dimension]
    document = _base_document(independence_profile=profile)

    with pytest.raises(ValidationError, match="independence_profile"):
        VerificationResult.model_validate(document)


def test_independence_profile_with_all_six_dimensions_succeeds() -> None:
    document = _base_document()

    _validate_against_schema(document)
    result = VerificationResult.model_validate(document)

    assert result.independence_profile.principal == "urn:mrr:person:01J00000000000000000000036"


# ---------------------------------------------------------------------------
# confidence: bounded [0, 1] — the reviewer's own confidence, not epistemic
# truth (mirrors SourceFamily.confidence's own guard).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_confidence_out_of_bounds_rejected(confidence: float) -> None:
    document = _base_document(confidence=confidence)

    with pytest.raises(ValidationError, match="confidence"):
        VerificationResult.model_validate(document)


# ---------------------------------------------------------------------------
# findings: severity vocabulary reused from CorrectionEvent.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("severity", ["minor", "material", "critical"])
def test_each_finding_severity_validates(severity: str) -> None:
    document = _base_document(
        findings=[{"severity": severity, "statement": "A fixture finding statement."}]
    )

    _validate_against_schema(document)
    result = VerificationResult.model_validate(document)

    assert result.findings[0].severity == severity


def test_unknown_finding_severity_rejected() -> None:
    document = _base_document(
        findings=[{"severity": "catastrophic", "statement": "A fixture finding statement."}]
    )

    with pytest.raises(ValidationError, match="severity"):
        VerificationResult.model_validate(document)


# ---------------------------------------------------------------------------
# adjudication_relation: nullable, links a review to one it adjudicates.
# ---------------------------------------------------------------------------


def test_adjudication_relation_accepts_a_urn() -> None:
    document = _base_document(
        adjudication_relation="urn:mrr:verification:01J00000000000000000000037"
    )

    _validate_against_schema(document)
    result = VerificationResult.model_validate(document)

    assert result.adjudication_relation == "urn:mrr:verification:01J00000000000000000000037"
