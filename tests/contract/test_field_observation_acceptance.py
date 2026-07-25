"""Acceptance tests for task-packets/R2-T01.yaml (contract tier, DB-free,
no-network): running ``mrr.services.field_observation.service
.FieldObservationService`` over the REAL committed
``corpora/e2e-survey/observation-batch.v1.json`` reports both integrity
anchors matched and embeds the frozen N2 evaluator's own report showing all
eight citations resolved, zero not_found — Routine 2's first read-only field
observation (task-packets/R2-T01.yaml derivation): integrity-verifying and
auditing the already-committed e2e-survey batch, fail-closed.

Acceptance-test mapping:

- AT1 ("both anchors matched, embedded audit 8/8 resolved, 0 not_found, and
  the embedded snapshot_sha256 equals the actual file sha256") ->
  ``test_at1_both_anchors_matched``,
  ``test_at1_embedded_audit_reports_eight_resolved_zero_not_found``,
  ``test_at1_embedded_snapshot_sha256_equals_actual_file_sha256``.
- AT2 ("honesty header present, R2-T02/R2-T03 named out of scope") ->
  ``test_at2_observation_header_present_and_names_out_of_scope``.
- AT5 ("descriptor-relative paths, CWD-independent") ->
  ``test_at5_result_is_identical_regardless_of_process_cwd``.
- Determinism (byte-identical renders) ->
  ``test_two_markdown_renders_of_the_real_batch_are_byte_identical``,
  ``test_two_json_renders_of_the_real_batch_are_byte_identical``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from mrr.domain.field_observation_report import FieldObservationReport, render_json, render_markdown
from mrr.services.field_observation.service import FieldObservationService

REPO_ROOT = Path(__file__).resolve().parents[2]
BATCH_PATH = REPO_ROOT / "corpora" / "e2e-survey" / "observation-batch.v1.json"
SNAPSHOT_PATH = REPO_ROOT / "corpora" / "e2e-survey" / "verification" / "resolution-snapshot.json"

_EXPECTED_CITATION_IDS = (
    "agent-laboratory",
    "claim-level-auditability-aar",
    "deeptrace",
    "independent-ai-scientist-eval",
    "inspectable-ai-for-science",
    "kosmos",
    "sakana-nature",
    "sciintegrity-bench",
)


def _build_report() -> FieldObservationReport:
    return FieldObservationService().build_report(BATCH_PATH)


def test_batch_descriptor_exists_and_declares_both_named_inputs() -> None:
    """Sanity check on the fixture itself (never edited by this packet,
    task-packets/R2-T01.yaml R3) before asserting anything about the
    service's own behavior over it.
    """
    import json

    document = json.loads(BATCH_PATH.read_text(encoding="utf-8"))
    assert document["inputs"]["manifest"]["path"] == "citations.manifest.json"
    assert document["inputs"]["snapshot"]["path"] == "verification/resolution-snapshot.json"


def test_at1_both_anchors_matched() -> None:
    report = _build_report()
    assert len(report.anchors) == 2
    for row in report.anchors:
        assert row.matched is True, (
            f"{row.role}: declared {row.declared_sha256!r} != actual {row.actual_sha256!r}"
        )
        assert row.declared_sha256 == row.actual_sha256


def test_at1_embedded_audit_reports_eight_resolved_zero_not_found() -> None:
    report = _build_report()
    summary = report.citation_audit.summary
    assert summary.total == 8
    assert summary.resolved == 8
    assert summary.not_found == 0
    assert summary.title_mismatch == 0
    assert summary.unverifiable == 0
    assert summary.malformed == 0

    seen_ids = {row.citation_id for row in report.citation_audit.citations}
    assert seen_ids == set(_EXPECTED_CITATION_IDS)


def test_at1_embedded_snapshot_sha256_equals_actual_file_sha256() -> None:
    report = _build_report()
    expected = f"sha256:{hashlib.sha256(SNAPSHOT_PATH.read_bytes()).hexdigest()}"
    assert report.citation_audit.snapshot_sha256 == expected

    # And the anchor row for "snapshot" itself agrees with the same value.
    snapshot_row = next(row for row in report.anchors if row.role == "snapshot")
    assert snapshot_row.actual_sha256 == expected
    assert snapshot_row.declared_sha256 == expected


def test_at2_observation_header_present_and_names_out_of_scope() -> None:
    report = _build_report()
    assert report.observation_is_not_optimization is True
    assert "R2-T02" in report.observation_note
    assert "R2-T03" in report.observation_note
    assert "read-only" in report.observation_note.lower()


def test_at5_result_is_identical_regardless_of_process_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """task-packets/R2-T01.yaml AT5: pointing --batch at the committed
    descriptor (by its absolute path) from a DIFFERENT process CWD still
    resolves its declared inputs relative to the descriptor's own directory
    and yields the identical clean result.
    """
    report_from_repo_root = _build_report()

    monkeypatch.chdir(tmp_path)
    report_from_elsewhere = FieldObservationService().build_report(BATCH_PATH.resolve())

    assert report_from_elsewhere == report_from_repo_root


def test_two_markdown_renders_of_the_real_batch_are_byte_identical() -> None:
    report = _build_report()
    assert render_markdown(report) == render_markdown(report)

    report_again = _build_report()
    assert render_markdown(report) == render_markdown(report_again)


def test_two_json_renders_of_the_real_batch_are_byte_identical() -> None:
    report = _build_report()
    report_again = _build_report()
    assert render_json(report) == render_json(report_again)


def test_rendered_markdown_shows_all_eight_citation_ids_and_the_anchor_table() -> None:
    report = _build_report()
    rendered = render_markdown(report)
    for citation_id in _EXPECTED_CITATION_IDS:
        assert citation_id in rendered
    assert "manifest" in rendered
    assert "snapshot" in rendered
    assert rendered.count("| resolved |") == 8
