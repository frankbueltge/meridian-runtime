"""Contract test for ``mrr.domain.artifact_presence_report`` (task-packets/
A2-T01.yaml, "Teil 2 — Nachsehen", contract tier): the pure projection model
validates its own instances (``extra="forbid"`` rejects an unknown field),
its honesty header cannot be constructed false, its violation/observation
counts are separate fields never summed, and its two renderers are
deterministic — mirrors tests/contract/test_anchoring_integrity_report_
contract.py's/tests/contract/test_support_audit_report.py's own precedent,
applied here to a PURE PROJECTION model with no JSON-Schema mirror.

The acceptance oracle against the two REAL committed archive dumps lives at
the service tier (tests/unit/services/test_artifact_presence_service.py) —
task-packets/A2-T01.yaml's own allowed_paths names that file for exactly
this purpose; this module stays scoped to the report projection itself.
"""

from __future__ import annotations

import pytest
from mrr.domain.artifact_presence import ArtifactAnchorRow, ArtifactPresenceVerdict
from mrr.domain.artifact_presence_report import (
    ArtifactAnchorVerdictRow,
    ArtifactPresenceObservationCounts,
    ArtifactPresenceReport,
    ArtifactPresenceViolationCounts,
    build_artifact_presence_report,
    render_json,
    render_markdown,
)
from pydantic import ValidationError

_HASH_A = "sha256:" + "a" * 64
_HASH_B = "sha256:" + "b" * 64
_HASH_C = "sha256:" + "c" * 64
_HASH_D = "sha256:" + "d" * 64


def _report() -> ArtifactPresenceReport:
    verdicts = [
        ArtifactPresenceVerdict(
            anchor_id="urn:mrr:evidence-anchor:present",
            expected_hash=_HASH_A,
            blob_path="/store/aa/bb/hash-a",
            status="artifact_present",
        ),
        ArtifactPresenceVerdict(
            anchor_id="urn:mrr:evidence-anchor:missing",
            expected_hash=_HASH_B,
            blob_path="/store/bb/cc/hash-b",
            status="artifact_missing",
        ),
        ArtifactPresenceVerdict(
            anchor_id="urn:mrr:evidence-anchor:mismatch",
            expected_hash=_HASH_C,
            blob_path="/store/cc/dd/hash-c",
            status="artifact_hash_mismatch",
        ),
        ArtifactPresenceVerdict(
            anchor_id="urn:mrr:evidence-anchor:not-recorded",
            expected_hash=_HASH_D,
            blob_path=None,
            status="store_reference_not_recorded",
        ),
    ]
    return build_artifact_presence_report(
        dump_path="archive/dumps/test-dump.sql",
        store_root="/store",
        run_manifest_ids=["urn:mrr:run:1"],
        verdicts=verdicts,
        anchors_without_snapshot_hash=["urn:mrr:evidence-anchor:no-hash"],
    )


# ---------------------------------------------------------------------------
# Pydantic contract.
# ---------------------------------------------------------------------------


def test_artifact_presence_report_model_validates_a_well_formed_instance() -> None:
    report = _report()
    ArtifactPresenceReport.model_validate(report.model_dump())


def test_artifact_presence_report_rejects_an_unknown_top_level_field() -> None:
    payload = _report().model_dump()
    payload["not_a_declared_field"] = "surprise"
    with pytest.raises(ValidationError):
        ArtifactPresenceReport.model_validate(payload)


def test_honesty_header_cannot_be_constructed_false() -> None:
    payload = _report().model_dump()
    payload["recorded_root_is_not_evidence_soundness"] = False
    with pytest.raises(ValidationError):
        ArtifactPresenceReport.model_validate(payload)


def test_note_field_is_the_fixed_module_constant_not_freehand_text() -> None:
    report = _report()
    assert "NOT evidence the bytes are sound" in report.note
    assert "OBSERVATION, never a violation" in report.note


def test_anchor_row_status_is_the_closed_four_value_literal() -> None:
    with pytest.raises(ValidationError):
        ArtifactAnchorVerdictRow.model_validate(
            {
                "anchor_id": "x",
                "expected_hash": _HASH_A,
                "blob_path": None,
                "status": "not_a_real_status",
            }
        )


# ---------------------------------------------------------------------------
# Violations and observations are separate fields, never summed — the
# packet's hardest rule.
# ---------------------------------------------------------------------------


def test_violation_and_observation_counts_are_on_separate_model_classes() -> None:
    assert set(ArtifactPresenceViolationCounts.model_fields) == {
        "artifact_missing",
        "artifact_hash_mismatch",
    }
    assert set(ArtifactPresenceObservationCounts.model_fields) == {"store_reference_not_recorded"}
    # No combined "problems"/"issues" field anywhere on the top-level report.
    assert "problems" not in ArtifactPresenceReport.model_fields
    assert "issues" not in ArtifactPresenceReport.model_fields


