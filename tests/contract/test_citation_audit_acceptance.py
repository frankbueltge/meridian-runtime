"""Acceptance tests for task-packets/N2-T01.yaml (contract tier, DB-free,
no-network): running ``mrr.services.citation_audit.service
.CitationAuditService`` over the REAL committed
``corpora/e2e-survey/citations.manifest.json`` and its committed
``corpora/e2e-survey/verification/resolution-snapshot.json`` reports all
eight citations ``resolved`` — the reflexive first use of N2 (task-packets/
N2-T01.yaml derivation): the /e2e-automation survey, whose own subject is
that AI research systems fabricate citations, must itself survive its
citations being checked.

Acceptance-test mapping:

- AT1 ("all eight resolved, zero not_found/title_mismatch/malformed, by
  count and by per-citation status") ->
  ``test_at1_all_eight_citations_resolved_by_count``,
  ``test_at1_every_citation_status_is_resolved_individually``.
- AT2 ("honesty header present, N2-T02/N2-T03 named out of scope") ->
  ``test_at2_existence_not_support_header_present_and_names_out_of_scope``.
- AT3 ("snapshot sha256 in the report equals the actual file sha256") ->
  ``test_at3_snapshot_sha256_in_report_equals_actual_file_sha256``.
- AT5 ("two renders of the real set are byte-identical") ->
  ``test_at5_two_markdown_renders_of_the_real_set_are_byte_identical``,
  ``test_at5_two_json_renders_of_the_real_set_are_byte_identical``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from mrr.domain.citation_audit_report import CitationAuditReport, render_json, render_markdown
from mrr.services.citation_audit.service import CitationAuditService

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "corpora" / "e2e-survey" / "citations.manifest.json"
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


def _build_report() -> CitationAuditReport:
    return CitationAuditService().build_report(MANIFEST_PATH, SNAPSHOT_PATH)


def test_fixture_files_exist_and_declare_eight_citations() -> None:
    """Sanity check on the fixtures themselves (never edited by this
    packet, task-packets/N2-T01.yaml R3) before asserting anything about the
    service's own behavior over them.
    """
    import json

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert len(manifest["citations"]) == 8
    snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert len(snapshot["resolutions"]) == 8


def test_at1_all_eight_citations_resolved_by_count() -> None:
    report = _build_report()
    assert report.summary.total == 8
    assert report.summary.resolved == 8
    assert report.summary.not_found == 0
    assert report.summary.title_mismatch == 0
    assert report.summary.unverifiable == 0
    assert report.summary.malformed == 0


def test_at1_every_citation_status_is_resolved_individually() -> None:
    report = _build_report()
    seen_ids = {row.citation_id for row in report.citations}
    assert seen_ids == set(_EXPECTED_CITATION_IDS)
    for row in report.citations:
        assert row.status == "resolved", f"{row.citation_id}: expected resolved, got {row.status}"
        assert row.resolved_title is not None


def test_at1_citations_are_ordered_by_citation_id() -> None:
    report = _build_report()
    ids = [row.citation_id for row in report.citations]
    assert ids == sorted(ids)


def test_at2_existence_not_support_header_present_and_names_out_of_scope() -> None:
    report = _build_report()
    assert report.verifies_existence_not_support is True
    assert "N2-T02" in report.existence_note
    assert "N2-T03" in report.existence_note
    assert "SUPPORT" in report.existence_note


def test_at3_snapshot_sha256_in_report_equals_actual_file_sha256() -> None:
    report = _build_report()
    expected = f"sha256:{hashlib.sha256(SNAPSHOT_PATH.read_bytes()).hexdigest()}"
    assert report.snapshot_sha256 == expected


def test_at3_changing_the_snapshot_changes_the_reported_hash(tmp_path: Path) -> None:
    """AT3's second half: "changing the snapshot changes the reported hash"
    — exercised against a scratch COPY of the real snapshot (the committed
    original is never edited, task-packets/N2-T01.yaml R3/stop_conditions).
    """
    import json
    import shutil

    scratch_manifest = tmp_path / "citations.manifest.json"
    scratch_snapshot_dir = tmp_path / "verification"
    scratch_snapshot_dir.mkdir()
    scratch_snapshot = scratch_snapshot_dir / "resolution-snapshot.json"

    shutil.copyfile(MANIFEST_PATH, scratch_manifest)
    shutil.copyfile(SNAPSHOT_PATH, scratch_snapshot)

    original_report = CitationAuditService().build_report(scratch_manifest, scratch_snapshot)

    document = json.loads(scratch_snapshot.read_text(encoding="utf-8"))
    document["resolutions"][0]["resolved_detail"] = "a byte changed for this test only"
    scratch_snapshot.write_text(json.dumps(document), encoding="utf-8")

    changed_report = CitationAuditService().build_report(scratch_manifest, scratch_snapshot)

    assert original_report.snapshot_sha256 != changed_report.snapshot_sha256


def test_at5_two_markdown_renders_of_the_real_set_are_byte_identical() -> None:
    report = _build_report()
    assert render_markdown(report) == render_markdown(report)

    report_again = _build_report()
    assert render_markdown(report) == render_markdown(report_again)


def test_at5_two_json_renders_of_the_real_set_are_byte_identical() -> None:
    report = _build_report()
    report_again = _build_report()
    assert render_json(report) == render_json(report_again)


def test_rendered_markdown_of_the_real_fixture_shows_eight_row_table() -> None:
    report = _build_report()
    rendered = render_markdown(report)
    for citation_id in _EXPECTED_CITATION_IDS:
        assert citation_id in rendered
    assert rendered.count("| resolved |") == 8
