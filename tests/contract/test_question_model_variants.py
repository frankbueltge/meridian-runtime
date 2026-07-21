"""Contract tests for ``QuestionModel`` (task-packets/K1-T01.yaml) beyond
the generic example-driven checks tests/contract/test_examples.py already
runs.

The unknown-claim-type failure case is covered by
tests/contract/fixtures/invalid/question-model-unknown-claim-type.json via
tests/contract/test_negative_fixtures.py, not duplicated here.

Acceptance-test mapping (task-packets/K1-T01.yaml):

- "QuestionModel — a document missing raw_question is rejected at
  construction" -> ``test_missing_raw_question_rejected_at_construction``.
- "claim_type_sought accepts only ClaimType's seven values" ->
  ``test_every_claim_type_sought_value_is_accepted``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from mrr.contracts.question_model import QuestionModel
from pydantic import ValidationError

from scripts.check_contracts import SCHEMAS_DIR, build_registry, build_validator_for_schema

_VALID_HASH = "sha256:" + "1" * 64


def _base_document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "id": "urn:mrr:question-model:01J00000000000000000000210",
        "api_version": "mrr/v1alpha1",
        "kind": "QuestionModel",
        "practice_id": "urn:mrr:practice:01J00000000000000000000001",
        "revision": 1,
        "created_at": "2026-07-21T09:00:00Z",
        "created_by": "urn:mrr:person:01J00000000000000000000002",
        "content_hash": _VALID_HASH,
        "raw_question": "Does this work instantiate the mechanism?",
        "claim_type_sought": "interpretive",
        "scope": {"population": None, "time": None, "geography": None, "conditions": []},
        "load_bearing_terms": [],
        "status": "draft",
    }
    document.update(overrides)
    return document


def _validate_against_schema(document: dict[str, Any]) -> None:
    schema = json.loads((SCHEMAS_DIR / "question-model.schema.json").read_text())
    registry = build_registry()
    build_validator_for_schema(schema, registry).validate(document)


def test_missing_raw_question_rejected_at_construction() -> None:
    document = _base_document()
    del document["raw_question"]

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="raw_question"):
        QuestionModel.model_validate(document)


def test_empty_raw_question_rejected_at_construction() -> None:
    document = _base_document(raw_question="")

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="raw_question"):
        QuestionModel.model_validate(document)


@pytest.mark.parametrize(
    "claim_type",
    [
        "observational",
        "causal",
        "statistical",
        "methodological",
        "interpretive",
        "normative",
        "speculative",
    ],
)
def test_every_claim_type_sought_value_is_accepted(claim_type: str) -> None:
    document = _base_document(claim_type_sought=claim_type)

    _validate_against_schema(document)
    model = QuestionModel.model_validate(document)

    assert model.claim_type_sought == claim_type


def test_unrecognized_claim_type_sought_rejected() -> None:
    document = _base_document(claim_type_sought="prophetic")

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="claim_type_sought"):
        QuestionModel.model_validate(document)


def test_empty_load_bearing_terms_is_accepted() -> None:
    document = _base_document(load_bearing_terms=[])

    _validate_against_schema(document)
    model = QuestionModel.model_validate(document)

    assert model.load_bearing_terms == []
