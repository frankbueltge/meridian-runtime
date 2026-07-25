"""Unit tests for ``mrr.domain.anchoring_integrity`` (task-packets/
N2-T02b.yaml R2/R7, unit tier). DB-free, no-network — every input here is a
small, hand-built ``mrr.domain.archive_dump`` typed row, never a fixture
read from disk (the REAL committed archive dumps are exercised separately,
at the contract tier, in tests/contract/test_anchoring_integrity_acceptance.py).
"""

from __future__ import annotations

import pytest
from mrr.domain.anchoring_integrity import (
    AnchorCoverageVerdict,
    AnchorLinkVerdict,
    IntegrityGateError,
    SourceCoverageVerdict,
    anchor_coverage,
    check_anchor_links,
    check_and_gate,
    check_claim_references,
    check_dump_anchor,
    source_coverage,
)
from mrr.domain.archive_dump import ClaimRow, EvidenceAnchorRow, SourceRecordRow

_OK_HASH = "sha256:" + "a" * 64
_OTHER_HASH = "sha256:" + "b" * 64


# ---------------------------------------------------------------------------
# The fail-closed dump-hash gate (mirrors, does not reuse, R2-T01's).
# ---------------------------------------------------------------------------


def test_check_dump_anchor_ok_when_hashes_are_exactly_equal() -> None:
    result = check_dump_anchor("mrr_a", "some/path.sql", _OK_HASH, _OK_HASH)
    assert result.status == "dump_anchor_ok"


def test_check_dump_anchor_mismatch_when_hashes_differ() -> None:
    result = check_dump_anchor("mrr_a", "some/path.sql", _OK_HASH, _OTHER_HASH)
    assert result.status == "dump_anchor_mismatch"


def test_check_and_gate_does_not_raise_when_all_results_match() -> None:
    results = [
        check_dump_anchor("mrr_a", "a.sql", _OK_HASH, _OK_HASH),
        check_dump_anchor("mrr_b", "b.sql", _OK_HASH, _OK_HASH),
    ]
    check_and_gate(results)  # must not raise


def test_check_and_gate_raises_integrity_gate_error_on_a_mismatch() -> None:
    results = [check_dump_anchor("mrr_a", "a.sql", _OK_HASH, _OTHER_HASH)]
    with pytest.raises(IntegrityGateError) as excinfo:
        check_and_gate(results)
    assert excinfo.value.schema_name == "mrr_a"
    assert excinfo.value.path == "a.sql"
    assert excinfo.value.declared_sha256 == _OK_HASH
    assert excinfo.value.actual_sha256 == _OTHER_HASH


def test_check_and_gate_names_the_first_mismatch_in_schema_name_sorted_order() -> None:
    results = [
        check_dump_anchor("mrr_z", "z.sql", _OK_HASH, _OTHER_HASH),
        check_dump_anchor("mrr_a", "a.sql", _OK_HASH, _OTHER_HASH),
    ]
    with pytest.raises(IntegrityGateError) as excinfo:
        check_and_gate(results)
    assert excinfo.value.schema_name == "mrr_a"


def test_check_and_gate_supports_any_number_of_dumps_the_open_set() -> None:
    """Unlike ``mrr.domain.field_observation.BatchRole`` (a closed two-value
    set), a dump's ``schema_name`` is a plain ``str`` — the gate accepts any
    number of declared dumps, one, two, or many (task-packets/N2-T02b.yaml
    derived_decisions (d))."""
    results = [check_dump_anchor(f"mrr_{i}", f"{i}.sql", _OK_HASH, _OK_HASH) for i in range(5)]
    check_and_gate(results)  # must not raise


# ---------------------------------------------------------------------------
# Reference resolution — VIOLATIONS when dangling.
# ---------------------------------------------------------------------------


def test_check_anchor_links_resolves_when_source_record_exists() -> None:
    anchors = (EvidenceAnchorRow(anchor_id="ea1", source_record_id="sr1"),)
    verdicts = check_anchor_links(anchors, {"sr1"})
    assert verdicts == (
        AnchorLinkVerdict(anchor_id="ea1", source_record_id="sr1", status="anchor_resolved"),
    )


def test_check_anchor_links_dangling_when_source_record_absent() -> None:
    anchors = (EvidenceAnchorRow(anchor_id="ea1", source_record_id="sr-does-not-exist"),)
    verdicts = check_anchor_links(anchors, {"sr1"})
    assert verdicts[0].status == "anchor_dangling"


def test_check_anchor_links_sorted_by_anchor_id_regardless_of_input_order() -> None:
    anchors = (
        EvidenceAnchorRow(anchor_id="ea2", source_record_id="sr1"),
        EvidenceAnchorRow(anchor_id="ea1", source_record_id="sr1"),
    )
    verdicts = check_anchor_links(anchors, {"sr1"})
    assert [v.anchor_id for v in verdicts] == ["ea1", "ea2"]


def test_check_claim_references_resolves_both_evidence_and_counterevidence() -> None:
    claims = (
        ClaimRow(
            claim_id="c1",
            evidence_relations=("ea1",),
            counterevidence_relations=("ea2",),
        ),
    )
    verdicts = check_claim_references(claims, {"ea1", "ea2"})
    assert len(verdicts) == 2
    kinds = {(v.anchor_id, v.relation_kind) for v in verdicts}
    assert kinds == {("ea1", "evidence"), ("ea2", "counterevidence")}
    assert all(v.status == "claim_reference_resolved" for v in verdicts)


