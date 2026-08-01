"""task-packets/N1-T02.yaml AT5: label isolation survives into MB-CLS.

``benchmarks/meridianbench/tests/test_harness_label_isolation.py`` already
proves the harness itself cannot hand a label to a system under test. This file
proves MB-CLS does not undo that — which is the live risk, because the gold
set's ON-DISK form carries the excerpt and the answer in the same JSON object,
and something has to split them.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from benchmarks.meridianbench.harness import BenchmarkCase
from benchmarks.meridianbench.suites.mb_cls import (
    MbClsExpected,
    MbClsInput,
    collect_predictions,
    load_cases,
    load_gold_set_for_tests,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE = REPO_ROOT / "benchmarks" / "meridianbench" / "fixtures" / "mb-cls-v2.synthetic.json"


def _cases() -> tuple[BenchmarkCase[MbClsInput, MbClsExpected], ...]:
    return load_cases(load_gold_set_for_tests(FIXTURE))


def test_at5_the_input_type_carries_no_field_from_which_the_answer_follows() -> None:
    field_names = {field.name for field in dataclasses.fields(MbClsInput)}
    assert field_names == {"case_id", "excerpt", "claim_text", "categories", "criteria"}
    # Named negatives, so that adding any of these later fails here first.
    assert "expected_relation" not in field_names
    assert "relation" not in field_names
    assert "expected" not in field_names
    assert "rationale" not in field_names


def test_at5_the_value_handed_to_a_system_under_test_is_an_input_never_a_case() -> None:
    seen: list[object] = []

    def spy(value: MbClsInput) -> str:
        seen.append(value)
        return "supports"

    cases = _cases()
    collect_predictions(spy, cases)

    assert len(seen) == len(cases)
    assert all(isinstance(value, MbClsInput) for value in seen)
    assert not any(isinstance(value, BenchmarkCase) for value in seen)
    assert not any(isinstance(value, MbClsExpected) for value in seen)


def test_metadata_never_carries_the_gold_class_even_though_the_file_does() -> None:
    # The committed fixture DOES carry a `gold_class` metadata key for human
    # reporting. If load_cases passed metadata through verbatim, the answer
    # would reach a system under test by the back door the harness docstring
    # warns about ("never a channel for anything a scorer needs").
    raw_metadata_keys = {
        key
        for case in load_gold_set_for_tests(FIXTURE).cases
        for key in (case.get("metadata") or {})
    }
    assert "gold_class" in raw_metadata_keys

    for case in _cases():
        assert "gold_class" not in case.metadata
        assert case.expected.relation not in case.metadata.values()


def test_cases_carry_the_question_the_criteria_and_the_label_space() -> None:
    # The converse of isolation: withholding the criteria would measure
    # whether a classifier can guess a convention, not whether it can read.
    case = _cases()[0]
    assert case.input.claim_text
    assert case.input.excerpt
    assert case.input.categories == (
        "supports",
        "contradicts",
        "qualifies",
        "contextualizes",
    )
    assert set(case.input.criteria) == set(case.input.categories)


def test_every_expected_relation_is_a_declared_category_or_honestly_absent() -> None:
    cases = _cases()
    assert len(cases) == 23
    decidable = [c for c in cases if not c.expected.undecidable]
    undecidable = [c for c in cases if c.expected.undecidable]
    assert len(decidable) == 20
    assert len(undecidable) == 3

    for case in decidable:
        assert case.expected.relation in case.input.categories
        assert case.expected.rationale

    for case in undecidable:
        # No label, and no placeholder standing in for one. A stringified
        # "None" here would be a fabricated answer — which is exactly the bug
        # this assertion was written after finding.
        assert case.expected.relation is None
        assert case.expected.rationale is None
        assert case.expected.undecidable_reason


def test_the_committed_fixture_validates_against_the_gold_label_set_schema() -> None:
    # AGENTS.md rule 6 — "all externally visible data structures MUST be
    # schema-validated" — satisfied by EXECUTING the schema, not by shipping
    # one. A schema nothing runs is documentation that drifts.
    #
    # The schema deliberately does not live under schemas/: that directory is
    # reserved for governed contract entities and tests/contract/test_examples.py
    # enforces the reservation. See the schema's own description.
    import json

    import jsonschema

    schema = json.loads(
        (
            REPO_ROOT / "benchmarks" / "meridianbench" / "fixtures" / "gold-label-set.schema.json"
        ).read_text(encoding="utf-8")
    )
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    jsonschema.validate(document, schema)


def test_the_schema_rejects_a_set_that_is_missing_its_label_provenance() -> None:
    # The one field that must never be optional: a standard that cannot say
    # where its answers came from is not a standard.
    import json

    import jsonschema
    import pytest

    schema = json.loads(
        (
            REPO_ROOT / "benchmarks" / "meridianbench" / "fixtures" / "gold-label-set.schema.json"
        ).read_text(encoding="utf-8")
    )
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    del document["label_provenance"]

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(document, schema)
