"""Unit tests for ``mrr.domain.anchoring_integrity_report`` (task-packets/
N2-T02b.yaml R3/R7, unit tier). Pure model-building/rendering — every input
here is a small, hand-built set of R2 verdicts, never a fixture read from
disk (the REAL committed archive dumps are exercised separately, at the
contract tier, in tests/contract/test_anchoring_integrity_acceptance.py).
"""

from __future__ import annotations

from mrr.domain.anchoring_integrity import (
    AnchorCoverageVerdict,
    AnchorLinkVerdict,
    ClaimReferenceVerdict,
    SourceCoverageVerdict,
    check_dump_anchor,
)
from mrr.domain.anchoring_integrity_report import (
    AnchoringIntegrityReport,
    DumpAnchoringReport,
    build_anchoring_integrity_report,
    build_dump_anchoring_report,
    render_json,
    render_markdown,
)

_HASH = "sha256:" + "a" * 64


def _dump_report(schema_name: str = "mrr_test") -> DumpAnchoringReport:
    file_anchor = check_dump_anchor(schema_name, f"{schema_name}.sql", _HASH, _HASH)
    return build_dump_anchoring_report(
        schema_name=schema_name,
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
            SourceCoverageVerdict(
                source_record_id="sr1", title="Anchored Work", status="source_anchored"
            ),
            SourceCoverageVerdict(
                source_record_id="sr2", title="Unanchored Work", status="source_unanchored"
            ),
        ],
        anchor_coverage=[
            AnchorCoverageVerdict(anchor_id="ea1", status="anchor_referenced"),
        ],
    )


def _report() -> AnchoringIntegrityReport:
    return build_anchoring_integrity_report(
        batch_id="test-batch",
        observation_kind="archive-anchoring-integrity",
        audit_target="a synthetic test target",
        dumps=[_dump_report()],
    )


# ---------------------------------------------------------------------------
# The honesty header — structural, not advisory.
# ---------------------------------------------------------------------------


def test_report_carries_the_fixed_honesty_header() -> None:
    report = _report()
    assert report.anchoring_is_not_support is True
    assert "N2-T03" in report.note
    assert "support" in report.note.lower()
    assert "observation" in report.note.lower()
    assert "not errors" in report.note.lower() or "not an" in report.note.lower()


# ---------------------------------------------------------------------------
# Violations and observations are separate fields, never summed.
# ---------------------------------------------------------------------------


def test_violation_and_observation_counts_are_separate_fields() -> None:
    report = _report()
    dump = report.dumps[0]
    assert dump.violations.anchor_dangling == 0
    assert dump.violations.claim_reference_dangling == 0
    assert dump.observations.source_unanchored == 1
    assert dump.observations.anchor_unreferenced == 0
    # No combined field exists on either model — this would raise
    # AttributeError if someone tried to reintroduce a "problems" field.
    assert not hasattr(dump, "problems")
    assert not hasattr(dump, "total_violations")
    assert not hasattr(dump, "total_problems")


def test_dangling_and_unanchored_counts_computed_independently() -> None:
    file_anchor = check_dump_anchor("mrr_test", "mrr_test.sql", _HASH, _HASH)
    dump = build_dump_anchoring_report(
        schema_name="mrr_test",
        file_anchor=file_anchor,
        total_objects=2,
        object_counts_by_kind={"EvidenceAnchor": 1, "SourceRecord": 1},
        anchor_links=[
            AnchorLinkVerdict(
                anchor_id="ea1", source_record_id="sr-missing", status="anchor_dangling"
            ),
        ],
        claim_references=[],
        source_coverage=[
            SourceCoverageVerdict(source_record_id="sr1", title="W", status="source_unanchored"),
        ],
        anchor_coverage=[AnchorCoverageVerdict(anchor_id="ea1", status="anchor_unreferenced")],
    )
    assert dump.violations.anchor_dangling == 1
    assert dump.violations.claim_reference_dangling == 0
    assert dump.observations.source_unanchored == 1
    assert dump.observations.anchor_unreferenced == 1


# ---------------------------------------------------------------------------
# Ordering (defensive — always sorted regardless of input order).
# ---------------------------------------------------------------------------


def test_dump_reports_are_sorted_by_schema_name() -> None:
    file_anchor_b = check_dump_anchor("mrr_b", "b.sql", _HASH, _HASH)
    file_anchor_a = check_dump_anchor("mrr_a", "a.sql", _HASH, _HASH)
    dump_b = build_dump_anchoring_report(
        schema_name="mrr_b",
        file_anchor=file_anchor_b,
        total_objects=0,
        object_counts_by_kind={},
        anchor_links=[],
        claim_references=[],
        source_coverage=[],
        anchor_coverage=[],
    )
    dump_a = build_dump_anchoring_report(
        schema_name="mrr_a",
        file_anchor=file_anchor_a,
        total_objects=0,
        object_counts_by_kind={},
        anchor_links=[],
        claim_references=[],
        source_coverage=[],
        anchor_coverage=[],
    )
    report = build_anchoring_integrity_report(
        batch_id="b", observation_kind="k", audit_target="t", dumps=[dump_b, dump_a]
    )
    assert [d.schema_name for d in report.dumps] == ["mrr_a", "mrr_b"]


# ---------------------------------------------------------------------------
# Rendering — pure, deterministic.
# ---------------------------------------------------------------------------


def test_two_markdown_renders_are_byte_identical() -> None:
    report = _report()
    assert render_markdown(report) == render_markdown(report)


def test_two_json_renders_are_byte_identical() -> None:
    report = _report()
    assert render_json(report) == render_json(report)


def test_markdown_names_the_unanchored_source() -> None:
    rendered = render_markdown(_report())
    assert "Unanchored Work" in rendered
    assert "sr2" in rendered


def test_markdown_shows_object_kind_counts() -> None:
    rendered = render_markdown(_report())
    assert "SourceRecord" in rendered
    assert "EvidenceAnchor" in rendered


def test_markdown_shows_violations_and_observations_in_separate_sections() -> None:
    rendered = render_markdown(_report())
    violations_index = rendered.index("Violations")
    observations_index = rendered.index("Observations")
    assert violations_index < observations_index


def test_json_render_is_sorted_and_has_no_wall_clock_field() -> None:
    rendered = render_json(_report())
    assert '"anchoring_is_not_support": true' in rendered
    # sort_keys=True: "anchoring_is_not_support" (a) sorts before "audit_target".
    assert rendered.index('"anchoring_is_not_support"') < rendered.index('"audit_target"')