def test_check_claim_references_dangling_when_anchor_absent() -> None:
    claims = (
        ClaimRow(claim_id="c1", evidence_relations=("ea-missing",), counterevidence_relations=()),
    )
    verdicts = check_claim_references(claims, {"ea1"})
    assert verdicts[0].status == "claim_reference_dangling"


def test_check_claim_references_counts_every_individual_reference_not_deduplicated() -> None:
    """A claim's own reference COUNT (not distinct anchor set) matches the
    real acceptance oracle (45/90 references, not fewer distinct anchors)."""
    claims = (
        ClaimRow(claim_id="c1", evidence_relations=("ea1",), counterevidence_relations=("ea1",)),
    )
    verdicts = check_claim_references(claims, {"ea1"})
    assert len(verdicts) == 2


def test_check_claim_references_sorted_by_claim_id_then_anchor_id() -> None:
    claims = (
        ClaimRow(claim_id="c2", evidence_relations=("ea1",), counterevidence_relations=()),
        ClaimRow(claim_id="c1", evidence_relations=("ea2", "ea1"), counterevidence_relations=()),
    )
    verdicts = check_claim_references(claims, {"ea1", "ea2"})
    assert [(v.claim_id, v.anchor_id) for v in verdicts] == [
        ("c1", "ea1"),
        ("c1", "ea2"),
        ("c2", "ea1"),
    ]


# ---------------------------------------------------------------------------
# Coverage — OBSERVATIONS, never violations.
# ---------------------------------------------------------------------------


def test_source_coverage_anchored_when_an_anchor_points_at_it() -> None:
    sources = (SourceRecordRow(source_record_id="sr1", title="A Work"),)
    anchors = (EvidenceAnchorRow(anchor_id="ea1", source_record_id="sr1"),)
    verdicts = source_coverage(sources, anchors)
    assert verdicts == (
        SourceCoverageVerdict(source_record_id="sr1", title="A Work", status="source_anchored"),
    )


def test_source_coverage_unanchored_when_no_anchor_points_at_it() -> None:
    sources = (SourceRecordRow(source_record_id="sr1", title="A Work"),)
    verdicts = source_coverage(sources, anchors=())
    assert verdicts[0].status == "source_unanchored"
    assert verdicts[0].title == "A Work"


def test_anchor_coverage_referenced_when_a_claim_cites_it() -> None:
    anchors = (EvidenceAnchorRow(anchor_id="ea1", source_record_id="sr1"),)
    claims = (ClaimRow(claim_id="c1", evidence_relations=("ea1",), counterevidence_relations=()),)
    verdicts = anchor_coverage(anchors, claims)
    assert verdicts == (AnchorCoverageVerdict(anchor_id="ea1", status="anchor_referenced"),)


def test_anchor_coverage_unreferenced_when_no_claim_cites_it() -> None:
    anchors = (EvidenceAnchorRow(anchor_id="ea1", source_record_id="sr1"),)
    verdicts = anchor_coverage(anchors, claims=())
    assert verdicts[0].status == "anchor_unreferenced"


def test_anchor_coverage_checks_counterevidence_relations_too() -> None:
    anchors = (EvidenceAnchorRow(anchor_id="ea1", source_record_id="sr1"),)
    claims = (ClaimRow(claim_id="c1", evidence_relations=(), counterevidence_relations=("ea1",)),)
    verdicts = anchor_coverage(anchors, claims)
    assert verdicts[0].status == "anchor_referenced"


# ---------------------------------------------------------------------------
# The never-collapse invariant: a violation and an observation are
# DIFFERENT statuses of DIFFERENT types (AGENTS.md prohibited shortcut).
# ---------------------------------------------------------------------------


def test_a_dangling_reference_and_an_unanchored_source_are_different_statuses_and_types() -> None:
    """task-packets/N2-T02b.yaml R7: "an explicit test that a dangling
    reference and an unanchored source produce DIFFERENT statuses and land
    in DIFFERENT count blocks (the never-collapse invariant)"."""
    sources = (
        SourceRecordRow(source_record_id="sr-anchored", title="Anchored Work"),
        SourceRecordRow(source_record_id="sr-unanchored", title="Unanchored Work"),
    )
    anchors = (
        EvidenceAnchorRow(anchor_id="ea-dangling", source_record_id="sr-does-not-exist"),
        EvidenceAnchorRow(anchor_id="ea-ok", source_record_id="sr-anchored"),
    )

    link_verdicts = check_anchor_links(anchors, {sr.source_record_id for sr in sources})
    coverage_verdicts = source_coverage(sources, anchors)

    dangling = next(v for v in link_verdicts if v.anchor_id == "ea-dangling")
    unanchored = next(v for v in coverage_verdicts if v.source_record_id == "sr-unanchored")

    # Different closed sets entirely — never comparable, never unifiable.
    # (Compared as plain str here, not Literal: mypy already proves the two
    # Literal types are non-overlapping at the type level — the point of
    # this test is the runtime/documentation guarantee, not a type puzzle.)
    assert dangling.status == "anchor_dangling"
    assert unanchored.status == "source_unanchored"
    assert str(dangling.status) != str(unanchored.status)
    assert type(dangling).__name__ != type(unanchored).__name__

    # A resolved anchor and an anchored source are the "good" ends of their
    # OWN, separate closed sets — asserted here too so the test documents
    # all four values, not just the two "bad" ones.
    resolved = next(v for v in link_verdicts if v.anchor_id == "ea-ok")
    anchored = next(v for v in coverage_verdicts if v.source_record_id == "sr-anchored")
    assert resolved.status == "anchor_resolved"
    assert anchored.status == "source_anchored"
