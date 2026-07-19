"""Contract tests for ``ModelProfile`` (task-packets/E4-T01.yaml) beyond the
generic example-driven checks tests/contract/test_examples.py already runs.

examples/model-profile.example.json uses a single ``determinism`` value
("deterministic") with a zero sampling temperature. This module covers the
remaining vocabulary and the MRR-FR-044 self-contradiction gate inline
(adding more files under examples/ would break test_examples.py's "exactly
one example per schema" check, ``test_every_schema_has_an_example_and_a_model``)
— mirroring tests/contract/test_verification_result_variants.py's own
precedent.

The malformed-config-hash and unknown-determinism failure cases are covered
by tests/contract/fixtures/invalid/model-profile-*.json via
tests/contract/test_negative_fixtures.py, not duplicated here.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from mrr.contracts.model_profile import ModelProfile
from pydantic import ValidationError

from scripts.check_contracts import SCHEMAS_DIR, build_registry, build_validator_for_schema

_VALID_HASH = "sha256:" + "9" * 64


def _base_document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "id": "urn:mrr:model-profile:01J00000000000000000000040",
        "api_version": "mrr/v1alpha1",
        "kind": "ModelProfile",
        "practice_id": "urn:mrr:practice:01J00000000000000000000001",
        "revision": 1,
        "created_at": "2026-07-19T09:00:00Z",
        "created_by": "urn:mrr:agent:01J00000000000000000000016",
        "content_hash": _VALID_HASH,
        "provider": "anthropic",
        "model_family": "claude-3",
        "model_identifier": "claude-3-5-sonnet-20241022",
        "decoding_parameters": {"temperature": 0},
        "determinism": "deterministic",
        "seed": 42,
        "prompt_family": "meridian-skeptic-v1",
        "tool_permissions": ["web_search"],
        "config_hash": _VALID_HASH,
    }
    document.update(overrides)
    return document


def _validate_against_schema(document: dict[str, Any]) -> None:
    schema = json.loads((SCHEMAS_DIR / "model-profile.schema.json").read_text())
    registry = build_registry()
    build_validator_for_schema(schema, registry).validate(document)


# ---------------------------------------------------------------------------
# determinism / MRR-FR-044: explicit, required, and self-consistency-checked
# against decoding_parameters.temperature.
# ---------------------------------------------------------------------------


def test_deterministic_with_zero_temperature_succeeds() -> None:
    document = _base_document(determinism="deterministic", decoding_parameters={"temperature": 0})

    _validate_against_schema(document)
    profile = ModelProfile.model_validate(document)

    assert profile.determinism == "deterministic"


def test_deterministic_with_no_temperature_key_succeeds() -> None:
    document = _base_document(determinism="deterministic", decoding_parameters={})

    _validate_against_schema(document)
    profile = ModelProfile.model_validate(document)

    assert profile.decoding_parameters == {}


def test_deterministic_with_nonzero_temperature_rejected() -> None:
    document = _base_document(determinism="deterministic", decoding_parameters={"temperature": 0.7})

    with pytest.raises(JsonSchemaValidationError):
        _validate_against_schema(document)

    with pytest.raises(ValidationError, match="determinism"):
        ModelProfile.model_validate(document)


def test_stochastic_with_nonzero_temperature_succeeds() -> None:
    document = _base_document(determinism="stochastic", decoding_parameters={"temperature": 0.7})

    _validate_against_schema(document)
    profile = ModelProfile.model_validate(document)

    assert profile.determinism == "stochastic"


def test_unknown_determinism_rejected_at_model_level() -> None:
    document = _base_document(determinism="probably_deterministic")

    with pytest.raises(ValidationError, match="determinism"):
        ModelProfile.model_validate(document)


# ---------------------------------------------------------------------------
# Optional fields: seed, prompt_family, tool_permissions, decoding_parameters
# all absent — the minimal, provider-neutral profile.
# ---------------------------------------------------------------------------


def test_minimal_profile_with_no_optional_fields_succeeds() -> None:
    document = _base_document()
    for optional_field in ("decoding_parameters", "seed", "prompt_family", "tool_permissions"):
        del document[optional_field]

    _validate_against_schema(document)
    profile = ModelProfile.model_validate(document)

    assert profile.decoding_parameters == {}
    assert profile.seed is None
    assert profile.prompt_family is None
    assert profile.tool_permissions == []


# ---------------------------------------------------------------------------
# provider: an opaque string label — no enumerated vendor vocabulary.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider", ["anthropic", "openai", "local-ollama", "internal-embedder"])
def test_arbitrary_provider_labels_all_validate(provider: str) -> None:
    document = _base_document(provider=provider)

    _validate_against_schema(document)
    profile = ModelProfile.model_validate(document)

    assert profile.provider == provider


def test_empty_provider_rejected() -> None:
    document = _base_document(provider="")

    with pytest.raises(ValidationError, match="provider"):
        ModelProfile.model_validate(document)