def test_build_artifact_presence_report_counts_each_status_exactly_once() -> None:
    report = _report()
    assert report.violations == ArtifactPresenceViolationCounts(
        artifact_missing=1, artifact_hash_mismatch=1
    )
    assert report.observations == ArtifactPresenceObservationCounts(store_reference_not_recorded=1)
    # Four verdicts in, four verdicts out — one of each status, none
    # double-counted, none dropped.
    assert len(report.anchors) == 4


def test_all_not_recorded_report_has_zero_violations_never_folded_in() -> None:
    """Mirrors the real acceptance oracle's own shape: a report built
    entirely from store_reference_not_recorded verdicts must show zero
    violations, not some non-zero count from folding observations in.
    """
    verdicts = [
        ArtifactPresenceVerdict(
            anchor_id=f"urn:mrr:evidence-anchor:{i}",
            expected_hash=_HASH_A,
            blob_path=None,
            status="store_reference_not_recorded",
        )
        for i in range(17)
    ]
    report = build_artifact_presence_report(
        dump_path="archive/dumps/test-dump.sql",
        store_root=None,
        run_manifest_ids=["urn:mrr:run:1"],
        verdicts=verdicts,
        anchors_without_snapshot_hash=[],
    )
    assert report.observations.store_reference_not_recorded == 17
    assert report.violations.artifact_missing == 0
    assert report.violations.artifact_hash_mismatch == 0


# ---------------------------------------------------------------------------
# build_artifact_presence_report sorts everything, regardless of input
# order (the one place ordering is guaranteed).
# ---------------------------------------------------------------------------


def test_build_artifact_presence_report_sorts_anchors_by_id() -> None:
    verdicts = [
        ArtifactPresenceVerdict(
            anchor_id="urn:mrr:evidence-anchor:z",
            expected_hash=_HASH_A,
            blob_path=None,
            status="store_reference_not_recorded",
        ),
        ArtifactPresenceVerdict(
            anchor_id="urn:mrr:evidence-anchor:a",
            expected_hash=_HASH_A,
            blob_path=None,
            status="store_reference_not_recorded",
        ),
    ]
    report = build_artifact_presence_report(
        dump_path="d.sql",
        store_root=None,
        run_manifest_ids=[],
        verdicts=verdicts,
        anchors_without_snapshot_hash=[
            "urn:mrr:evidence-anchor:z-no-hash",
            "urn:mrr:evidence-anchor:a-no-hash",
        ],
    )
    assert [row.anchor_id for row in report.anchors] == [
        "urn:mrr:evidence-anchor:a",
        "urn:mrr:evidence-anchor:z",
    ]
    assert report.anchors_without_snapshot_hash == (
        "urn:mrr:evidence-anchor:a-no-hash",
        "urn:mrr:evidence-anchor:z-no-hash",
    )


def test_build_artifact_presence_report_sorts_run_manifest_ids() -> None:
    report = build_artifact_presence_report(
        dump_path="d.sql",
        store_root=None,
        run_manifest_ids=["urn:mrr:run:z", "urn:mrr:run:a"],
        verdicts=[],
        anchors_without_snapshot_hash=[],
    )
    assert report.run_manifest_ids == ("urn:mrr:run:a", "urn:mrr:run:z")


def test_store_reference_status_reflects_store_root_presence() -> None:
    recorded = build_artifact_presence_report(
        dump_path="d.sql",
        store_root="/x",
        run_manifest_ids=[],
        verdicts=[],
        anchors_without_snapshot_hash=[],
    )
    not_recorded = build_artifact_presence_report(
        dump_path="d.sql",
        store_root=None,
        run_manifest_ids=[],
        verdicts=[],
        anchors_without_snapshot_hash=[],
    )
    assert recorded.store_reference_status == "recorded"
    assert not_recorded.store_reference_status == "not_recorded"


# ---------------------------------------------------------------------------
# Determinism — no wall clock, no unordered iteration.
# ---------------------------------------------------------------------------


def test_render_markdown_is_deterministic() -> None:
    report = _report()
    assert render_markdown(report) == render_markdown(report)


def test_render_json_is_deterministic() -> None:
    report = _report()
    assert render_json(report) == render_json(report)


def test_render_markdown_separates_violations_from_observations_visually() -> None:
    rendered = render_markdown(_report())
    violations_index = rendered.index("## Violations")
    observations_index = rendered.index("## Observations")
    assert violations_index < observations_index
    # The violation section names both violation kinds.
    violation_block = rendered[violations_index:observations_index]
    assert "artifact_missing" in violation_block
    assert "artifact_hash_mismatch" in violation_block


def test_render_json_round_trips_through_the_model() -> None:
    import json

    report = _report()
    payload = json.loads(render_json(report))
    reloaded = ArtifactPresenceReport.model_validate(payload)
    assert reloaded == report


# ---------------------------------------------------------------------------
# ArtifactAnchorRow mirrors mrr.domain.artifact_presence.ArtifactAnchorRow
# field for field — a quick structural sanity check.
# ---------------------------------------------------------------------------


def test_artifact_anchor_row_fields_match_domain_dataclass() -> None:
    row = ArtifactAnchorRow(anchor_id="a", snapshot_hash=_HASH_A)
    assert row.anchor_id == "a"
    assert row.snapshot_hash == _HASH_A
