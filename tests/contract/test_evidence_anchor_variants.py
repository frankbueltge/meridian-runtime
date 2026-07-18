"""Contract tests for the two EvidenceAnchor variants (task-packets/
E3-T01.yaml), beyond the generic example-driven checks
tests/contract/test_examples.py already runs.

examples/evidence-anchor.example.json (picked up automatically by
test_examples.py) is the TEXT variant with a quoted-fragment hash. This
module covers what a single example file cannot (adding a second file under
examples/ would break test_examples.py's
``test_every_schema_has_an_example_and_a_model``, which asserts exactly one
example per schema):

- a COMPUTATIONAL anchor with a recomputation reference (`run_id` +
  `recomputation_status`) validates against both the schema and Pydantic —
  task-packets/E3-T01.yaml acceptance: "a computational anchor with a
  recomputation reference validates";
- a TEXT anchor that uses the explicit `anchor_unavailable_reason` escape
  (no exact resolution at all) also validates — the OTHER lawful branch of
  the exact-resolution-or-explicit-reason invariant, not just the "has an
  exact resolution" branch already covered by the example.

Fixtures live under tests/contract/fixtures/valid/ (not examples/, for the
reason above, and not tests/contract/fixtures/invalid/, which
test_negative_fixtures.py treats as "must fail").
"""

from __future__ import annotations

import json
from pathlib import Path

from mrr.contracts import EvidenceAnchor

from scripts.check_contracts import SCHEMAS_DIR, build_registry, build_validator_for_schema

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "valid"


def _validate_against_schema(document: dict[str, object]) -> None:
    schema = json.loads((SCHEMAS_DIR / "evidence-anchor.schema.json").read_text())
    registry = build_registry()
    build_validator_for_schema(schema, registry).validate(document)


def test_computational_anchor_with_recomputation_reference_validates() -> None:
    document = json.loads((FIXTURES_DIR / "evidence-anchor-computational.json").read_text())

    _validate_against_schema(document)
    anchor = EvidenceAnchor.model_validate(document)

    assert anchor.anchor_kind == "computational"
    assert anchor.run_id is not None
    assert anchor.recomputation_status == "reproduced"
    assert anchor.snapshot_hash is None
    assert anchor.quoted_fragment_hash is None


def test_text_anchor_with_explicit_unavailable_reason_validates() -> None:
    document = json.loads((FIXTURES_DIR / "evidence-anchor-unavailable-reason.json").read_text())

    _validate_against_schema(document)
    anchor = EvidenceAnchor.model_validate(document)

    assert anchor.anchor_kind == "text"
    assert anchor.snapshot_hash is None
    assert anchor.quoted_fragment_hash is None
    assert anchor.anchor_unavailable_reason is not None


def test_text_example_has_a_quoted_fragment_hash() -> None:
    """The example picked up by test_examples.py is the TEXT variant with a
    quoted-fragment hash (task-packets/E3-T01.yaml acceptance: "a text
    anchor with a quoted-fragment hash validates").
    """
    example_path = Path(__file__).resolve().parents[2] / "examples" / "evidence-anchor.example.json"
    document = json.loads(example_path.read_text())
    anchor = EvidenceAnchor.model_validate(document)

    assert anchor.anchor_kind == "text"
    assert anchor.quoted_fragment_hash is not None
