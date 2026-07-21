"""Contract tests for ``MethodRuling`` (task-packets/K1-T01.yaml) beyond
the generic example-driven checks tests/contract/test_examples.py already
runs.

The unknown-ceiling and non-applicability-empty-above-descriptive failure
cases are covered by tests/contract/fixtures/invalid/method-ruling-*.json
via tests/contract/test_negative_fixtures.py, not duplicated here.

Acceptance-test mapping (task-packets/K1-T01.yaml):

- "ruled_ceiling accepts all seven CLAIM_CEILING_ORDER values (imported
  from mrr.contracts.method_profile, not redeclared)" ->
  ``test_all_seven_ceiling_values_are_accepted``.
- "ruled_ceiling: 'descriptive' with an empty list is accepted;
  'associational_unadjusted' with a non-empty list is accepted" ->
  ``test_descriptive_or_below_with_empty_non_applicability_is_accepted``,
  ``test_above_descriptive_with_non_empty_non_applicability_is_accepted``.
- "ruling_basis: 'deterministic_rule' with a non-null human_review (or a
  null deterministic_rule_reference) is rejected; 'human_review' with a
  non-null deterministic_rule_reference (or a null human_review) is
  rejected; each basis with exactly its own matching field populated is
  accepted" -> the ``test_ruling_basis_*`` group below.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from mrr.contracts.method_profile import CLAIM_CEILING_ORDER
from mrr.contracts.method_ruling import MethodRuling
from pydantic import ValidationError

from scripts.check_contracts import SCHEMAS_DIR, build_registry, build_validator_for_schema

_VALID_HASH = "sha256:" + "5" * 64
_HUMAN_REVIEW = {
    "reviewer_id": "urn:mrr:person:01J00000000000000000000004",
    "review_note": "Fixture.",
}


def _base_document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "id": "urn:mrr:method-ruling:01J00000000000000000000250",
        "api_version": "mrr/v1alpha1",
        "kind": "MethodRuling",
        "practice_id": "urn:mrr:practice:01J00000000000000000000001",
        "revision": 1,
        "created_at": "2026-07-21T10:30:00Z",
        "created_by": "urn:mrr:person:01J00000000000000000000002",
        "content_hash": _VALID_HASH,
        "ruled_ceiling": "descriptive",
        "scope_of_validity": {
            "population": None,
            "time": None,
            "geography": None,
            "conditions": [],
        },
        "non_applicability_conditions": [],
        "ruling_basis": "human_review",
        "deterministic_rule_reference": None,
        "human_review": _HUMAN_REVIEW,
        "issued_by": "urn:mrr:person:01J00000000000000000000004",
        "protocol_id": "urn:mrr:method-protocol:01J00000000000000000000230",
        "applies_to_analysis": "instantiation-vs-reference-classification",
        "status": "issued",
    }
    document.update(overrides)
    return document


def _validate_against_schema(document: dict[str, Any]) -> None:
    schema = json.loads((SCHEMAS_DIR / "method-ruling.schema.json").read_text())
    registry = build_registry()
    build_validator_for_schema(schema, registry).validate(document)


@pytest.mark.parametrize("ceiling", list(CLAIM_CEILING_ORDER))
def test_all_seven_ceiling_values_are_accepted(ceiling: str) -> None:
    non_applicability = (
        []
        if CLAIM_CEILING_ORDER.index(ceiling) <= CLAIM_CEILING_ORDER.index("descriptive")
        else ["does not apply to unverifiable-provenance sources"]
    )
    document = _base_document(ruled_ceiling=ceiling, non_applicability_conditions=non_applicability)

    _validate_against_schema(document)
    ruling = MethodRuling.model_validate(document)

    assert ruling.ruled_ceiling == ceiling


def test_descriptive_or_below_with_empty_non_applicability_is_accepted() -> None:
    document = _base_document(ruled_ceiling="descriptive", non_applicability_conditions=[])

    _validate_against_schema(document)
    ruling = MethodRuling.model_validate(document)

    assert ruling.non_applicability_conditions == []


def test_above_descriptive_with_non_empty_non_applicability_is_accepted() -> None:
    document = _base_document(
        ruled_ceiling="associational_unadjusted",
        non_applicability_conditions=["does not license causal language"],
    )

    _validate_against_schema(document)
    ruling = MethodRuling.model_validate(document)

    assert ruling.non_applicability_conditions == ["does not license causal language"]


def test_ruling_basis_deterministic_rule_with_matching_field_is_accepted() -> None:
    document = _base_document(
        ruling_basis="deterministic_rule",
        deterministic_rule_reference="mrr.method.systematic_evidence_synthesis/1#ceiling-rule-3",
        human_review=None,
    )

    _validate_against_schema(document)
    ruling = MethodRuling.model_validate(document)

    assert ruling.ruling_basis == "deterministic_rule"
    assert ruling.human_review is None


def test_ruling_basis_deterministic_rule_with_non_null_human_review_rejected() -> None:
    document = _base_document(
        ruling_basis="deterministic_rule",
        deterministic_rule_reference="mrr.method.systematic_evidence_synthesis/1#ceiling-rule-3",
        human_review=_HUMAN_REVIEW,
    )

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="human_review"):
        MethodRuling.model_validate(document)


def test_ruling_basis_deterministic_rule_with_null_reference_rejected() -> None:
    document = _base_document(
        ruling_basis="deterministic_rule",
        deterministic_rule_reference=None,
        human_review=None,
    )

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="deterministic_rule_reference"):
        MethodRuling.model_validate(document)


def test_ruling_basis_human_review_with_non_null_deterministic_reference_rejected() -> None:
    document = _base_document(
        ruling_basis="human_review",
        deterministic_rule_reference="mrr.method.systematic_evidence_synthesis/1#ceiling-rule-3",
        human_review=_HUMAN_REVIEW,
    )

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="deterministic_rule_reference"):
        MethodRuling.model_validate(document)


def test_ruling_basis_human_review_with_null_human_review_rejected() -> None:
    document = _base_document(
        ruling_basis="human_review",
        deterministic_rule_reference=None,
        human_review=None,
    )

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="human_review"):
        MethodRuling.model_validate(document)
