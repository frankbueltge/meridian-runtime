"""Contract tests for ``ResearchDecision`` (task-packets/K1-T01.yaml) beyond
the generic example-driven checks tests/contract/test_examples.py already
runs.

The unknown-decision-type failure case is covered by
tests/contract/fixtures/invalid/research-decision-unknown-decision-type.json
via tests/contract/test_negative_fixtures.py, not duplicated here.

Acceptance-test mapping (task-packets/K1-T01.yaml):

- "decision_type: 'stop_insufficient_evidence' validates with the identical
  required-field set as every other decision_type value (no special-cased
  field)" -> ``test_every_decision_type_validates_with_the_identical_field_set``.
- "an unrecognized decision_type string is rejected; status accepts only
  'issued'" -> ``test_unrecognized_decision_type_rejected``,
  ``test_status_accepts_only_issued``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from mrr.contracts.research_decision import ResearchDecision
from pydantic import ValidationError

from scripts.check_contracts import SCHEMAS_DIR, build_registry, build_validator_for_schema

_VALID_HASH = "sha256:" + "6" * 64

_ALL_DECISION_TYPES = [
    "continue",
    "revise",
    "narrow_scope",
    "kill_branch",
    "replicate",
    "escalate_human_review",
    "stop_insufficient_evidence",
]


def _base_document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "id": "urn:mrr:research-decision:01J00000000000000000000260",
        "api_version": "mrr/v1alpha1",
        "kind": "ResearchDecision",
        "practice_id": "urn:mrr:practice:01J00000000000000000000001",
        "revision": 1,
        "created_at": "2026-07-21T11:00:00Z",
        "created_by": "urn:mrr:person:01J00000000000000000000002",
        "content_hash": _VALID_HASH,
        "decision_type": "continue",
        "protocol_id": "urn:mrr:method-protocol:01J00000000000000000000230",
        "applies_to_analysis": "instantiation-vs-reference-classification",
        "rationale": "Fixture rationale.",
        "status": "issued",
    }
    document.update(overrides)
    return document


def _validate_against_schema(document: dict[str, Any]) -> None:
    schema = json.loads((SCHEMAS_DIR / "research-decision.schema.json").read_text())
    registry = build_registry()
    build_validator_for_schema(schema, registry).validate(document)


@pytest.mark.parametrize("decision_type", _ALL_DECISION_TYPES)
def test_every_decision_type_validates_with_the_identical_field_set(decision_type: str) -> None:
    """No decision_type value requires any extra field — stop_insufficient_evidence
    (MRR-MTH-011) validates identically to every other value.
    """
    document = _base_document(decision_type=decision_type)

    _validate_against_schema(document)
    decision = ResearchDecision.model_validate(document)

    assert decision.decision_type == decision_type


def test_stop_insufficient_evidence_is_not_rendered_as_an_error() -> None:
    """MRR-MTH-011: stop_insufficient_evidence is a successful terminal
    result, not an error — this contract-level check confirms it validates
    with no distinguishing flag or extra requirement.
    """
    document = _base_document(decision_type="stop_insufficient_evidence")

    _validate_against_schema(document)
    decision = ResearchDecision.model_validate(document)

    assert decision.decision_type == "stop_insufficient_evidence"
    assert decision.status == "issued"


def test_unrecognized_decision_type_rejected() -> None:
    document = _base_document(decision_type="abandon_forever")

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="decision_type"):
        ResearchDecision.model_validate(document)


def test_status_accepts_only_issued() -> None:
    document = _base_document(status="draft")

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="status"):
        ResearchDecision.model_validate(document)


def test_missing_rationale_rejected() -> None:
    document = _base_document()
    del document["rationale"]

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="rationale"):
        ResearchDecision.model_validate(document)
