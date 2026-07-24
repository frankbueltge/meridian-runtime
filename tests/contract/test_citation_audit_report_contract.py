"""Contract test for ``mrr.domain.citation_audit_report`` (task-packets/
N2-T01.yaml R6, contract tier): "the R2 model validates its own instances
(extra='forbid' rejects an unknown field)". Mirrors ``tests/contract/
test_agreement_report_contract.py`` — applied here to a PURE PROJECTION
model that has no JSON-Schema mirror at all (task-packets/N2-T01.yaml R2:
"NOT a BaseObject, NO schemas/*.schema.json mirror").
"""

from __future__ import annotations

import pytest
from mrr.domain.citation_audit import CitationVerdict
from mrr.domain.citation_audit_report import (
    CitationAuditReport,
    CitationVerdictRow,
    SummaryCounts,
    build_citation_audit_report,
)
from pydantic import ValidationError


def _report() -> CitationAuditReport:
    verdict = CitationVerdict(
        citation_id="c1",
        cited_as="Some Paper",
        identifier="arxiv:2511.02824",
        status="resolved",
        resolved_title="Some Resolved Title",
        reason="identifier resolves",
    )
    return build_citation_audit_report(
        audit_target="a test target",
        manifest_path="manifest.json",
        snapshot_path="snapshot.json",
        snapshot_sha256="sha256:" + "0" * 64,
        verdicts=[verdict],
    )


def test_citation_audit_report_model_validates_a_well_formed_instance() -> None:
    report = _report()
    # Round-trips through model_dump/model_validate without error.
    CitationAuditReport.model_validate(report.model_dump())


def test_citation_audit_report_rejects_an_unknown_top_level_field() -> None:
    payload = _report().model_dump()
    payload["not_a_declared_field"] = "surprise"

    with pytest.raises(ValidationError):
        CitationAuditReport.model_validate(payload)


def test_summary_counts_rejects_an_unknown_field() -> None:
    payload = {
        "resolved": 1,
        "not_found": 0,
        "title_mismatch": 0,
        "unverifiable": 0,
        "malformed": 0,
        "total": 1,
        "pass_fail": "pass",  # exactly the collapsed field this must never have
    }
    with pytest.raises(ValidationError):
        SummaryCounts.model_validate(payload)


def test_citation_verdict_row_rejects_an_unknown_field() -> None:
    payload = {
        "citation_id": "c1",
        "cited_as": "Some Paper",
        "identifier": "arxiv:2511.02824",
        "status": "resolved",
        "resolved_title": "Some Resolved Title",
        "reason": "identifier resolves",
        "extra_field": "not declared",
    }
    with pytest.raises(ValidationError):
        CitationVerdictRow.model_validate(payload)


def test_citation_verdict_row_rejects_a_status_outside_the_closed_set() -> None:
    payload = {
        "citation_id": "c1",
        "cited_as": "Some Paper",
        "identifier": "arxiv:2511.02824",
        "status": "unknown",  # not one of the five declared statuses
        "resolved_title": None,
        "reason": "x",
    }
    with pytest.raises(ValidationError):
        CitationVerdictRow.model_validate(payload)


def test_citation_audit_report_verifies_existence_field_cannot_be_false() -> None:
    payload = _report().model_dump()
    payload["verifies_existence_not_support"] = False

    with pytest.raises(ValidationError):
        CitationAuditReport.model_validate(payload)


def test_summary_counts_rejects_negative_values() -> None:
    payload = {
        "resolved": -1,
        "not_found": 0,
        "title_mismatch": 0,
        "unverifiable": 0,
        "malformed": 0,
        "total": 0,
    }
    with pytest.raises(ValidationError):
        SummaryCounts.model_validate(payload)
