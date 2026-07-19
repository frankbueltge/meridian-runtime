"""Contract tests for ``ModelInvocation`` (task-packets/E4-T01.yaml) beyond
the generic example-driven checks tests/contract/test_examples.py already
runs.

examples/model-invocation.example.json uses a single ``status`` value
("completed") under the default ``"hashes_only"`` redaction policy. This
module covers the remaining terminal-status vocabulary, the
response_hash/status and redaction/raw-text biconditionals, the tool_calls
result_hash/status biconditional, and the proposal-only shape gate inline
(adding more files under examples/ would break test_examples.py's "exactly
one example per schema" check) — mirroring
tests/contract/test_verification_result_variants.py's own precedent.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from mrr.contracts.model_invocation import ModelInvocation, TerminalStatus
from pydantic import ValidationError

from scripts.check_contracts import SCHEMAS_DIR, build_registry, build_validator_for_schema

_VALID_HASH = "sha256:" + "9" * 64
_OTHER_HASH = "sha256:" + "8" * 64

_ALL_STATUSES: tuple[TerminalStatus, ...] = (
    "completed",
    "refused",
    "content_filtered",
    "error",
    "timed_out",
)


def _base_document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "id": "urn:mrr:model-invocation:01J00000000000000000000041",
        "api_version": "mrr/v1alpha1",
        "kind": "ModelInvocation",
        "practice_id": "urn:mrr:practice:01J00000000000000000000001",
        "revision": 1,
        "created_at": "2026-07-19T09:05:00Z",
        "created_by": "urn:mrr:executor:01J00000000000000000000017",
        "content_hash": _VALID_HASH,
        "model_profile_id": "urn:mrr:model-profile:01J00000000000000000000040",
        "model_profile_hash": _VALID_HASH,
        "operation_kind": "deterministic",
        "prompt_config_hash": _VALID_HASH,
        "token_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "tool_calls": [],
        "response_hash": _VALID_HASH,
        "status": "completed",
        "redaction_policy": "hashes_only",
    }
    document.update(overrides)
    return document


def _validate_against_schema(document: dict[str, Any]) -> None:
    schema = json.loads((SCHEMAS_DIR / "model-invocation.schema.json").read_text())
    registry = build_registry()
    build_validator_for_schema(schema, registry).validate(document)


# ---------------------------------------------------------------------------
# status: five DISTINCT terminal values, never collapsed into one.
# ---------------------------------------------------------------------------


def test_completed_status_with_a_response_hash_succeeds() -> None:
    document = _base_document(status="completed", response_hash=_VALID_HASH)

    _validate_against_schema(document)
    invocation = ModelInvocation.model_validate(document)

    assert invocation.status == "completed"
    assert invocation.response_hash == _VALID_HASH


@pytest.mark.parametrize("status", ["refused", "content_filtered", "error", "timed_out"])
def test_non_completed_status_with_no_response_hash_succeeds(status: str) -> None:
    document = _base_document(status=status)
    del document["response_hash"]

    _validate_against_schema(document)
    invocation = ModelInvocation.model_validate(document)

    assert invocation.status == status
    assert invocation.response_hash is None


def test_completed_status_without_a_response_hash_rejected() -> None:
    document = _base_document(status="completed")
    del document["response_hash"]

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="response_hash"):
        ModelInvocation.model_validate(document)


@pytest.mark.parametrize("status", ["refused", "content_filtered", "error", "timed_out"])
def test_non_completed_status_with_a_response_hash_rejected(status: str) -> None:
    document = _base_document(status=status, response_hash=_VALID_HASH)

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="response_hash"):
        ModelInvocation.model_validate(document)


def test_refused_is_never_recorded_as_completed() -> None:
    """A refused call and a completed call are distinct statuses; nothing
    about a 'refused' record resembles an accepted response (task-packets/
    E4-T01.yaml invariant).
    """
    document = _base_document(status="refused")
    del document["response_hash"]

    invocation = ModelInvocation.model_validate(document)

    assert invocation.status != "completed"
    assert invocation.response_hash is None


def test_unknown_status_rejected_at_model_level() -> None:
    document = _base_document(status="mostly_fine")

    with pytest.raises(ValidationError, match="status"):
        ModelInvocation.model_validate(document)


# ---------------------------------------------------------------------------
# operation_kind: required, closed two-value vocabulary.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("operation_kind", ["deterministic", "stochastic"])
def test_each_operation_kind_validates(operation_kind: str) -> None:
    document = _base_document(operation_kind=operation_kind)

    _validate_against_schema(document)
    invocation = ModelInvocation.model_validate(document)

    assert invocation.operation_kind == operation_kind


def test_missing_operation_kind_rejected() -> None:
    document = _base_document()
    del document["operation_kind"]

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)
    with pytest.raises(ValidationError, match="operation_kind"):
        ModelInvocation.model_validate(document)


def test_unknown_operation_kind_rejected_at_model_level() -> None:
    document = _base_document(operation_kind="somewhat_random")

    with pytest.raises(ValidationError, match="operation_kind"):
        ModelInvocation.model_validate(document)


# ---------------------------------------------------------------------------
# redaction_policy: no implied default; hashes_only forbids raw text.
# ---------------------------------------------------------------------------


def test_hashes_only_with_no_raw_text_succeeds() -> None:
    document = _base_document(redaction_policy="hashes_only")

    _validate_against_schema(document)
    invocation = ModelInvocation.model_validate(document)

    assert invocation.raw_prompt_text is None
    assert invocation.raw_response_text is None


def test_hashes_only_with_raw_prompt_text_rejected() -> None:
    document = _base_document(redaction_policy="hashes_only", raw_prompt_text="the real prompt")

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)
    with pytest.raises(ValidationError, match="hashes_only"):
        ModelInvocation.model_validate(document)


def test_hashes_only_with_raw_response_text_rejected() -> None:
    document = _base_document(redaction_policy="hashes_only", raw_response_text="the real response")

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)
    with pytest.raises(ValidationError, match="hashes_only"):
        ModelInvocation.model_validate(document)


def test_raw_permitted_with_raw_text_succeeds() -> None:
    document = _base_document(
        redaction_policy="raw_permitted",
        raw_prompt_text="the real prompt",
        raw_response_text="the real response",
    )

    _validate_against_schema(document)
    invocation = ModelInvocation.model_validate(document)

    assert invocation.raw_prompt_text == "the real prompt"
    assert invocation.raw_response_text == "the real response"


def test_missing_redaction_policy_rejected() -> None:
    document = _base_document()
    del document["redaction_policy"]

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)
    with pytest.raises(ValidationError, match="redaction_policy"):
        ModelInvocation.model_validate(document)


# ---------------------------------------------------------------------------
# tool_calls: name + argument/result hashes + its own status.
# ---------------------------------------------------------------------------


def test_completed_tool_call_with_result_hash_succeeds() -> None:
    document = _base_document(
        tool_calls=[
            {
                "name": "web_search",
                "arguments_hash": _VALID_HASH,
                "result_hash": _OTHER_HASH,
                "status": "completed",
            }
        ]
    )

    _validate_against_schema(document)
    invocation = ModelInvocation.model_validate(document)

    assert invocation.tool_calls[0].result_hash == _OTHER_HASH


@pytest.mark.parametrize("status", ["refused", "error", "timed_out"])
def test_non_completed_tool_call_without_result_hash_succeeds(status: str) -> None:
    document = _base_document(
        tool_calls=[{"name": "web_search", "arguments_hash": _VALID_HASH, "status": status}]
    )

    _validate_against_schema(document)
    invocation = ModelInvocation.model_validate(document)

    assert invocation.tool_calls[0].result_hash is None


def test_completed_tool_call_without_result_hash_rejected() -> None:
    document = _base_document(
        tool_calls=[{"name": "web_search", "arguments_hash": _VALID_HASH, "status": "completed"}]
    )

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)
    with pytest.raises(ValidationError, match="result_hash"):
        ModelInvocation.model_validate(document)


@pytest.mark.parametrize("status", ["refused", "error", "timed_out"])
def test_non_completed_tool_call_with_result_hash_rejected(status: str) -> None:
    document = _base_document(
        tool_calls=[
            {
                "name": "web_search",
                "arguments_hash": _VALID_HASH,
                "result_hash": _OTHER_HASH,
                "status": status,
            }
        ]
    )

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)
    with pytest.raises(ValidationError, match="result_hash"):
        ModelInvocation.model_validate(document)


def test_empty_tool_calls_succeeds() -> None:
    document = _base_document(tool_calls=[])

    _validate_against_schema(document)
    invocation = ModelInvocation.model_validate(document)

    assert invocation.tool_calls == []


# ---------------------------------------------------------------------------
# Proposal-only shape (MRR-FR-046, AGENTS.md rule 7): no claim status, no
# verification verdict, no authoritative-acceptance field of any kind.
# ---------------------------------------------------------------------------


def test_model_invocation_has_no_claim_or_verification_or_acceptance_field() -> None:
    forbidden_field_names = {
        "claim_status",
        "verification_verdict",
        "verification_status",
        "accepted",
        "is_accepted",
        "authoritative",
        "recommendation",
    }
    assert forbidden_field_names.isdisjoint(ModelInvocation.model_fields)


def test_model_invocation_schema_declares_no_such_property_either() -> None:
    schema = json.loads((SCHEMAS_DIR / "model-invocation.schema.json").read_text())
    declared_properties = set(schema["allOf"][1]["properties"])
    forbidden_field_names = {
        "claim_status",
        "verification_verdict",
        "verification_status",
        "accepted",
        "is_accepted",
        "authoritative",
    }
    assert forbidden_field_names.isdisjoint(declared_properties)
