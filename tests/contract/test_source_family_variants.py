"""Contract tests for ``SourceFamily`` (task-packets/E3-T03.yaml) beyond the
generic example-driven checks tests/contract/test_examples.py already runs.

examples/source-family.example.json (picked up automatically by
test_examples.py) uses a single ``relationship_type`` value
("shared_dataset"). This module covers the remaining domain-2.10 vocabulary
values inline (adding five more files under examples/ would break
test_examples.py's "exactly one example per schema" check,
``test_every_schema_has_an_example_and_a_model``) — task-packets/E3-T03.yaml
acceptance: "each relationship-type value validates; an unknown value fails
both validators."

The unknown-value and malformed-urn failure cases are covered by
tests/contract/fixtures/invalid/source-family-unknown-relationship-type.json
and source-family-malformed-member-urn.json via
tests/contract/test_negative_fixtures.py, not duplicated here.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from mrr.contracts import RelationshipType, SourceFamily
from pydantic import ValidationError

from scripts.check_contracts import SCHEMAS_DIR, build_registry, build_validator_for_schema

#: Domain 2.10's exact relationship-type vocabulary — mirrors
#: mrr.contracts.source_family.RelationshipType.
_ALL_RELATIONSHIP_TYPES: tuple[RelationshipType, ...] = (
    "copy",
    "syndication",
    "shared_dataset",
    "shared_press_release",
    "direct_derivation",
    "uncertain",
)


def _base_document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "id": "urn:mrr:source-family:01J00000000000000000000033",
        "api_version": "mrr/v1alpha1",
        "kind": "SourceFamily",
        "practice_id": "urn:mrr:practice:01J00000000000000000000001",
        "revision": 1,
        "created_at": "2026-07-18T10:00:00Z",
        "created_by": "urn:mrr:agent:01J00000000000000000000016",
        "content_hash": "sha256:" + "7" * 64,
        "origin_ref": None,
        "member_source_ids": [
            "urn:mrr:source-record:01J00000000000000000000020",
            "urn:mrr:source-record:01J00000000000000000000021",
        ],
        "relationship_type": "shared_dataset",
        "confidence": 0.5,
        "rationale": "Fixture rationale for a contract-level relationship-type check.",
        "detecting_method": "Manual editorial review",
        "reviewer_id": None,
    }
    document.update(overrides)
    return document


def _validate_against_schema(document: dict[str, Any]) -> None:
    schema = json.loads((SCHEMAS_DIR / "source-family.schema.json").read_text())
    registry = build_registry()
    build_validator_for_schema(schema, registry).validate(document)


@pytest.mark.parametrize("relationship_type", _ALL_RELATIONSHIP_TYPES)
def test_each_relationship_type_validates(relationship_type: RelationshipType) -> None:
    document = _base_document(relationship_type=relationship_type)

    _validate_against_schema(document)
    family = SourceFamily.model_validate(document)

    assert family.relationship_type == relationship_type


def test_unknown_relationship_type_rejected_at_model_level() -> None:
    document = _base_document(relationship_type="coincidental_similarity")

    with pytest.raises(ValidationError, match="relationship_type"):
        SourceFamily.model_validate(document)


def test_member_source_ids_requires_at_least_one_entry() -> None:
    """Domain 2.10 does not spell out a minimum member count, but a family
    referencing zero sources represents nothing — the same non-degeneracy
    reasoning ``CorrectionEvent.affected_objects`` already applies. See
    ``mrr.contracts.source_family``'s own module docstring for the full
    rationale; flagged in the PR body for reviewer scrutiny.
    """
    document = _base_document(member_source_ids=[])

    with pytest.raises(ValidationError, match="member_source_ids"):
        SourceFamily.model_validate(document)


def test_origin_ref_accepts_a_free_text_descriptor_not_only_a_urn() -> None:
    """origin_ref is documented as urn-or-free-text-or-null (domain 2.10:
    "origin source or dataset" — the origin is often never itself retrieved
    as its own SourceRecord).
    """
    document = _base_document(origin_ref="Wire service distribution, 2026 edition")

    _validate_against_schema(document)
    family = SourceFamily.model_validate(document)

    assert family.origin_ref == "Wire service distribution, 2026 edition"


def test_origin_ref_accepts_a_urn() -> None:
    document = _base_document(origin_ref="urn:mrr:source-record:01J00000000000000000000019")

    _validate_against_schema(document)
    family = SourceFamily.model_validate(document)

    assert family.origin_ref == "urn:mrr:source-record:01J00000000000000000000019"
