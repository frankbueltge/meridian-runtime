"""Positive contract tests (E1-T03): every entity's example must validate
against JSON Schema and the corresponding Pydantic model, and round-trip
through ``model_dump_json()`` without losing information or drifting out of
schema validity.

This reuses the check functions in scripts/check_contracts.py (structured as
an importable module precisely so tests and the standalone
``uv run python scripts/check_contracts.py`` invocation share one
implementation) rather than re-implementing the same checks here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mrr.crypto.keys import decode_public_key

from scripts.check_contracts import (
    ENTITY_MODELS,
    build_registry,
    check_examples_against_json_schema,
    check_examples_against_pydantic_and_roundtrip,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = REPO_ROOT / "examples"


@pytest.fixture(scope="module")
def json_schema_errors() -> list[str]:
    errors: list[str] = []
    check_examples_against_json_schema(build_registry(), errors)
    return errors


@pytest.fixture(scope="module")
def pydantic_roundtrip_errors() -> list[str]:
    errors: list[str] = []
    check_examples_against_pydantic_and_roundtrip(build_registry(), errors)
    return errors


@pytest.mark.parametrize("entity", sorted(ENTITY_MODELS))
def test_example_passes_json_schema_validation(entity: str, json_schema_errors: list[str]) -> None:
    relevant = [error for error in json_schema_errors if error.startswith(f"{entity}:")]
    assert relevant == []


@pytest.mark.parametrize("entity", sorted(ENTITY_MODELS))
def test_example_passes_pydantic_validation_and_roundtrip(
    entity: str, pydantic_roundtrip_errors: list[str]
) -> None:
    relevant = [error for error in pydantic_roundtrip_errors if error.startswith(f"{entity}:")]
    assert relevant == []


def test_every_schema_has_an_example_and_a_model() -> None:
    """Guards against a new schema being added without a matching example
    and Pydantic model (or vice versa) — the three must stay in lockstep.
    """
    schema_entities = {
        p.name.removesuffix(".schema.json")
        for p in (REPO_ROOT / "schemas").glob("*.schema.json")
        if p.name != "common.schema.json"
    }
    example_entities = {
        p.name.removesuffix(".example.json")
        for p in (REPO_ROOT / "examples").glob("*.example.json")
    }

    assert schema_entities == example_entities == set(ENTITY_MODELS)


# ---------------------------------------------------------------------------
# ADR-0009 / task-packets/E5-T02b.yaml: the canonical public-key string is
# plain standard base64 of the raw 32 Ed25519 bytes — no committed example
# may carry the retired `ed25519-raw-base64:` prefix (the old production
# builder's spelling) or the `did:key:` placeholder (docs/spec/02's own
# section 2.2 example, never actually decodable in this codebase).
# ---------------------------------------------------------------------------

_RETIRED_PUBLIC_KEY_ENCODINGS = ("ed25519-raw-base64:", "did:key:")


def test_no_committed_example_contains_a_retired_public_key_encoding() -> None:
    for example_path in sorted(EXAMPLES_DIR.glob("*.example.json")):
        text = example_path.read_text()
        for retired in _RETIRED_PUBLIC_KEY_ENCODINGS:
            assert retired not in text, (
                f"{example_path.name} still contains the retired public-key "
                f"encoding {retired!r} (ADR-0009 pins plain base64)"
            )


def test_node_manifest_example_public_keys_are_canonical_and_decodable() -> None:
    """The node-manifest example's ``public_keys`` are real ADR-0009
    canonical plain-base64 Ed25519 keys (generated the same way E5-T01's
    practice example was) — not the ``did:key:`` placeholder docs/spec/02
    section 2.2 originally illustrated, which no decoder in this codebase
    ever resolved.
    """
    data = json.loads((EXAMPLES_DIR / "node-manifest.example.json").read_text())
    public_keys = data["public_keys"]
    assert public_keys, "node-manifest example must declare at least one public key"
    for encoded in public_keys:
        # Raises InvalidPublicKeyError if this is not a well-formed,
        # standard-base64-encoded 32-byte Ed25519 public key.
        decode_public_key(encoded)
