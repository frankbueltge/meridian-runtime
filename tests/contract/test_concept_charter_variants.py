"""Contract tests for ``ConceptCharter`` (task-packets/K1-T01.yaml) beyond
the generic example-driven checks tests/contract/test_examples.py already
runs.

A byte-identical duplicate entry (JSON Schema `uniqueItems` AND the Pydantic
`model_validator` both reject it) is covered by
tests/contract/fixtures/invalid/concept-charter-duplicate-entry-id.json via
tests/contract/test_negative_fixtures.py. The STRONGER case this module
covers instead — two entries sharing the same `entry_id` but differing in
every other field — is NOT expressible as a JSON Schema `uniqueItems` check
(JSON Schema has no per-subfield uniqueness construct across array items),
so it is tested here directly against the Pydantic model only, exactly as
task-packets/K1-T01.yaml's own acceptance-test wording says ("... rejected
by the Pydantic `model_validator`", with no JSON Schema mention for this
specific case, unlike the empty-`entries` case below).

Acceptance-test mapping (task-packets/K1-T01.yaml):

- "ConceptCharter — an `entries` list with two entries sharing the same
  `entry_id` is rejected by the Pydantic `model_validator`" ->
  ``test_duplicate_entry_id_with_differing_content_rejected_by_pydantic``.
- "... an empty `entries` list is rejected by both JSON Schema (`minItems`)
  and Pydantic" -> ``test_empty_entries_rejected``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from mrr.contracts.concept_charter import ConceptCharter
from pydantic import ValidationError

from scripts.check_contracts import SCHEMAS_DIR, build_registry, build_validator_for_schema

_VALID_HASH = "sha256:" + "2" * 64


def _base_document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "id": "urn:mrr:concept-charter:01J00000000000000000000220",
        "api_version": "mrr/v1alpha1",
        "kind": "ConceptCharter",
        "practice_id": "urn:mrr:practice:01J00000000000000000000001",
        "revision": 1,
        "created_at": "2026-07-21T09:05:00Z",
        "created_by": "urn:mrr:person:01J00000000000000000000002",
        "content_hash": _VALID_HASH,
        "entries": [
            {
                "entry_id": "model-collapse-mechanism-v1",
                "term": "model-collapse mechanism",
                "definition": "First definition.",
                "scope_note": None,
            }
        ],
        "status": "draft",
    }
    document.update(overrides)
    return document


def _validate_against_schema(document: dict[str, Any]) -> None:
    schema = json.loads((SCHEMAS_DIR / "concept-charter.schema.json").read_text())
    registry = build_registry()
    build_validator_for_schema(schema, registry).validate(document)


def test_empty_entries_rejected() -> None:
    document = _base_document(entries=[])

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="entries"):
        ConceptCharter.model_validate(document)


def test_duplicate_entry_id_with_differing_content_rejected_by_pydantic() -> None:
    """Same entry_id, different term/definition — a JSON Schema `uniqueItems`
    check would NOT catch this (the two items are not deeply equal), but the
    Pydantic `model_validator` must still reject it.
    """
    document = _base_document(
        entries=[
            {
                "entry_id": "model-collapse-mechanism-v1",
                "term": "model-collapse mechanism",
                "definition": "First definition.",
                "scope_note": None,
            },
            {
                "entry_id": "model-collapse-mechanism-v1",
                "term": "a completely different term",
                "definition": "A conflicting second definition sharing the same entry_id.",
                "scope_note": "distinct scope note",
            },
        ]
    )

    # Deliberately NOT asserted against JSON Schema here — see module docstring.
    with pytest.raises(ValidationError, match="entry_id"):
        ConceptCharter.model_validate(document)


def test_two_entries_with_distinct_entry_ids_are_accepted() -> None:
    document = _base_document(
        entries=[
            {
                "entry_id": "term-a",
                "term": "term a",
                "definition": "definition a",
                "scope_note": None,
            },
            {
                "entry_id": "term-b",
                "term": "term b",
                "definition": "definition b",
                "scope_note": None,
            },
        ]
    )

    _validate_against_schema(document)
    model = ConceptCharter.model_validate(document)

    assert [entry.entry_id for entry in model.entries] == ["term-a", "term-b"]
