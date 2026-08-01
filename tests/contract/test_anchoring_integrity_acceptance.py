"""Acceptance tests for task-packets/N2-T02b.yaml (contract tier, DB-free,
no-network): running ``mrr.services.anchoring_integrity.service
.AnchoringIntegrityService`` over the REAL committed
``corpora/archive-integrity/anchoring-batch.v1.json`` reports both dump
anchors matched and reproduces the acceptance oracle computed independently
at derivation by parsing both dumps offline
(docs/design/2026-07-25-n2-t02-derivation.md).

Acceptance-test mapping:

- AT1 (``mrr_k1t04_real_run_v2``: 18/17/4, 0 dangling anchors, 45 claim
  references with 0 dangling, 1 unanchored source, 0 unreferenced anchors)
  -> ``test_at1_k1t04_object_counts``, ``test_at1_k1t04_zero_violations``,
  ``test_at1_k1t04_one_unanchored_source_named``.
- AT2 (``mrr_run2_corroboration_floor_v1``: 36/34/8, 0 dangling anchors, 90
  claim references with 0 dangling, 2 unanchored sources, 0 unreferenced
  anchors) -> ``test_at2_run2_object_counts``,
  ``test_at2_run2_zero_violations``, ``test_at2_run2_two_unanchored_sources_named``.
- AT5 (honesty header names N2-T03, observations != errors) ->
  ``test_at5_honesty_header_present_and_names_n2_t03``.
- AT6 (existing suites pass unmodified; determinism) is asserted by the
  full ``make test``/``make test-contract`` run, not a single test here;
  determinism itself -> ``test_two_markdown_renders_are_byte_identical``,
  ``test_two_json_renders_are_byte_identical``.
- Every declared dump anchor matches the pinned, committed sha256 ->
  ``test_every_declared_dump_file_anchor_matches``; the batch declaring every
  committed dump in the first place -> ``test_batch_descriptor_covers_every_
  committed_dump`` (generalised 2026-08-01, see that test's own docstring for
  the gap the old fixed pair concealed).

AT1 and AT2 stay pinned to the two dumps their oracle was computed over.
Their numbers were derived by hand at the N2-T02 derivation from those exact
files; a third run has its own counts and does not belong in an oracle it was
not part of.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from mrr.domain.anchoring_integrity_report import (
    AnchoringIntegrityReport,
    DumpAnchoringReport,
    render_json,
    render_markdown,
)
from mrr.services.anchoring_integrity.service import AnchoringIntegrityService

REPO_ROOT = Path(__file__).resolve().parents[2]
BATCH_PATH = REPO_ROOT / "corpora" / "archive-integrity" / "anchoring-batch.v1.json"
K1T04_DUMP_PATH = REPO_ROOT / "archive" / "dumps" / "mrr_k1t04_real_run_v2.sql"
RUN2_DUMP_PATH = REPO_ROOT / "archive" / "dumps" / "mrr_run2_corroboration_floor_v1.sql"


def _build_report() -> AnchoringIntegrityReport:
    return AnchoringIntegrityService().build_report(BATCH_PATH)


def _dump(report: AnchoringIntegrityReport, schema_name: str) -> DumpAnchoringReport:
    return next(dump for dump in report.dumps if dump.schema_name == schema_name)


def _committed_dump_names() -> set[str]:
    return {path.stem for path in (REPO_ROOT / "archive" / "dumps").glob("*.sql")}


def test_batch_descriptor_covers_every_committed_dump() -> None:
    """Sanity check on the fixture itself before asserting anything about the
    service's own behavior over it.

    GENERALISED 2026-08-01. This test used to pin the batch to exactly the two
    dumps that existed when it was written — and that pin concealed a real gap:
    run 3 (e2e-claims) was produced on 2026-08-01, the batch had been frozen on
    2026-07-25, and the nightly integrity job therefore reported green over two
    thirds of the archive while this test agreed with it.

    The assertion is now the one that was meant all along: **every dump
    committed under archive/dumps/ is declared in the batch.** It fails when a
    future run is archived and the batch is not extended, instead of passing in
    silence.
    """
    import json

    document = json.loads(BATCH_PATH.read_text(encoding="utf-8"))
    declared = {entry["schema_name"] for entry in document["dumps"]}
    committed = _committed_dump_names()
    assert committed, "no committed dumps found — the archive cannot be empty"
    assert declared == committed, (
        "the anchoring batch and the committed archive have drifted apart: "
        f"archived but undeclared {sorted(committed - declared)!r}, "
        f"declared but not archived {sorted(declared - committed)!r}"
    )


def test_every_declared_dump_file_anchor_matches() -> None:
    report = _build_report()
    assert {dump.schema_name for dump in report.dumps} == _committed_dump_names()
    for dump in report.dumps:
        assert dump.file_anchor.matched is True, (
            f"{dump.schema_name}: declared {dump.file_anchor.declared_sha256!r} != actual "
            f"{dump.file_anchor.actual_sha256!r}"
        )
        assert dump.file_anchor.declared_sha256 == dump.file_anchor.actual_sha256


def test_dump_file_anchors_equal_the_real_committed_bytes_sha256() -> None:
    report = _build_report()
    k1t04 = _dump(report, "mrr_k1t04_real_run_v2")
    run2 = _dump(report, "mrr_run2_corroboration_floor_v1")
    assert k1t04.file_anchor.actual_sha256 == (
        f"sha256:{hashlib.sha256(K1T04_DUMP_PATH.read_bytes()).hexdigest()}"
    )
    assert run2.file_anchor.actual_sha256 == (
        f"sha256:{hashlib.sha256(RUN2_DUMP_PATH.read_bytes()).hexdigest()}"
    )


# ---------------------------------------------------------------------------
# AT1: mrr_k1t04_real_run_v2.
# ---------------------------------------------------------------------------


def test_at1_k1t04_object_counts() -> None:
    dump = _dump(_build_report(), "mrr_k1t04_real_run_v2")
    assert dump.object_counts.total == 67
    assert dump.object_counts.by_kind["SourceRecord"] == 18
    assert dump.object_counts.by_kind["EvidenceAnchor"] == 17
    assert dump.object_counts.by_kind["Claim"] == 4


def test_at1_k1t04_zero_violations() -> None:
    dump = _dump(_build_report(), "mrr_k1t04_real_run_v2")
    assert dump.violations.anchor_dangling == 0
    assert len(dump.anchor_links) == 17
    assert dump.violations.claim_reference_dangling == 0
    assert len(dump.claim_references) == 45


def test_at1_k1t04_one_unanchored_source_named() -> None:
    dump = _dump(_build_report(), "mrr_k1t04_real_run_v2")
    assert dump.observations.source_unanchored == 1
    assert dump.observations.anchor_unreferenced == 0
    unanchored_titles = [
        row.title for row in dump.source_coverage if row.status == "source_unanchored"
    ]
    assert unanchored_titles == ["The Next Biennial Should Be Curated by a Machine"]


# ---------------------------------------------------------------------------
# AT2: mrr_run2_corroboration_floor_v1.
# ---------------------------------------------------------------------------


def test_at2_run2_object_counts() -> None:
    dump = _dump(_build_report(), "mrr_run2_corroboration_floor_v1")
    assert dump.object_counts.total == 125
    assert dump.object_counts.by_kind["SourceRecord"] == 36
    assert dump.object_counts.by_kind["EvidenceAnchor"] == 34
    assert dump.object_counts.by_kind["Claim"] == 8


def test_at2_run2_zero_violations() -> None:
    dump = _dump(_build_report(), "mrr_run2_corroboration_floor_v1")
    assert dump.violations.anchor_dangling == 0
    assert len(dump.anchor_links) == 34
    assert dump.violations.claim_reference_dangling == 0
    assert len(dump.claim_references) == 90


def test_at2_run2_two_unanchored_sources_named() -> None:
    dump = _dump(_build_report(), "mrr_run2_corroboration_floor_v1")
    assert dump.observations.source_unanchored == 2
    assert dump.observations.anchor_unreferenced == 0
    unanchored_titles = [
        row.title for row in dump.source_coverage if row.status == "source_unanchored"
    ]
    assert unanchored_titles == [
        "The Next Biennial Should Be Curated by a Machine",
        "The Next Biennial Should Be Curated by a Machine",
    ]


# ---------------------------------------------------------------------------
# AT5: the honesty header.
# ---------------------------------------------------------------------------


def test_at5_honesty_header_present_and_names_n2_t03() -> None:
    report = _build_report()
    assert report.anchoring_is_not_support is True
    assert "N2-T03" in report.note
    assert "support" in report.note.lower()
    assert "observation" in report.note.lower()


# ---------------------------------------------------------------------------
# Determinism.
# ---------------------------------------------------------------------------


def test_two_markdown_renders_are_byte_identical() -> None:
    report = _build_report()
    assert render_markdown(report) == render_markdown(report)

    report_again = _build_report()
    assert render_markdown(report) == render_markdown(report_again)


def test_two_json_renders_are_byte_identical() -> None:
    report = _build_report()
    report_again = _build_report()
    assert render_json(report) == render_json(report_again)


def test_rendered_markdown_shows_both_dumps_and_the_unanchored_sources() -> None:
    report = _build_report()
    rendered = render_markdown(report)
    assert "mrr_k1t04_real_run_v2" in rendered
    assert "mrr_run2_corroboration_floor_v1" in rendered
    assert rendered.count("The Next Biennial Should Be Curated by a Machine") == 3
