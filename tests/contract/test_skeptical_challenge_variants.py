"""Contract tests for the SkepticalChallenge entity (task-packets/
E4-T04.yaml), beyond the generic example-driven checks
tests/contract/test_examples.py already runs.

examples/skeptical-challenge.example.json (picked up automatically by
test_examples.py) declares a ``supporting_source_ids`` entry. This module
covers the OTHER lawful variant -- a challenge that cites no source at all,
leaving ``supporting_source_ids`` absent and defaulting to an empty list --
mirroring tests/contract/test_hypothesis_variants.py's own "second lawful
variant, not just the example" precedent, plus the structural
proposal-only/closed-enum checks task-packets/E4-T04.yaml's acceptance
tests name directly.

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

from mrr.contracts import CHALLENGE_TYPES, ChallengeType, SkepticalChallenge

from scripts.check_contracts import SCHEMAS_DIR, build_registry, build_validator_for_schema

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "valid"
EXAMPLE_PATH = Path(__file__).resolve().parents[2] / "examples" / "skeptical-challenge.example.json"


def _validate_against_schema(document: dict[str, object]) -> None:
    schema = json.loads((SCHEMAS_DIR / "skeptical-challenge.schema.json").read_text())
    registry = build_registry()
    build_validator_for_schema(schema, registry).validate(document)


def test_challenge_with_no_supporting_sources_validates_and_defaults_to_empty_list() -> None:
    document = json.loads(
        (FIXTURES_DIR / "skeptical-challenge-no-supporting-sources.json").read_text()
    )

    _validate_against_schema(document)
    challenge = SkepticalChallenge.model_validate(document)

    assert challenge.challenge_type == "hidden_assumption"
    assert challenge.supporting_source_ids == []


def test_example_cites_a_supporting_source() -> None:
    """The example picked up by test_examples.py is the variant WITH a
    supporting_source_ids entry (task-packets/E4-T04.yaml acceptance: the
    ordinary path, a challenge that cites at least one source).
    """
    document = json.loads(EXAMPLE_PATH.read_text())
    challenge = SkepticalChallenge.model_validate(document)

    assert challenge.challenge_type == "counterevidence"
    assert challenge.supporting_source_ids != []
    assert challenge.target_claim_id
    assert challenge.target_claim_hash
    assert challenge.producing_model_profile_id
    assert challenge.producing_model_profile_hash


def test_skeptical_challenge_has_no_verdict_decision_or_claim_status_field() -> None:
    """Structural proposal-only guarantee (MRR-FR-070, AGENTS.md rule 7;
    task-packets/E4-T04.yaml acceptance: "the SkepticalChallenge has no
    verdict/decision/verified/resolved/claim_status field"). Checked
    directly against the model's own declared fields, not merely against
    one example's values.
    """
    forbidden_field_names = {
        "verdict",
        "decision",
        "verified",
        "resolved",
        "claim_status",
        "supported",
        "result",
        "authoritative",
    }
    assert forbidden_field_names.isdisjoint(SkepticalChallenge.model_fields)


def test_challenge_type_enum_is_exactly_the_four_mrr_fr_074_kinds_in_order() -> None:
    type_values = set(get_args(ChallengeType))
    assert type_values == {
        "counterevidence",
        "alternative_explanation",
        "scope_leakage",
        "hidden_assumption",
    }
    assert CHALLENGE_TYPES == (
        "counterevidence",
        "alternative_explanation",
        "scope_leakage",
        "hidden_assumption",
    )
    assert set(CHALLENGE_TYPES) == type_values
