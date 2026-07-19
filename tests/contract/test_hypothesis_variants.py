"""Contract tests for the Hypothesis entity's `insufficient_evidence` variant
(task-packets/E4-T03.yaml), beyond the generic example-driven checks
tests/contract/test_examples.py already runs.

examples/hypothesis.example.json (picked up automatically by
test_examples.py) is the ``confirmatory`` variant with non-empty
predicted/disconfirming observations. This module covers the OTHER lawful
branch of the falsifiability conditional — an ``insufficient_evidence``
branch that leaves both observation lists empty but records a non-null
``insufficiency_rationale`` (task-packets/E4-T03.yaml acceptance: "an
insufficient_evidence branch with empty observations but a recorded
insufficiency rationale is accepted") — mirroring
tests/contract/test_evidence_anchor_variants.py's own "second lawful
variant, not just the example" precedent.

Fixtures live under tests/contract/fixtures/valid/ (not examples/, which
tests/contract/test_examples.py's own
``test_every_schema_has_an_example_and_a_model`` requires to hold exactly
one example per schema; and not tests/contract/fixtures/invalid/, which
tests/contract/test_negative_fixtures.py treats as "must fail").
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

from mrr.contracts import BRANCH_ROLES, BranchRole, Hypothesis, HypothesisStatus

from scripts.check_contracts import SCHEMAS_DIR, build_registry, build_validator_for_schema

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "valid"


def _validate_against_schema(document: dict[str, object]) -> None:
    schema = json.loads((SCHEMAS_DIR / "hypothesis.schema.json").read_text())
    registry = build_registry()
    build_validator_for_schema(schema, registry).validate(document)


def test_insufficient_evidence_branch_with_rationale_validates() -> None:
    document = json.loads((FIXTURES_DIR / "hypothesis-insufficient-evidence.json").read_text())

    _validate_against_schema(document)
    hypothesis = Hypothesis.model_validate(document)

    assert hypothesis.branch_role == "insufficient_evidence"
    assert hypothesis.predicted_observations == []
    assert hypothesis.disconfirming_observations == []
    assert hypothesis.insufficiency_rationale is not None


def test_example_is_the_confirmatory_variant_with_non_empty_observations() -> None:
    """The example picked up by test_examples.py is the falsifiable
    (non-insufficient_evidence) variant (task-packets/E4-T03.yaml
    acceptance: the ordinary branch_role path, non-empty on both lists).
    """
    example_path = Path(__file__).resolve().parents[2] / "examples" / "hypothesis.example.json"
    document = json.loads(example_path.read_text())
    hypothesis = Hypothesis.model_validate(document)

    assert hypothesis.branch_role == "confirmatory"
    assert hypothesis.predicted_observations != []
    assert hypothesis.disconfirming_observations != []
    assert hypothesis.insufficiency_rationale is None
    assert hypothesis.status == "proposed"


def test_hypothesis_has_no_verified_supported_result_or_authoritative_field() -> None:
    """Structural not-a-claim-of-result (MRR-FR-014, AGENTS.md rule 7;
    task-packets/E4-T03.yaml acceptance: "the Hypothesis has no
    verified/supported/result field and its status enum contains no
    'verified' value"). Checked directly against the model's own declared
    fields and enum members, not merely against one example's values.
    """
    forbidden_field_names = {"verified", "supported", "result", "authoritative"}
    assert forbidden_field_names.isdisjoint(Hypothesis.model_fields)


def test_hypothesis_status_enum_has_exactly_four_values_and_no_verified() -> None:
    status_values = set(get_args(HypothesisStatus))
    assert status_values == {"proposed", "selected", "deferred", "rejected"}
    assert "verified" not in status_values


def test_branch_role_enum_is_exactly_the_six_mrr_fr_010_roles() -> None:
    role_values = set(get_args(BranchRole))
    assert role_values == {
        "confirmatory",
        "falsification",
        "alternative_explanation",
        "replication",
        "method_independent",
        "insufficient_evidence",
    }
    assert set(BRANCH_ROLES) == role_values
