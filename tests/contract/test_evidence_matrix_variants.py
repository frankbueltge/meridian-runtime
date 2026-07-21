"""Contract tests for ``EvidenceMatrix`` (task-packets/K1-T01.yaml) beyond
the generic example-driven checks tests/contract/test_examples.py already
runs.

The unverifiable-row-missing-reason failure case is covered by
tests/contract/fixtures/invalid/evidence-matrix-unverifiable-missing-reason.json
via tests/contract/test_negative_fixtures.py, not duplicated here. Two rows
sharing the same `row_id` but differing in every other field is NOT
expressible as a JSON Schema `uniqueItems` check (no per-subfield
uniqueness construct across array items exists in JSON Schema), so — like
``ConceptCharter``'s identical situation, see
tests/contract/test_concept_charter_variants.py — it is tested here
directly against the Pydantic model only.

Acceptance-test mapping (task-packets/K1-T01.yaml):

- "EvidenceMatrix — an EvidenceMatrix with an empty rows list validates
  successfully (MRR-MTH-011)" -> ``test_empty_rows_list_validates_successfully``.
- "... two rows sharing the same row_id within one matrix are rejected by
  the Pydantic model_validator" ->
  ``test_duplicate_row_id_with_differing_content_rejected_by_pydantic``.
- "a row with verification_status: 'unverifiable' and a non-empty
  unverifiable_reason is accepted" ->
  ``test_unverifiable_row_with_reason_is_accepted``.

Acceptance-test mapping (task-packets/K1-T03b.yaml, MRR-MTH-018 sensitivity
variations — [contract tier]):

- "an EvidenceMatrix document with a non-empty, valid
  sensitivity_analysis_results array validates against BOTH the schema and
  Pydantic" -> ``test_valid_sensitivity_analysis_results_are_accepted``.
- "the SAME document with two entries sharing an identical
  (variation_entry_id, applies_to_analysis) pair is REJECTED at the Pydantic
  tier" -> the duplicate-pair fixture
  tests/contract/fixtures/invalid/evidence-matrix-sensitivity-result-duplicate-pair.json,
  picked up by tests/contract/test_negative_fixtures.py's own glob-based
  discovery (that fixture's two entries are fully byte-identical, so — like
  ConceptCharter.entries' own precedent, unlike EvidenceMatrixRow.row_id's —
  it is ALSO rejected by the schema's own `uniqueItems: true`, not only by
  the Pydantic model_validator).
- an outcome of 'insufficient_evidence' with a null decision_rationale is
  rejected -> ``test_sensitivity_result_insufficient_evidence_without_rationale_is_rejected``.
- distinct (variation_entry_id, applies_to_analysis) pairs (same
  variation_entry_id, different applies_to_analysis) are accepted ->
  ``test_sensitivity_results_with_distinct_pairs_are_accepted``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from mrr.contracts.evidence_matrix import EvidenceMatrix
from pydantic import ValidationError

from scripts.check_contracts import SCHEMAS_DIR, build_registry, build_validator_for_schema

_VALID_HASH = "sha256:" + "4" * 64


def _base_document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "id": "urn:mrr:evidence-matrix:01J00000000000000000000240",
        "api_version": "mrr/v1alpha1",
        "kind": "EvidenceMatrix",
        "practice_id": "urn:mrr:practice:01J00000000000000000000001",
        "revision": 1,
        "created_at": "2026-07-21T10:00:00Z",
        "created_by": "urn:mrr:person:01J00000000000000000000002",
        "content_hash": _VALID_HASH,
        "protocol_id": "urn:mrr:method-protocol:01J00000000000000000000230",
        "question_id": "urn:mrr:question-model:01J00000000000000000000210",
        "rows": [],
        "status": "draft",
    }
    document.update(overrides)
    return document


def _validate_against_schema(document: dict[str, Any]) -> None:
    schema = json.loads((SCHEMAS_DIR / "evidence-matrix.schema.json").read_text())
    registry = build_registry()
    build_validator_for_schema(schema, registry).validate(document)


def test_empty_rows_list_validates_successfully() -> None:
    document = _base_document(rows=[])

    _validate_against_schema(document)
    matrix = EvidenceMatrix.model_validate(document)

    assert matrix.rows == []


def _row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "row_id": "row-001",
        "source_record_id": "urn:mrr:source-record:01J00000000000000000000302",
        "evidence_anchor_id": None,
        "source_family_id": None,
        "verification_status": "verified",
        "unverifiable_reason": None,
        "claim_relevant_finding": "A finding.",
        "extraction": {},
    }
    row.update(overrides)
    return row


def test_unverifiable_row_with_reason_is_accepted() -> None:
    document = _base_document(
        rows=[
            _row(
                verification_status="unverifiable",
                unverifiable_reason="training provenance could not be confirmed",
            )
        ]
    )

    _validate_against_schema(document)
    matrix = EvidenceMatrix.model_validate(document)

    assert matrix.rows[0].verification_status == "unverifiable"


def test_duplicate_row_id_with_differing_content_rejected_by_pydantic() -> None:
    """Same row_id, different content — a JSON Schema uniqueItems check
    would NOT catch this (the two items are not deeply equal), but the
    Pydantic model_validator must still reject it.
    """
    document = _base_document(
        rows=[
            _row(row_id="row-001", claim_relevant_finding="First finding."),
            _row(row_id="row-001", claim_relevant_finding="A different, conflicting finding."),
        ]
    )

    # Deliberately NOT asserted against JSON Schema here — see module docstring.
    with pytest.raises(ValidationError, match="row_id"):
        EvidenceMatrix.model_validate(document)


def test_two_rows_with_distinct_row_ids_are_accepted() -> None:
    document = _base_document(
        rows=[
            _row(row_id="row-001"),
            _row(row_id="row-002"),
        ]
    )

    _validate_against_schema(document)
    matrix = EvidenceMatrix.model_validate(document)

    assert [row.row_id for row in matrix.rows] == ["row-001", "row-002"]


# ---------------------------------------------------------------------------
# task-packets/K1-T03b.yaml (MRR-MTH-018 sensitivity variations).
# ---------------------------------------------------------------------------


def _sensitivity_result(**overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "variation_entry_id": "variant-a",
        "applies_to_analysis": "candidate-x",
        "outcome": "supported",
        "included_source_count": 2,
        "verified_source_count": 2,
        "distinct_independent_supporting_family_count": 2,
        "distinct_independent_contradicting_family_count": 0,
        "decision_rationale": None,
        "matches_base_outcome": True,
    }
    result.update(overrides)
    return result


def test_valid_sensitivity_analysis_results_are_accepted() -> None:
    document = _base_document(rows=[], sensitivity_analysis_results=[_sensitivity_result()])

    _validate_against_schema(document)
    matrix = EvidenceMatrix.model_validate(document)

    assert matrix.sensitivity_analysis_results is not None
    assert len(matrix.sensitivity_analysis_results) == 1
    assert matrix.sensitivity_analysis_results[0].variation_entry_id == "variant-a"


def test_absent_sensitivity_analysis_results_defaults_to_none() -> None:
    document = _base_document(rows=[])

    _validate_against_schema(document)
    matrix = EvidenceMatrix.model_validate(document)

    assert matrix.sensitivity_analysis_results is None


def test_sensitivity_result_insufficient_evidence_without_rationale_is_rejected() -> None:
    document = _base_document(
        rows=[],
        sensitivity_analysis_results=[
            _sensitivity_result(outcome="insufficient_evidence", decision_rationale=None)
        ],
    )

    with pytest.raises(ValidationError, match="decision_rationale"):
        EvidenceMatrix.model_validate(document)


def test_sensitivity_result_insufficient_evidence_with_rationale_is_accepted() -> None:
    document = _base_document(
        rows=[],
        sensitivity_analysis_results=[
            _sensitivity_result(
                outcome="insufficient_evidence",
                decision_rationale="0 included sources under this variation.",
                distinct_independent_supporting_family_count=0,
                distinct_independent_contradicting_family_count=0,
                included_source_count=0,
                verified_source_count=0,
            )
        ],
    )

    _validate_against_schema(document)
    matrix = EvidenceMatrix.model_validate(document)

    assert matrix.sensitivity_analysis_results is not None
    assert matrix.sensitivity_analysis_results[0].outcome == "insufficient_evidence"


def test_sensitivity_results_with_distinct_pairs_are_accepted() -> None:
    document = _base_document(
        rows=[],
        sensitivity_analysis_results=[
            _sensitivity_result(applies_to_analysis="candidate-x"),
            _sensitivity_result(applies_to_analysis="candidate-y"),
            _sensitivity_result(variation_entry_id="variant-b", applies_to_analysis="candidate-x"),
        ],
    )

    _validate_against_schema(document)
    matrix = EvidenceMatrix.model_validate(document)

    assert matrix.sensitivity_analysis_results is not None
    assert len(matrix.sensitivity_analysis_results) == 3


def test_sensitivity_results_with_duplicate_pair_rejected_by_pydantic() -> None:
    """Same (variation_entry_id, applies_to_analysis) pair, differing
    outcome — the stronger same-pair-different-content case a JSON Schema
    `uniqueItems` check would NOT catch (the two items are not deeply
    equal), mirroring `test_duplicate_row_id_with_differing_content_
    rejected_by_pydantic`'s own identical rationale above. The fully
    byte-identical duplicate case is separately covered by the committed
    fixture tests/contract/fixtures/invalid/evidence-matrix-sensitivity-
    result-duplicate-pair.json via test_negative_fixtures.py.
    """
    document = _base_document(
        rows=[],
        sensitivity_analysis_results=[
            _sensitivity_result(outcome="supported"),
            _sensitivity_result(outcome="contested"),
        ],
    )

    with pytest.raises(ValidationError, match="duplicate SensitivityAnalysisResult pair"):
        EvidenceMatrix.model_validate(document)
