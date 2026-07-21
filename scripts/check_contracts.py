"""Cross-validate schemas/*.schema.json, examples/*.example.json, and the
mrr.contracts Pydantic v2 models against each other (E1-T03; extended to a
seventh entity, RunManifest, by E2-T05; extended to a eighth and ninth
entity, SourceRecord and EvidenceAnchor, by E3-T01; extended to a tenth
entity, SourceFamily, by E3-T03; extended to an eleventh entity,
VerificationResult, by E3-T04; extended to a twelfth and thirteenth entity,
ModelProfile and ModelInvocation, by E4-T01; extended to a fourteenth
entity, Hypothesis, by E4-T03; extended to a fifteenth entity,
SkepticalChallenge, by E4-T04; extended to a sixteenth entity, Practice,
by E5-T01; extended to a seventeenth entity, NodeMessageEnvelope, by
E5-T03; extended to an eighteenth entity, OfflineBundle, by E5-T06;
extended to a nineteenth entity, TransferContract, by E6-T01; extended to
a twentieth entity, Obligation, by E6-T02; extended to a twenty-first
entity, MethodProfile, by K0-T01).

Four checks, run in order and accumulated into one failure list rather than
stopping at the first problem (so a single run reports every entity that is
out of sync, not just the first one):

1. Every schemas/*.schema.json is a valid JSON Schema Draft 2020-12 document
   (``Draft202012Validator.check_schema``).
2. Every examples/*.example.json validates against its own entity schema,
   resolved through a ``referencing.Registry`` built from all sixteen
   schemas (fifteen entities plus ``common.schema.json``) keyed by their
   ``$id`` — this is what lets the relative ``common.schema.json#/$defs/...``
   refs inside each entity schema resolve.
3. Every example validates against the corresponding Pydantic model in
   ``mrr.contracts``.
4. Round-trip: ``model_validate(example)`` -> ``model_dump_json()`` ->
   ``model_validate`` again must produce an equal model, and the
   re-serialized JSON must still pass the same JSON Schema validation as
   step 2 (catches serialization drift, e.g. a datetime format Pydantic
   emits that the schema's ``format: date-time`` would reject).

The JSON dumped in step 4 uses ``exclude_none=True``. Every optional field
in every ``mrr.contracts`` model that is *not* explicitly nullable in its
schema (a plain scalar type such as ``{"type": "string"}`, not
``{"type": ["string", "null"]}``) defaults to Python ``None`` to mean "not
stated" — dumping such a field as JSON ``null`` would fail that field's own
schema type. Dropping unset fields instead of emitting them as ``null`` is
the only way ``model_dump_json()`` output stays schema-valid for those
fields; see packages/contracts/mrr/contracts/common.py's ``Budget``
docstring for the same reasoning at the model layer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from mrr.contracts import (
    Claim,
    CorrectionEvent,
    EvidenceAnchor,
    EvidenceCrate,
    Hypothesis,
    MethodProfile,
    ModelInvocation,
    ModelProfile,
    NodeManifest,
    NodeMessageEnvelope,
    Obligation,
    OfflineBundle,
    Practice,
    ResearchScore,
    RunManifest,
    SkepticalChallenge,
    SourceFamily,
    SourceRecord,
    TaskBundle,
    TransferContract,
    VerificationResult,
)
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from referencing import Registry, Resource

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = REPO_ROOT / "schemas"
EXAMPLES_DIR = REPO_ROOT / "examples"

#: Entity name (the shared schemas/*.schema.json and examples/*.example.json
#: stem) to the Pydantic model that mirrors it.
ENTITY_MODELS: dict[str, type[BaseModel]] = {
    "research-score": ResearchScore,
    "node-manifest": NodeManifest,
    "task-bundle": TaskBundle,
    "claim": Claim,
    "evidence-crate": EvidenceCrate,
    "correction-event": CorrectionEvent,
    "run-manifest": RunManifest,
    "source-record": SourceRecord,
    "evidence-anchor": EvidenceAnchor,
    "source-family": SourceFamily,
    "verification-result": VerificationResult,
    "model-profile": ModelProfile,
    "model-invocation": ModelInvocation,
    "hypothesis": Hypothesis,
    "skeptical-challenge": SkepticalChallenge,
    "practice": Practice,
    "node-message-envelope": NodeMessageEnvelope,
    "offline-bundle": OfflineBundle,
    "transfer-contract": TransferContract,
    "obligation": Obligation,
    "method-profile": MethodProfile,
}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _schema_paths() -> list[Path]:
    return sorted(SCHEMAS_DIR.glob("*.schema.json"))


def _example_paths() -> list[Path]:
    return sorted(EXAMPLES_DIR.glob("*.example.json"))


def _entity_name(example_path: Path) -> str:
    return example_path.name.removesuffix(".example.json")


def build_registry() -> Registry:
    """Build a ``referencing.Registry`` from every schema in schemas/,
    keyed by its own ``$id``, so relative ``$ref``s such as
    ``common.schema.json#/$defs/urn`` resolve against the referencing
    schema's declared base URI.
    """
    resources: list[tuple[str, Resource]] = []
    for path in _schema_paths():
        contents = _load_json(path)
        resources.append((contents["$id"], Resource.from_contents(contents)))
    return Registry().with_resources(resources)


def build_validator_for_schema(schema: dict[str, Any], registry: Registry) -> Draft202012Validator:
    return Draft202012Validator(
        schema, registry=registry, format_checker=Draft202012Validator.FORMAT_CHECKER
    )


def check_schemas_are_valid_draft202012(errors: list[str]) -> None:
    """Check 1: every schemas/*.schema.json is a valid Draft 2020-12 document."""
    for path in _schema_paths():
        schema = _load_json(path)
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            errors.append(f"{path.name}: not a valid JSON Schema Draft 2020-12 document: {exc}")


def check_examples_against_json_schema(registry: Registry, errors: list[str]) -> None:
    """Check 2: every example validates against its own entity schema."""
    for example_path in _example_paths():
        entity = _entity_name(example_path)
        schema = _load_json(SCHEMAS_DIR / f"{entity}.schema.json")
        example = _load_json(example_path)
        try:
            build_validator_for_schema(schema, registry).validate(example)
        except ValidationError as exc:
            errors.append(f"{entity}: example fails JSON Schema validation: {exc.message}")


def check_examples_against_pydantic_and_roundtrip(registry: Registry, errors: list[str]) -> None:
    """Checks 3 and 4: Pydantic validation, and the model/JSON round trip."""
    for example_path in _example_paths():
        entity = _entity_name(example_path)
        model_cls = ENTITY_MODELS[entity]
        example = _load_json(example_path)

        try:
            model = model_cls.model_validate(example)
        except PydanticValidationError as exc:
            errors.append(f"{entity}: example fails Pydantic validation: {exc}")
            continue

        dumped_json = model.model_dump_json(exclude_none=True)

        try:
            model_again = model_cls.model_validate_json(dumped_json)
        except PydanticValidationError as exc:
            errors.append(f"{entity}: round-tripped JSON fails Pydantic validation: {exc}")
            continue

        if model != model_again:
            errors.append(f"{entity}: round-tripped model is not equal to the original")

        schema = _load_json(SCHEMAS_DIR / f"{entity}.schema.json")
        try:
            build_validator_for_schema(schema, registry).validate(json.loads(dumped_json))
        except ValidationError as exc:
            errors.append(
                f"{entity}: round-tripped JSON fails JSON Schema validation "
                f"(serialization drift): {exc.message}"
            )


def run_all_checks() -> list[str]:
    """Run every check and return the accumulated list of failure messages
    (empty if everything passed).
    """
    errors: list[str] = []
    check_schemas_are_valid_draft202012(errors)
    registry = build_registry()
    check_examples_against_json_schema(registry, errors)
    check_examples_against_pydantic_and_roundtrip(registry, errors)
    return errors


def main() -> int:
    errors = run_all_checks()

    if errors:
        print("check_contracts: FAILED", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print(
        f"check_contracts: OK — {len(ENTITY_MODELS)} entities checked "
        "(schema, Pydantic, round trip)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
