"""Unit tests for ``mrr.domain.source_independence`` (task-packets/
K1-T03.yaml) — entirely pure, no PostgreSQL, no ``sqlalchemy.Engine``.

Acceptance-test mapping (task-packets/K1-T03.yaml):

- "distinct_independent_source_family_count over rows sharing one
  source_family_id counts them ONCE; over rows with distinct or null
  source_family_id (each falling back to its own source_record_id) counts
  them separately" -> ``test_shared_source_family_id_counts_once``,
  ``test_distinct_or_null_source_family_id_counts_separately``.
- "an 'unverifiable' row is excluded from the count regardless of its
  source_family_id" -> ``test_unverifiable_row_excluded_regardless_of_family``.
- The order-independence/never-exceeds-verified-count invariants are pinned
  as a property test in
  ``tests/property/test_source_independence_properties.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from mrr.domain.source_independence import (
    distinct_independent_source_family_count,
    family_key,
    has_sufficient_independent_source_families,
)


@dataclass(frozen=True, slots=True)
class _Row:
    """A minimal, structural stand-in satisfying
    ``mrr.domain.source_independence.SourceFamilyRow`` — proves the module
    genuinely operates on ANY structurally-conforming type, not only a real
    ``mrr.contracts.evidence_matrix.EvidenceMatrixRow``.
    """

    source_family_id: str | None
    source_record_id: str
    verification_status: str


def _row(
    source_record_id: str,
    *,
    source_family_id: str | None = None,
    verification_status: str = "verified",
) -> _Row:
    return _Row(
        source_family_id=source_family_id,
        source_record_id=source_record_id,
        verification_status=verification_status,
    )


def test_family_key_uses_source_family_id_when_present() -> None:
    row = _row("source-1", source_family_id="family-a")
    assert family_key(row) == "family-a"


def test_family_key_falls_back_to_source_record_singleton_when_null() -> None:
    row = _row("source-1", source_family_id=None)
    assert family_key(row) == "source:source-1"


def test_shared_source_family_id_counts_once() -> None:
    rows = [
        _row("source-1", source_family_id="family-a"),
        _row("source-2", source_family_id="family-a"),
    ]
    assert distinct_independent_source_family_count(rows) == 1


def test_distinct_or_null_source_family_id_counts_separately() -> None:
    rows = [
        _row("source-1", source_family_id="family-a"),
        _row("source-2", source_family_id="family-b"),
        _row("source-3", source_family_id=None),
        _row("source-4", source_family_id=None),
    ]
    # family-a, family-b, source:source-3, source:source-4 -> 4 distinct.
    assert distinct_independent_source_family_count(rows) == 4


def test_unverifiable_row_excluded_regardless_of_family() -> None:
    rows = [
        _row("source-1", source_family_id="family-a", verification_status="verified"),
        _row("source-2", source_family_id="family-b", verification_status="unverifiable"),
        _row("source-3", source_family_id=None, verification_status="unverifiable"),
    ]
    assert distinct_independent_source_family_count(rows) == 1


def test_pending_row_excluded_too() -> None:
    rows = [
        _row("source-1", source_family_id="family-a", verification_status="verified"),
        _row("source-2", source_family_id="family-b", verification_status="pending"),
    ]
    assert distinct_independent_source_family_count(rows) == 1


def test_empty_rows_count_zero() -> None:
    assert distinct_independent_source_family_count([]) == 0


def test_has_sufficient_independent_source_families_threshold() -> None:
    rows = [
        _row("source-1", source_family_id="family-a"),
        _row("source-2", source_family_id="family-b"),
    ]
    assert has_sufficient_independent_source_families(rows, minimum=2) is True
    assert has_sufficient_independent_source_families(rows, minimum=3) is False


def test_has_sufficient_independent_source_families_rejects_negative_minimum() -> None:
    with pytest.raises(ValueError, match="minimum"):
        has_sufficient_independent_source_families([], minimum=-1)


def test_a_real_evidence_matrix_row_satisfies_the_protocol_structurally() -> None:
    """A real ``mrr.contracts.evidence_matrix.EvidenceMatrixRow`` structurally
    satisfies ``SourceFamilyRow`` with no adapter needed — this module is a
    genuinely new, standalone counting rule, not a reimplementation coupled
    to the executor's own pre-persistence row shape.
    """
    from mrr.contracts.evidence_matrix import EvidenceMatrixRow

    row = EvidenceMatrixRow.model_validate(
        {
            "row_id": "row-1",
            "source_record_id": "urn:mrr:source-record:01J00000000000000000000300",
            "verification_status": "verified",
            "claim_relevant_finding": "A finding.",
            "extraction": {},
        }
    )
    assert distinct_independent_source_family_count([row]) == 1
    assert family_key(row) == "source:urn:mrr:source-record:01J00000000000000000000300"
