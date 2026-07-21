"""Contract tests for ``MethodProfile`` (task-packets/K0-T01.yaml) beyond
the generic example-driven checks tests/contract/test_examples.py already
runs.

examples/method-profile.example.json covers exactly one realistic
``systematic_evidence_synthesis`` profile. This module covers the remaining
vocabulary and the MRR-MTH-003/016 declaration-shape gates inline (adding
more files under examples/ would break test_examples.py's "exactly one
example per schema" check, ``test_every_schema_has_an_example_and_a_model``)
— mirroring tests/contract/test_model_profile_variants.py's own precedent.

The missing-max-ceiling, missing-protocol-form, unknown-claim-type,
unknown-ceiling, empty-executor-steps, and missing-executor-step-kind
failure cases are covered by tests/contract/fixtures/invalid/method-profile-*.json
via tests/contract/test_negative_fixtures.py, not duplicated here.

Acceptance-test mapping (task-packets/K0-T01.yaml):

- "schema round-trip" -> covered by tests/contract/test_examples.py
  (parametrized over ``ENTITY_MODELS``, which this task registers
  ``"method-profile"`` into).
- "claim_types accepts only ClaimType's seven values; an unrecognized claim
  type string is rejected" ->
  ``test_unrecognized_claim_type_rejected_at_model_level``.
- "max_claim_ceiling accepts only the seven ClaimCeiling strings in
  CLAIM_CEILING_ORDER's declared order; an unrecognized ceiling string is
  rejected" -> ``test_claim_ceiling_order_matches_spec_08_section_4_exactly``,
  ``test_all_seven_ceiling_values_are_accepted``,
  ``test_unrecognized_ceiling_rejected_at_model_level``.
- "executor_steps requires at least one entry, each declaring an explicit
  deterministic/model_assisted kind; an entry with a missing or unrecognized
  kind is rejected" -> ``test_empty_executor_steps_rejected``,
  ``test_executor_step_unrecognized_kind_rejected``.
- "a profile missing max_claim_ceiling is rejected at construction, never
  silently defaulted" / "... missing protocol_form ..." ->
  ``test_missing_max_claim_ceiling_rejected_at_construction``,
  ``test_missing_protocol_form_rejected_at_construction``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from mrr.contracts.method_profile import CLAIM_CEILING_ORDER, ClaimCeiling, MethodProfile
from pydantic import ValidationError

from scripts.check_contracts import SCHEMAS_DIR, build_registry, build_validator_for_schema

_VALID_HASH = "sha256:" + "9" * 64


def _base_document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "id": "urn:mrr:method-profile:01J00000000000000000000110",
        "api_version": "mrr/v1alpha1",
        "kind": "MethodProfile",
        "practice_id": "urn:mrr:practice:01J00000000000000000000001",
        "revision": 1,
        "created_at": "2026-07-21T09:00:00Z",
        "created_by": "urn:mrr:person:01J00000000000000000000002",
        "content_hash": _VALID_HASH,
        "profile_key": "systematic_evidence_synthesis",
        "version": "1.0.0",
        "claim_types": ["observational", "interpretive"],
        "max_claim_ceiling": "associational_unadjusted",
        "protocol_form": "synthesis_protocol",
        "executor_task_family": ["mrr.method.systematic_evidence_synthesis/1"],
        "executor_steps": [
            {"name": "snapshot_loading", "kind": "deterministic"},
            {"name": "extraction", "kind": "model_assisted"},
        ],
        "inappropriate_uses": ["causal claims beyond associational_unadjusted"],
        "status": "accepted",
    }
    document.update(overrides)
    return document


def _validate_against_schema(document: dict[str, Any]) -> None:
    schema = json.loads((SCHEMAS_DIR / "method-profile.schema.json").read_text())
    registry = build_registry()
    build_validator_for_schema(schema, registry).validate(document)


# ---------------------------------------------------------------------------
# ClaimCeiling / CLAIM_CEILING_ORDER — spec 08 section 4's exact taxonomy.
# ---------------------------------------------------------------------------


def test_claim_ceiling_order_matches_spec_08_section_4_exactly() -> None:
    assert CLAIM_CEILING_ORDER == (
        "insufficient_evidence",
        "mechanism_hypothesis",
        "descriptive",
        "associational_unadjusted",
        "associational_adjusted",
        "causal_local",
        "causal_bounded",
    )


def test_claim_ceiling_order_matches_the_literal_type_exactly() -> None:
    from typing import get_args

    assert set(CLAIM_CEILING_ORDER) == set(get_args(ClaimCeiling))
    assert len(CLAIM_CEILING_ORDER) == 7


@pytest.mark.parametrize("ceiling", list(CLAIM_CEILING_ORDER))
def test_all_seven_ceiling_values_are_accepted(ceiling: str) -> None:
    document = _base_document(max_claim_ceiling=ceiling)

    _validate_against_schema(document)
    profile = MethodProfile.model_validate(document)

    assert profile.max_claim_ceiling == ceiling


def test_unrecognized_ceiling_rejected_at_model_level() -> None:
    document = _base_document(max_claim_ceiling="definitely_causal")

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="max_claim_ceiling"):
        MethodProfile.model_validate(document)


# ---------------------------------------------------------------------------
# claim_types — mrr.contracts.claim.ClaimType's seven values, verbatim.
# ---------------------------------------------------------------------------


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
def test_every_claim_type_value_is_accepted(claim_type: str) -> None:
    document = _base_document(claim_types=[claim_type])

    _validate_against_schema(document)
    profile = MethodProfile.model_validate(document)

    assert profile.claim_types == [claim_type]


def test_unrecognized_claim_type_rejected_at_model_level() -> None:
    document = _base_document(claim_types=["prophetic"])

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="claim_types"):
        MethodProfile.model_validate(document)


# ---------------------------------------------------------------------------
# executor_steps — non-empty, explicit per-step kind (MRR-MTH-016).
# ---------------------------------------------------------------------------


def test_empty_executor_steps_rejected() -> None:
    document = _base_document(executor_steps=[])

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="executor_steps"):
        MethodProfile.model_validate(document)


def test_executor_step_unrecognized_kind_rejected() -> None:
    document = _base_document(
        executor_steps=[{"name": "extraction", "kind": "probably_deterministic"}]
    )

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="executor_steps"):
        MethodProfile.model_validate(document)


def test_executor_step_missing_kind_rejected() -> None:
    document = _base_document(executor_steps=[{"name": "extraction"}])

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="executor_steps"):
        MethodProfile.model_validate(document)


# ---------------------------------------------------------------------------
# Required-unconditionally fields: max_claim_ceiling, protocol_form
# (task-packets/K0-T01.yaml acceptance tests, verbatim).
# ---------------------------------------------------------------------------


def test_missing_max_claim_ceiling_rejected_at_construction() -> None:
    document = _base_document()
    del document["max_claim_ceiling"]

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="max_claim_ceiling"):
        MethodProfile.model_validate(document)


def test_missing_protocol_form_rejected_at_construction() -> None:
    document = _base_document()
    del document["protocol_form"]

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="protocol_form"):
        MethodProfile.model_validate(document)


# ---------------------------------------------------------------------------
# inappropriate_uses is allowed to be empty (task-packets/K0-T01.yaml
# specification_gaps — flagged as an open question, not gated here).
# ---------------------------------------------------------------------------


def test_empty_inappropriate_uses_is_accepted() -> None:
    document = _base_document(inappropriate_uses=[])

    _validate_against_schema(document)
    profile = MethodProfile.model_validate(document)

    assert profile.inappropriate_uses == []


# ---------------------------------------------------------------------------
# profile_key / version pattern checks.
# ---------------------------------------------------------------------------


def test_profile_key_must_be_a_lowercase_snake_case_slug() -> None:
    document = _base_document(profile_key="Systematic-Evidence-Synthesis")

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="profile_key"):
        MethodProfile.model_validate(document)


def test_version_must_match_the_semver_pattern() -> None:
    document = _base_document(version="v1")

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="version"):
        MethodProfile.model_validate(document)
