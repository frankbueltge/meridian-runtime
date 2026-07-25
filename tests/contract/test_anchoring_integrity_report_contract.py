"""Contract test for ``mrr.domain.anchoring_integrity_report`` (task-packets/
N2-T02b.yaml R7, contract tier): "the R3 model validates its own instances
(extra='forbid' rejects an unknown field)". Mirrors ``tests/contract/
test_field_observation_report_contract.py`` — applied here to a PURE
PROJECTION model that has no JSON-Schema mirror at all (task-packets/
N2-T02b.yaml R3: "NOT a BaseObject, NO schemas/*.schema.json mirror").
"""

from __future__ import annotations

import pytest
from mrr.domain.anchoring_integrity import (
    AnchorCoverageVerdict,
    AnchorLinkVerdict,
    ClaimReferenceVerdict,
    SourceCoverageVerdict,
    check_dump_anchor,
)
from mrr.domain.anchoring_integrity_report import (
    AnchoringIntegrityReport,
    AnchorLinkVerdictRow,
    DumpAnchoringReport,
    ObservationCounts,
    ViolationCounts,
    build_anchoring_integrity_report,
    build_dump_anchoring_report,
)
from pydantic import ValidationError

_HASH = "sha256:" + "a" * 64


def _report() -> AnchoringIntegrityReport:
    file_anchor = check_dump_anchor("mrr_test", "mrr_test.sql", _HASH, _HASH)
    dump = build_dump_anchoring_report(
        schema_name="mrr_test",
        file_anchor=file_anchor,
        total_objects=3,
        object_counts_by_kind={"SourceRecord": 2, "EvidenceAnchor": 1},
        anchor_links=[
            AnchorLinkVerdict(anchor_id="ea1", source_record_id="sr1", status="anchor_resolved"),
        ],
        claim_references=[
            ClaimReferenceVerdict(
                claim_id="c1",
                anchor_id="ea1",
                relation_kind="evidence",
                status="claim_reference_resolved",
            ),
        ],
        source_coverage=[
            SourceCoverageVerdict(source_record_id="sr1", title="A Work", status="source_anchored"),
        ],
        anchor_coverage=[AnchorCoverageVerdict(anchor_id="ea1", status="anchor_referenced")],
    )
    return build_anchoring_integrity_report(
        batch_id="test-batch",
        observation_kind="archive-anchoring-integrity",
        audit_target="a test target",
        dumps=[dump],
    )


def test_anchoring_integrity_report_model_validates_a_well_formed_instance() -> None:
    report = _report()
    # Round-trips through model_dump/model_validate without error.
    AnchoringIntegrityReport.model_validate(report.model_dump())


def test_anchoring_integrity_report_rejects_an_unknown_top_level_field() -> None:
    payload = _report().model_dump()
    payload["not_a_declared_field"] = "surprise"
    with pytest.raises(ValidationError):
        AnchoringIntegrityReport.model_validate(payload)


def test_anchoring_integrity_report_anchoring_is_not_support_cannot_be_false() -> None:
    payload = _report().model_dump()
    payload["anchoring_is_not_support"] = False
    with pytest.raises(ValidationError):
        AnchoringIntegrityReport.model_validate(payload)


def test_anchoring_integrity_report_rejects_empty_dumps_tuple() -> None:
    payload = _report().model_dump()
    payload["dumps"] = []
    with pytest.raises(ValidationError):
        AnchoringIntegrityReport.model_validate(payload)


def test_dump_anchoring_report_rejects_an_unknown_field() -> None:
    payload = _report().model_dump()["dumps"][0]
    payload["extra_field"] = "not declared"
    with pytest.raises(ValidationError):
        DumpAnchoringReport.model_validate(payload)


def test_anchor_link_verdict_row_rejects_a_status_outside_the_closed_set() -> None:
    payload = {"anchor_id": "ea1", "source_record_id": "sr1", "status": "not-a-real-status"}
    with pytest.raises(ValidationError):
        AnchorLinkVerdictRow.model_validate(payload)


def test_violation_counts_rejects_an_unknown_field_including_a_collapsed_total() -> None:
    """The exact collapsing this packet must never do: a "problems" or
    "total_violations" field on the violations block."""
    payload = {"anchor_dangling": 0, "claim_reference_dangling": 0, "problems": 0}
    with pytest.raises(ValidationError):
        ViolationCounts.model_validate(payload)


def test_observation_counts_rejects_an_unknown_field() -> None:
    payload = {"source_unanchored": 0, "anchor_unreferenced": 0, "total_observations": 0}
    with pytest.raises(ValidationError):
        ObservationCounts.model_validate(payload)


def test_violation_counts_rejects_negative_values() -> None:
    payload = {"anchor_dangling": -1, "claim_reference_dangling": 0}
    with pytest.raises(ValidationError):
        ViolationCounts.model_validate(payload)


def test_violation_counts_and_observation_counts_are_distinct_model_types() -> None:
    """Structural proof that violations and observations can never be the
    SAME model instance — they are two different Pydantic classes with
    disjoint field sets, not two views of one shared "counts" type."""
    assert ViolationCounts.__name__ != ObservationCounts.__name__
    assert set(ViolationCounts.model_fields) != set(ObservationCounts.model_fields)
    assert set(ViolationCounts.model_fields).isdisjoint(set(ObservationCounts.model_fields))
