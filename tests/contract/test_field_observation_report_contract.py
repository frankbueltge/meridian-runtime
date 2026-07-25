"""Contract test for ``mrr.domain.field_observation_report`` (task-packets/
R2-T01.yaml R6, contract tier): "the R2 model validates its own instances
(extra='forbid' rejects an unknown field)". Mirrors ``tests/contract/
test_citation_audit_report_contract.py`` — applied here to a PURE PROJECTION
model that has no JSON-Schema mirror at all (task-packets/R2-T01.yaml R2:
"NOT a BaseObject, NO schemas/*.schema.json mirror").
"""

from __future__ import annotations

import pytest
from mrr.domain.citation_audit import CitationVerdict
from mrr.domain.citation_audit_report import build_citation_audit_report
from mrr.domain.field_observation import check_anchor
from mrr.domain.field_observation_report import (
    AnchorRow,
    FieldObservationReport,
    build_field_observation_report,
)
from pydantic import ValidationError

_HASH = "sha256:" + "a" * 64


def _report() -> FieldObservationReport:
    verdict = CitationVerdict(
        citation_id="c1",
        cited_as="Some Paper",
        identifier="arxiv:2511.02824",
        status="resolved",
        resolved_title="Some Resolved Title",
        reason="identifier resolves",
    )
    citation_audit = build_citation_audit_report(
        audit_target="a test target",
        manifest_path="m.json",
        snapshot_path="s.json",
        snapshot_sha256=_HASH,
        verdicts=[verdict],
    )
    anchor_results = [
        check_anchor("manifest", "m.json", _HASH, _HASH),
        check_anchor("snapshot", "s.json", _HASH, _HASH),
    ]
    return build_field_observation_report(
        batch_id="test-batch",
        observation_kind="citation_audit",
        audit_target="a test target",
        anchor_results=anchor_results,
        citation_audit=citation_audit,
    )


def test_field_observation_report_model_validates_a_well_formed_instance() -> None:
    report = _report()
    # Round-trips through model_dump/model_validate without error.
    FieldObservationReport.model_validate(report.model_dump())


def test_field_observation_report_rejects_an_unknown_top_level_field() -> None:
    payload = _report().model_dump()
    payload["not_a_declared_field"] = "surprise"

    with pytest.raises(ValidationError):
        FieldObservationReport.model_validate(payload)


def test_anchor_row_rejects_an_unknown_field() -> None:
    payload = {
        "role": "manifest",
        "path": "m.json",
        "declared_sha256": _HASH,
        "actual_sha256": _HASH,
        "matched": True,
        "extra_field": "not declared",
    }
    with pytest.raises(ValidationError):
        AnchorRow.model_validate(payload)


def test_anchor_row_rejects_a_role_outside_the_closed_set() -> None:
    payload = {
        "role": "not-a-real-role",
        "path": "m.json",
        "declared_sha256": _HASH,
        "actual_sha256": _HASH,
        "matched": True,
    }
    with pytest.raises(ValidationError):
        AnchorRow.model_validate(payload)


def test_field_observation_report_observation_is_not_optimization_cannot_be_false() -> None:
    payload = _report().model_dump()
    payload["observation_is_not_optimization"] = False

    with pytest.raises(ValidationError):
        FieldObservationReport.model_validate(payload)


def test_field_observation_report_rejects_empty_anchors_tuple() -> None:
    """R1/R2 invariant: "the per-input anchor rows ... can [not] be absent"
    — enforced structurally via ``Field(min_length=1)`` on
    ``FieldObservationReport.anchors``.
    """
    payload = _report().model_dump()
    payload["anchors"] = []

    with pytest.raises(ValidationError):
        FieldObservationReport.model_validate(payload)


def test_field_observation_report_citation_audit_field_is_itself_validated() -> None:
    """The embedded ``citation_audit`` field is a full nested
    ``CitationAuditReport`` model, not a loose dict — an invalid nested
    payload (a status outside the closed set) is rejected here too.
    """
    payload = _report().model_dump()
    payload["citation_audit"]["citations"][0]["status"] = "not-a-real-status"

    with pytest.raises(ValidationError):
        FieldObservationReport.model_validate(payload)
