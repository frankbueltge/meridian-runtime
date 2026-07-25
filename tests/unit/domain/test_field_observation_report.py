"""Unit tests for ``mrr.domain.field_observation_report`` (task-packets/
R2-T01.yaml R2/R6, unit tier). DB-free, no-network — the embedded
``CitationAuditReport`` here is hand-built via
``mrr.domain.citation_audit_report.build_citation_audit_report`` over a
synthetic verdict, never read from the real committed fixture (that is
exercised separately, at the contract tier, in
tests/contract/test_field_observation_acceptance.py).
"""

from __future__ import annotations

from mrr.domain.citation_audit import CitationVerdict
from mrr.domain.citation_audit_report import CitationAuditReport, build_citation_audit_report
from mrr.domain.field_observation import AnchorCheckResult, check_anchor
from mrr.domain.field_observation_report import (
    FieldObservationReport,
    build_field_observation_report,
    render_json,
    render_markdown,
)

_OK_HASH = "sha256:" + "a" * 64
_OTHER_HASH = "sha256:" + "b" * 64


def _citation_audit_report() -> CitationAuditReport:
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
        manifest_path="m.json",
        snapshot_path="s.json",
        snapshot_sha256=_OK_HASH,
        verdicts=[verdict],
    )


def _anchor_results(
    *, manifest_ok: bool = True, snapshot_ok: bool = True
) -> list[AnchorCheckResult]:
    manifest_actual = _OK_HASH if manifest_ok else _OTHER_HASH
    snapshot_actual = _OK_HASH if snapshot_ok else _OTHER_HASH
    return [
        check_anchor("manifest", "m.json", _OK_HASH, manifest_actual),
        check_anchor("snapshot", "s.json", _OK_HASH, snapshot_actual),
    ]


def _report(anchor_results: list[AnchorCheckResult] | None = None) -> FieldObservationReport:
    return build_field_observation_report(
        batch_id="test-batch",
        observation_kind="citation_audit",
        audit_target="a test target",
        anchor_results=anchor_results if anchor_results is not None else _anchor_results(),
        citation_audit=_citation_audit_report(),
    )


# ---------------------------------------------------------------------------
# The honesty header — structural, not advisory (AT2).
# ---------------------------------------------------------------------------


def test_observation_is_not_optimization_header_is_true() -> None:
    report = _report()
    assert report.observation_is_not_optimization is True


def test_observation_note_names_r2_t02_and_r2_t03_as_out_of_scope() -> None:
    report = _report()
    assert "R2-T02" in report.observation_note
    assert "R2-T03" in report.observation_note


def test_observation_note_states_no_model_step() -> None:
    report = _report()
    assert "NO model" in report.observation_note or "no model" in report.observation_note.lower()


# ---------------------------------------------------------------------------
# Ordered anchor rows.
# ---------------------------------------------------------------------------


def test_anchor_rows_are_ordered_manifest_then_snapshot() -> None:
    report = _report()
    assert [row.role for row in report.anchors] == ["manifest", "snapshot"]


def test_anchor_rows_are_ordered_even_when_input_is_reversed() -> None:
    reversed_results = list(reversed(_anchor_results()))
    report = _report(anchor_results=reversed_results)
    assert [row.role for row in report.anchors] == ["manifest", "snapshot"]


def test_anchor_row_matched_true_when_hashes_are_equal() -> None:
    report = _report(anchor_results=_anchor_results(manifest_ok=True, snapshot_ok=True))
    assert all(row.matched for row in report.anchors)


def test_anchor_row_matched_false_when_hashes_differ() -> None:
    report = _report(anchor_results=_anchor_results(manifest_ok=False, snapshot_ok=True))
    matched_by_role = {row.role: row.matched for row in report.anchors}
    assert matched_by_role["manifest"] is False
    assert matched_by_role["snapshot"] is True


# ---------------------------------------------------------------------------
# The embedded, unchanged citation-audit report.
# ---------------------------------------------------------------------------


def test_citation_audit_is_embedded_unchanged() -> None:
    audit = _citation_audit_report()
    report = build_field_observation_report(
        batch_id="test-batch",
        observation_kind="citation_audit",
        audit_target="a test target",
        anchor_results=_anchor_results(),
        citation_audit=audit,
    )
    assert report.citation_audit == audit
    assert report.citation_audit.verifies_existence_not_support is True


# ---------------------------------------------------------------------------
# Determinism (task-packets/R2-T01.yaml invariant).
# ---------------------------------------------------------------------------


def test_render_markdown_is_byte_identical_across_two_calls() -> None:
    report = _report()
    assert render_markdown(report) == render_markdown(report)


def test_render_json_is_byte_identical_across_two_calls() -> None:
    report = _report()
    assert render_json(report) == render_json(report)


def test_render_markdown_of_two_equal_reports_built_separately_is_identical() -> None:
    report_a = _report()
    report_b = _report()
    assert render_markdown(report_a) == render_markdown(report_b)


def test_render_markdown_shows_the_anchor_table_and_honesty_header() -> None:
    report = _report()
    rendered = render_markdown(report)
    assert "observation_is_not_optimization" in rendered
    assert "manifest" in rendered
    assert "snapshot" in rendered
    assert "Embedded citation-audit report" in rendered
    # the embedded report's own honesty header is shown verbatim too.
    assert "verifies_existence_not_support" in rendered


def test_render_json_round_trips_via_model_validate() -> None:
    import json

    report = _report()
    payload = json.loads(render_json(report))
    revalidated = FieldObservationReport.model_validate(payload)
    assert revalidated == report
