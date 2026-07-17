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

from pathlib import Path

import pytest

from scripts.check_contracts import (
    ENTITY_MODELS,
    build_registry,
    check_examples_against_json_schema,
    check_examples_against_pydantic_and_roundtrip,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


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
