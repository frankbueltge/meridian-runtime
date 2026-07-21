"""Contract tests for ``MethodProtocol`` (task-packets/K1-T01.yaml) beyond
the generic example-driven checks tests/contract/test_examples.py already
runs.

The missing-locked-at and missing-amendment failure cases are covered by
tests/contract/fixtures/invalid/method-protocol-*.json via
tests/contract/test_negative_fixtures.py, not duplicated here. The
randomly-generated field-presence property test lives in
tests/property/test_method_protocol_properties.py (Hypothesis-based,
mirroring that directory's own convention).

Acceptance-test mapping (task-packets/K1-T01.yaml):

- "every legal co-occurrence (draft/reviewed with both null; locked/
  amended/executed with both non-null) is accepted" ->
  ``test_every_legal_status_and_lock_field_combination_is_accepted``.
- "status: 'amended' with a fully-populated amendment block is accepted" ->
  ``test_amended_status_with_fully_populated_amendment_is_accepted``.
- "an empty kill_conditions list is rejected; an empty planned_analyses
  list is rejected" -> ``test_empty_kill_conditions_rejected``,
  ``test_empty_planned_analyses_rejected``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from mrr.contracts.method_protocol import MethodProtocol, MethodProtocolStatus
from pydantic import ValidationError

from scripts.check_contracts import SCHEMAS_DIR, build_registry, build_validator_for_schema

_VALID_HASH = "sha256:" + "3" * 64
_AMENDMENT_HASH = "sha256:" + "3" * 63 + "4"


def _base_document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "id": "urn:mrr:method-protocol:01J00000000000000000000230",
        "api_version": "mrr/v1alpha1",
        "kind": "MethodProtocol",
        "practice_id": "urn:mrr:practice:01J00000000000000000000001",
        "revision": 1,
        "created_at": "2026-07-21T09:10:00Z",
        "created_by": "urn:mrr:person:01J00000000000000000000002",
        "content_hash": _VALID_HASH,
        "profile_id": "urn:mrr:method-profile:01J00000000000000000000110",
        "extraction_fields": ["claim_relevant_finding"],
        "inclusion_criteria": ["provenance-verified"],
        "exclusion_criteria": [],
        "sensitivity_variations": [],
        "planned_analyses": ["instantiation-vs-reference-classification"],
        "kill_conditions": ["fewer than 5 included sources -> stop_insufficient_evidence"],
        "locked_at": None,
        "locked_by": None,
        "amendment": None,
        "status": "draft",
    }
    document.update(overrides)
    return document


def _validate_against_schema(document: dict[str, Any]) -> None:
    schema = json.loads((SCHEMAS_DIR / "method-protocol.schema.json").read_text())
    registry = build_registry()
    build_validator_for_schema(schema, registry).validate(document)


@pytest.mark.parametrize(
    "status",
    ["draft", "reviewed"],
)
def test_unlocked_status_with_null_lock_fields_is_accepted(status: MethodProtocolStatus) -> None:
    document = _base_document(status=status, locked_at=None, locked_by=None)

    _validate_against_schema(document)
    protocol = MethodProtocol.model_validate(document)

    assert protocol.status == status
    assert protocol.locked_at is None
    assert protocol.locked_by is None


@pytest.mark.parametrize(
    "status",
    ["locked", "executed"],
)
def test_locked_status_with_non_null_lock_fields_is_accepted(status: MethodProtocolStatus) -> None:
    document = _base_document(
        status=status,
        locked_at="2026-07-21T09:30:00Z",
        locked_by="urn:mrr:person:01J00000000000000000000002",
    )

    _validate_against_schema(document)
    protocol = MethodProtocol.model_validate(document)

    assert protocol.status == status
    assert protocol.locked_at is not None
    assert protocol.locked_by is not None


def test_every_legal_status_and_lock_field_combination_is_accepted() -> None:
    for status in ("draft", "reviewed"):
        document = _base_document(status=status, locked_at=None, locked_by=None)
        _validate_against_schema(document)
        MethodProtocol.model_validate(document)

    for status in ("locked", "executed"):
        document = _base_document(
            status=status,
            locked_at="2026-07-21T09:30:00Z",
            locked_by="urn:mrr:person:01J00000000000000000000002",
        )
        _validate_against_schema(document)
        MethodProtocol.model_validate(document)

    amended_document = _base_document(
        status="amended",
        locked_at="2026-07-21T09:30:00Z",
        locked_by="urn:mrr:person:01J00000000000000000000002",
        amendment={
            "reason": "outcome-informed correction",
            "actor": "urn:mrr:person:01J00000000000000000000002",
            "amended_at": "2026-07-21T12:00:00Z",
            "outcome_information_observed": True,
            "amended_locked_content_hash": _VALID_HASH,
        },
    )
    _validate_against_schema(amended_document)
    MethodProtocol.model_validate(amended_document)


def test_amended_status_with_fully_populated_amendment_is_accepted() -> None:
    document = _base_document(
        status="amended",
        locked_at="2026-07-21T09:30:00Z",
        locked_by="urn:mrr:person:01J00000000000000000000002",
        amendment={
            "reason": "outcome-informed correction to extraction_fields",
            "actor": "urn:mrr:person:01J00000000000000000000002",
            "amended_at": "2026-07-21T12:00:00Z",
            "outcome_information_observed": True,
            "amended_locked_content_hash": _VALID_HASH,
        },
    )

    _validate_against_schema(document)
    protocol = MethodProtocol.model_validate(document)

    assert protocol.amendment is not None
    assert protocol.amendment.outcome_information_observed is True


def test_locked_with_null_locked_at_rejected() -> None:
    document = _base_document(
        status="locked", locked_at=None, locked_by="urn:mrr:person:01J00000000000000000000002"
    )

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="locked_at"):
        MethodProtocol.model_validate(document)


def test_locked_with_null_locked_by_rejected() -> None:
    document = _base_document(status="locked", locked_at="2026-07-21T09:30:00Z", locked_by=None)

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="locked_by"):
        MethodProtocol.model_validate(document)


def test_draft_with_non_null_locked_at_rejected() -> None:
    document = _base_document(status="draft", locked_at="2026-07-21T09:30:00Z", locked_by=None)

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError):
        MethodProtocol.model_validate(document)


def test_locked_with_non_null_amendment_rejected() -> None:
    document = _base_document(
        status="locked",
        locked_at="2026-07-21T09:30:00Z",
        locked_by="urn:mrr:person:01J00000000000000000000002",
        amendment={
            "reason": "should not be here",
            "actor": "urn:mrr:person:01J00000000000000000000002",
            "amended_at": "2026-07-21T12:00:00Z",
            "outcome_information_observed": True,
            "amended_locked_content_hash": _VALID_HASH,
        },
    )

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="amendment"):
        MethodProtocol.model_validate(document)


def test_empty_kill_conditions_rejected() -> None:
    document = _base_document(kill_conditions=[])

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="kill_conditions"):
        MethodProtocol.model_validate(document)


def test_empty_planned_analyses_rejected() -> None:
    document = _base_document(planned_analyses=[])

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="planned_analyses"):
        MethodProtocol.model_validate(document)
