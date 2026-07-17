"""Negative contract tests (E1-T03): malformed objects under
tests/contract/fixtures/invalid/ must be rejected by *both* JSON Schema and
the corresponding Pydantic model — never accepted by one and silently
waved through by the other.

Fixtures are NOT under examples/ (examples/README.md: "these examples are
schema-oriented fixtures for implementation bootstrapping" — i.e. valid
ones only). Each fixture file name is prefixed with the entity it belongs
to (``claim-...json``, ``evidence-crate-...json``), matched against
scripts.check_contracts.ENTITY_MODELS by longest-prefix so hyphenated
entity names such as ``evidence-crate`` resolve correctly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema.exceptions import ValidationError
from pydantic import ValidationError as PydanticValidationError

from scripts.check_contracts import (
    ENTITY_MODELS,
    SCHEMAS_DIR,
    build_registry,
    build_validator_for_schema,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "invalid"


def _entity_for_fixture(path: Path) -> str:
    """Resolve a fixture's entity by longest-matching known entity name
    prefix (so ``evidence-crate-...`` matches ``evidence-crate``, not a
    shorter false match).
    """
    candidates = [entity for entity in ENTITY_MODELS if path.name.startswith(f"{entity}-")]
    if not candidates:
        raise AssertionError(f"fixture {path.name} does not start with any known entity name")
    return max(candidates, key=len)


FIXTURE_PATHS = sorted(FIXTURES_DIR.glob("*.json"))
assert FIXTURE_PATHS, (
    "expected at least one negative fixture under tests/contract/fixtures/invalid/"
)


@pytest.mark.parametrize("fixture_path", FIXTURE_PATHS, ids=[p.name for p in FIXTURE_PATHS])
def test_invalid_fixture_fails_json_schema_validation(fixture_path: Path) -> None:
    entity = _entity_for_fixture(fixture_path)
    schema = json.loads((SCHEMAS_DIR / f"{entity}.schema.json").read_text())
    document = json.loads(fixture_path.read_text())
    registry = build_registry()

    with pytest.raises(ValidationError):
        build_validator_for_schema(schema, registry).validate(document)


@pytest.mark.parametrize("fixture_path", FIXTURE_PATHS, ids=[p.name for p in FIXTURE_PATHS])
def test_invalid_fixture_fails_pydantic_validation(fixture_path: Path) -> None:
    entity = _entity_for_fixture(fixture_path)
    model_cls = ENTITY_MODELS[entity]
    document = json.loads(fixture_path.read_text())

    with pytest.raises(PydanticValidationError):
        model_cls.model_validate(document)
