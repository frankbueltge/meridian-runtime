"""Contract tests for ADR-0010 staged-adoption step 1 (task-packets/
E1-T03b.yaml): the optional ``classification`` property added to
``schemas/common.schema.json#/$defs/baseObject`` and the mirroring
``BaseObject.classification: BaseObjectClassification | None = None`` field
in ``mrr.contracts.common``.

Acceptance-test mapping (task-packets/E1-T03b.yaml ``acceptance_tests``):

- "schema tier" -> ``test_base_object_schema_declares_classification_enum``,
  ``test_base_object_required_array_is_unchanged``.
- "schema+Pydantic dual tier, Practice as the demonstration vehicle" ->
  ``test_practice_accepts_every_base_object_classification_value``.
- "absence, both tiers" -> ``test_practice_example_without_classification_
  still_validates_and_defaults_to_none``.
- "TaskBundle collision/override regression, dual tier" ->
  ``test_task_bundle_rejects_synthetic_test_fixture_at_both_tiers``,
  ``test_task_bundle_still_accepts_each_original_five_value_at_both_tiers``,
  ``test_task_bundle_omitting_classification_is_still_rejected_at_both_
  tiers``. (The negative fixture itself,
  tests/contract/fixtures/invalid/task-bundle-classification-synthetic-
  test-fixture-rejected.json, is exercised separately and automatically by
  tests/contract/test_negative_fixtures.py's own glob-based discovery — not
  duplicated here.)
- reviewer annotation (ii) ("verify the actual entity count empirically ...
  rather than trusting either number") ->
  ``test_every_other_base_object_derived_entity_inherits_the_optional_
  field``, which derives the count from ``ENTITY_MODELS`` and
  ``issubclass(..., BaseObject)`` rather than hardcoding 27, 28, or 29.

The negative Practice fixture (tests/contract/fixtures/invalid/practice-
invalid-classification-value.json) is likewise exercised automatically by
test_negative_fixtures.py; not duplicated here either.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from mrr.contracts.common import BaseObject, BaseObjectClassification, Classification
from mrr.contracts.practice import Practice
from mrr.contracts.task_bundle import TaskBundle
from pydantic import ValidationError as PydanticValidationError

from scripts.check_contracts import (
    ENTITY_MODELS,
    SCHEMAS_DIR,
    build_registry,
    build_validator_for_schema,
)

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples"

#: The six values ADR-0010's Decision text names for
#: ``BaseObjectClassification`` -- the original five plus the new
#: ``SYNTHETIC_TEST_FIXTURE`` marker.
_SIX_VALUES: tuple[str, ...] = (
    "PUBLIC",
    "INTERNAL",
    "RESTRICTED",
    "SENSITIVE",
    "PARTICIPANT_IDENTIFIABLE",
    "SYNTHETIC_TEST_FIXTURE",
)

#: TaskBundle's own, pre-existing, unwidened five-value vocabulary
#: (schemas/task-bundle.schema.json line 60, unchanged by this task).
_ORIGINAL_FIVE_VALUES: tuple[str, ...] = (
    "PUBLIC",
    "INTERNAL",
    "RESTRICTED",
    "SENSITIVE",
    "PARTICIPANT_IDENTIFIABLE",
)


def _validate_against_schema(schema_filename: str, document: dict[str, Any]) -> None:
    schema = json.loads((SCHEMAS_DIR / schema_filename).read_text())
    registry = build_registry()
    build_validator_for_schema(schema, registry).validate(document)


# ---------------------------------------------------------------------------
# Schema tier: schemas/common.schema.json#/$defs/baseObject.properties
# ---------------------------------------------------------------------------


def test_base_object_schema_declares_classification_enum() -> None:
    common_schema = json.loads((SCHEMAS_DIR / "common.schema.json").read_text())
    base_object = common_schema["$defs"]["baseObject"]

    assert base_object["properties"]["classification"] == {
        "type": "string",
        "enum": list(_SIX_VALUES),
    }


def test_base_object_required_array_is_unchanged() -> None:
    """``classification`` is added only to ``properties``, never to
    ``required`` -- the field stays optional (invariants).
    """
    common_schema = json.loads((SCHEMAS_DIR / "common.schema.json").read_text())
    base_object = common_schema["$defs"]["baseObject"]

    assert base_object["required"] == [
        "id",
        "api_version",
        "kind",
        "practice_id",
        "revision",
        "created_at",
        "created_by",
        "content_hash",
    ]
    assert "classification" not in base_object["required"]


# ---------------------------------------------------------------------------
# Schema+Pydantic dual tier, Practice as the demonstration vehicle.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", _SIX_VALUES)
def test_practice_accepts_every_base_object_classification_value(value: str) -> None:
    document = json.loads((EXAMPLES_DIR / "practice.example.json").read_text())
    document["classification"] = value

    _validate_against_schema("practice.schema.json", document)
    practice = Practice.model_validate(document)

    assert practice.classification == value


def test_practice_example_without_classification_still_validates_and_defaults_to_none() -> None:
    document = json.loads((EXAMPLES_DIR / "practice.example.json").read_text())
    assert "classification" not in document

    _validate_against_schema("practice.schema.json", document)
    practice = Practice.model_validate(document)

    assert practice.classification is None
    assert "classification" not in practice.model_dump(exclude_none=True)


# ---------------------------------------------------------------------------
# TaskBundle collision/override regression (derived_decisions (a)).
# ---------------------------------------------------------------------------


def test_task_bundle_rejects_synthetic_test_fixture_at_both_tiers() -> None:
    document = json.loads((EXAMPLES_DIR / "task-bundle.example.json").read_text())
    document["classification"] = "SYNTHETIC_TEST_FIXTURE"

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema("task-bundle.schema.json", document)

    with pytest.raises(PydanticValidationError):
        TaskBundle.model_validate(document)


@pytest.mark.parametrize("value", _ORIGINAL_FIVE_VALUES)
def test_task_bundle_still_accepts_each_original_five_value_at_both_tiers(value: str) -> None:
    """Regression: TaskBundle's pre-existing behavior is provably
    unchanged for every value it already accepted.
    """
    document = json.loads((EXAMPLES_DIR / "task-bundle.example.json").read_text())
    document["classification"] = value

    _validate_against_schema("task-bundle.schema.json", document)
    task_bundle = TaskBundle.model_validate(document)

    assert task_bundle.classification == value


def test_task_bundle_omitting_classification_is_still_rejected_at_both_tiers() -> None:
    """Regression: TaskBundle's own ``classification`` stays required --
    its ``required``-ness is provably unchanged by BaseObject's new,
    optional field.
    """
    document = json.loads((EXAMPLES_DIR / "task-bundle.example.json").read_text())
    del document["classification"]

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema("task-bundle.schema.json", document)

    with pytest.raises(PydanticValidationError):
        TaskBundle.model_validate(document)


def test_task_bundle_classification_field_is_still_required_and_narrowly_typed() -> None:
    """Direct proof (not just behavioral) that TaskBundle's own field
    declaration was not touched: its annotation is still the original,
    five-value ``Classification`` -- not the new, wider
    ``BaseObjectClassification`` -- and it is still a required field (no
    default), unlike ``BaseObject.classification`` itself.
    """
    field = TaskBundle.model_fields["classification"]

    assert cast(Any, field.annotation) is Classification
    assert field.is_required()

    base_field = BaseObject.model_fields["classification"]
    assert cast(Any, base_field.annotation) == BaseObjectClassification | None
    assert not base_field.is_required()
    assert base_field.default is None


# ---------------------------------------------------------------------------
# Reviewer annotation (ii): verify the actual baseObject-derived entity
# count empirically, rather than trusting the packet prose's "27 of the 29"
# or the invariants' "29 entities except TaskBundle" parentheticals.
# ---------------------------------------------------------------------------


def test_every_other_base_object_derived_entity_inherits_the_optional_field() -> None:
    """Every entity in ``ENTITY_MODELS`` that is actually a ``BaseObject``
    subclass -- some of the 29 registered entities (``CorrectionNotification``,
    ``NodeMessageEnvelope``, ``OfflineBundle``) are deliberately NOT
    ``BaseObject`` subclasses, per their own module docstrings, so they carry
    no ``classification`` slot at all and are excluded here -- gains the new,
    six-value, optional ``classification`` field via inheritance, with ZERO
    change to that entity's own model file, EXCEPT ``TaskBundle``, which
    narrows it to its own pre-existing, required, five-value field (asserted
    separately above).
    """
    base_object_derived = {
        name: model_cls
        for name, model_cls in ENTITY_MODELS.items()
        if issubclass(model_cls, BaseObject)
    }
    not_base_object_derived = set(ENTITY_MODELS) - set(base_object_derived)

    # Empirical, not asserted from the packet prose: every registered entity
    # is either a BaseObject subclass or one of the documented transport/
    # notification exceptions -- nothing is silently uncategorized.
    assert set(ENTITY_MODELS) == set(base_object_derived) | not_base_object_derived
    assert "task-bundle" in base_object_derived

    others = {name: cls for name, cls in base_object_derived.items() if name != "task-bundle"}
    assert others, "expected at least one BaseObject-derived entity other than TaskBundle"

    for name, model_cls in others.items():
        field = model_cls.model_fields["classification"]
        assert cast(Any, field.annotation) == BaseObjectClassification | None, (
            f"{name}: expected the inherited BaseObjectClassification | None annotation"
        )
        assert not field.is_required(), f"{name}: classification must stay optional"
        assert field.default is None, f"{name}: classification must default to None"

    # Document (not assert as a magic number) the actual counts this run
    # found, so a future entity addition cannot silently fall outside this
    # loop's coverage without the two counts below visibly moving together.
    assert len(base_object_derived) == len(others) + 1
    assert len(ENTITY_MODELS) == len(base_object_derived) + len(not_base_object_derived)
