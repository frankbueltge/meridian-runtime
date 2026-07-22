"""Contract tests for ``ReleaseRecord`` (task-packets/E8-T04.yaml) beyond the
generic example-driven checks tests/contract/test_examples.py already runs.

The malformed-approver and unknown-status failure cases are covered by
tests/contract/fixtures/invalid/release-record-*.json via
tests/contract/test_negative_fixtures.py, not duplicated here.

Acceptance-test mapping (task-packets/E8-T04.yaml R1/R5):

- "approved_by person-URN pattern-enforced in the schema AND contract
  validator" -> ``test_person_urn_pattern_accepted_at_both_layers``,
  ``test_non_person_urn_rejected_at_both_layers``.
- "non-empty approval_statement" ->
  ``test_empty_approval_statement_rejected_at_both_layers``.
- "approval_mode single_human|dual" (schema-valid; SERVICE refusal for
  "dual" is unit-tier, mrr.services.release) ->
  ``test_both_approval_modes_are_schema_and_contract_valid``.
- "bundle.files: sorted [{path, sha256}] + root_hash" ->
  ``test_bundle_files_must_be_sorted_by_path``,
  ``test_bundle_files_must_not_contain_duplicate_paths``.
- status released|superseded -> ``test_status_accepts_only_released_or_superseded``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from mrr.contracts.release_record import ReleaseRecord
from pydantic import ValidationError

from scripts.check_contracts import SCHEMAS_DIR, build_registry, build_validator_for_schema

_VALID_HASH = "sha256:" + "6" * 64


def _bundle_file(path: str, digit: str = "1") -> dict[str, Any]:
    return {"path": path, "sha256": "sha256:" + digit * 64}


def _base_document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "id": "urn:mrr:release-record:01J00000000000000000000280",
        "api_version": "mrr/v1alpha1",
        "kind": "ReleaseRecord",
        "practice_id": "urn:mrr:practice:01J00000000000000000000001",
        "revision": 1,
        "created_at": "2026-07-22T11:00:00Z",
        "created_by": "urn:mrr:person:01J00000000000000000000041",
        "content_hash": _VALID_HASH,
        "crate_id": "urn:mrr:evidence-crate:01J00000000000000000000012",
        "disclosure": "internal",
        "bundle": {
            "files": [_bundle_file("report.html"), _bundle_file("report.md", "2")],
            "root_hash": "sha256:" + "3" * 64,
        },
        "approval": {
            "approved_by": "urn:mrr:person:01J00000000000000000000041",
            "approval_statement": "Approving this release.",
            "approval_mode": "single_human",
        },
        "status": "released",
    }
    document.update(overrides)
    return document


def _validate_against_schema(document: dict[str, Any]) -> None:
    schema = json.loads((SCHEMAS_DIR / "release-record.schema.json").read_text())
    registry = build_registry()
    build_validator_for_schema(schema, registry).validate(document)


# ---------------------------------------------------------------------------
# approved_by: person-URN pattern, enforced at BOTH layers.
# ---------------------------------------------------------------------------


def test_person_urn_pattern_accepted_at_both_layers() -> None:
    document = _base_document()

    _validate_against_schema(document)
    record = ReleaseRecord.model_validate(document)

    assert record.approval.approved_by == document["approval"]["approved_by"]


@pytest.mark.parametrize(
    "non_person_approver",
    [
        "urn:mrr:agent-role:01J00000000000000000000041",
        "urn:mrr:node:01J00000000000000000000041",
        "urn:mrr:practice:01J00000000000000000000041",
        "not-a-urn-at-all",
    ],
)
def test_non_person_urn_rejected_at_both_layers(non_person_approver: str) -> None:
    document = _base_document()
    document["approval"]["approved_by"] = non_person_approver

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="approved_by"):
        ReleaseRecord.model_validate(document)


# ---------------------------------------------------------------------------
# approval_statement: non-empty.
# ---------------------------------------------------------------------------


def test_empty_approval_statement_rejected_at_both_layers() -> None:
    document = _base_document()
    document["approval"]["approval_statement"] = ""

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="approval_statement"):
        ReleaseRecord.model_validate(document)


# ---------------------------------------------------------------------------
# approval_mode: single_human|dual, both schema/contract-VALID (the "dual"
# service refusal is derived_decisions (b), asserted at the unit tier
# against mrr.services.release.service.ReleaseService, not here).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("approval_mode", ["single_human", "dual"])
def test_both_approval_modes_are_schema_and_contract_valid(approval_mode: str) -> None:
    document = _base_document()
    document["approval"]["approval_mode"] = approval_mode

    _validate_against_schema(document)
    record = ReleaseRecord.model_validate(document)

    assert record.approval.approval_mode == approval_mode


def test_unrecognized_approval_mode_rejected() -> None:
    document = _base_document()
    document["approval"]["approval_mode"] = "automatic"

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="approval_mode"):
        ReleaseRecord.model_validate(document)


# ---------------------------------------------------------------------------
# bundle.files: sorted, no duplicate paths (contract-level model_validator;
# not itself expressible as a plain JSON Schema array-order constraint).
# ---------------------------------------------------------------------------


def test_bundle_files_must_be_sorted_by_path() -> None:
    document = _base_document(
        bundle={
            "files": [_bundle_file("report.md", "2"), _bundle_file("report.html", "1")],
            "root_hash": "sha256:" + "3" * 64,
        }
    )

    # Schema-valid (JSON Schema cannot express array-order sortedness) ...
    _validate_against_schema(document)

    # ... but rejected by the Pydantic contract's own model_validator.
    with pytest.raises(ValidationError, match="sorted"):
        ReleaseRecord.model_validate(document)


def test_bundle_files_must_not_contain_duplicate_paths() -> None:
    document = _base_document(
        bundle={
            "files": [_bundle_file("report.md", "1"), _bundle_file("report.md", "2")],
            "root_hash": "sha256:" + "3" * 64,
        }
    )

    with pytest.raises(ValidationError, match="duplicate"):
        ReleaseRecord.model_validate(document)


def test_bundle_files_sorted_and_unique_round_trips() -> None:
    document = _base_document()
    record = ReleaseRecord.model_validate(document)

    dumped = json.loads(record.model_dump_json(exclude_none=True))
    _validate_against_schema(dumped)
    assert ReleaseRecord.model_validate(dumped) == record


# ---------------------------------------------------------------------------
# status: released|superseded.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["released", "superseded"])
def test_status_accepts_only_released_or_superseded(status: str) -> None:
    document = _base_document(status=status)

    _validate_against_schema(document)
    record = ReleaseRecord.model_validate(document)

    assert record.status == status


def test_unrecognized_status_rejected() -> None:
    document = _base_document(status="draft")

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="status"):
        ReleaseRecord.model_validate(document)


def test_missing_crate_id_rejected() -> None:
    document = _base_document()
    del document["crate_id"]

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="crate_id"):
        ReleaseRecord.model_validate(document)
