"""Unit tests for ``mrr.services.field_observation.service
.FieldObservationService`` (task-packets/R2-T01.yaml R4/R6, unit tier).
DB-free, no-network: every fixture is a small, synthetic descriptor +
manifest/snapshot bundle written under ``tmp_path`` — the REAL committed
``corpora/e2e-survey`` batch is exercised separately by the acceptance tests
in tests/contract/test_field_observation_acceptance.py.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from mrr.domain.citation_audit import MissingResolutionError
from mrr.domain.field_observation import IntegrityGateError
from mrr.services.citation_audit.service import CitationAuditService
from mrr.services.field_observation.service import (
    FieldObservationInputError,
    FieldObservationService,
)


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _sha256_of(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _minimal_batch(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Write a minimal, valid batch descriptor + manifest/snapshot bundle
    and return ``(batch_path, manifest_path, snapshot_path)``.
    """
    manifest_path = tmp_path / "citations.manifest.json"
    snapshot_path = tmp_path / "verification" / "resolution-snapshot.json"

    _write_json(
        manifest_path,
        {
            "audit_target": "a synthetic test target",
            "citations": [
                {
                    "citation_id": "c1",
                    "cited_as": "Some Paper",
                    "cited_url": "https://arxiv.org/abs/2511.02824",
                    "identifiers": {"arxiv": "2511.02824"},
                    "claimed_title": None,
                }
            ],
        },
    )
    _write_json(
        snapshot_path,
        {
            "resolutions": [
                {
                    "citation_id": "c1",
                    "resolved": True,
                    "resolved_title": "Some Resolved Title",
                }
            ]
        },
    )

    batch_path = tmp_path / "observation-batch.v1.json"
    _write_json(
        batch_path,
        {
            "schema_version": "observation-batch.v1",
            "batch_id": "synthetic-batch",
            "observation_kind": "citation_audit",
            "audit_target": "a synthetic test target",
            "provenance": "irrelevant prose, ignored by the parser",
            "inputs": {
                "manifest": {
                    "path": "citations.manifest.json",
                    "sha256": _sha256_of(manifest_path),
                },
                "snapshot": {
                    "path": "verification/resolution-snapshot.json",
                    "sha256": _sha256_of(snapshot_path),
                },
            },
        },
    )
    return batch_path, manifest_path, snapshot_path


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_build_report_happy_path_reports_matched_anchors_and_embeds_audit(
    tmp_path: Path,
) -> None:
    batch_path, _, _ = _minimal_batch(tmp_path)

    report = FieldObservationService().build_report(batch_path)

    assert report.batch_id == "synthetic-batch"
    assert report.observation_kind == "citation_audit"
    assert all(row.matched for row in report.anchors)
    assert report.citation_audit.summary.resolved == 1
    assert report.citation_audit.summary.total == 1


def test_batch_input_paths_resolved_relative_to_descriptor_directory_not_cwd(
    tmp_path: Path,
) -> None:
    batch_path, _, _ = _minimal_batch(tmp_path)
    # Regardless of what path form is given, resolution is relative to the
    # descriptor's own parent directory.
    report = FieldObservationService().build_report(batch_path.resolve())
    assert report.citation_audit.summary.total == 1


def test_observation_note_and_header_are_present_on_every_report(tmp_path: Path) -> None:
    batch_path, _, _ = _minimal_batch(tmp_path)
    report = FieldObservationService().build_report(batch_path)
    assert report.observation_is_not_optimization is True
    assert "R2-T02" in report.observation_note
    assert "R2-T03" in report.observation_note


# ---------------------------------------------------------------------------
# FieldObservationInputError — file-level dependency failures.
# ---------------------------------------------------------------------------


def test_missing_batch_file_raises_field_observation_input_error(tmp_path: Path) -> None:
    with pytest.raises(FieldObservationInputError):
        FieldObservationService().build_report(tmp_path / "does-not-exist.json")


def test_batch_with_invalid_json_raises_field_observation_input_error(tmp_path: Path) -> None:
    batch_path = tmp_path / "bad.json"
    batch_path.write_text("{not valid json")
    with pytest.raises(FieldObservationInputError):
        FieldObservationService().build_report(batch_path)


def test_batch_missing_required_key_raises_field_observation_input_error(tmp_path: Path) -> None:
    batch_path, _, _ = _minimal_batch(tmp_path)
    document = json.loads(batch_path.read_text())
    del document["batch_id"]
    batch_path.write_text(json.dumps(document))

    with pytest.raises(FieldObservationInputError):
        FieldObservationService().build_report(batch_path)


def test_batch_with_wrong_top_level_shape_raises_field_observation_input_error(
    tmp_path: Path,
) -> None:
    batch_path = tmp_path / "not-an-object.json"
    batch_path.write_text(json.dumps(["a", "list", "not", "an", "object"]))
    with pytest.raises(FieldObservationInputError):
        FieldObservationService().build_report(batch_path)


def test_batch_inputs_missing_manifest_key_raises_field_observation_input_error(
    tmp_path: Path,
) -> None:
    batch_path, _, _ = _minimal_batch(tmp_path)
    document = json.loads(batch_path.read_text())
    del document["inputs"]["manifest"]
    batch_path.write_text(json.dumps(document))

    with pytest.raises(FieldObservationInputError):
        FieldObservationService().build_report(batch_path)


def test_missing_declared_manifest_file_raises_field_observation_input_error(
    tmp_path: Path,
) -> None:
    batch_path, manifest_path, _ = _minimal_batch(tmp_path)
    manifest_path.unlink()

    with pytest.raises(FieldObservationInputError):
        FieldObservationService().build_report(batch_path)


def test_missing_declared_snapshot_file_raises_field_observation_input_error(
    tmp_path: Path,
) -> None:
    batch_path, _, snapshot_path = _minimal_batch(tmp_path)
    snapshot_path.unlink()

    with pytest.raises(FieldObservationInputError):
        FieldObservationService().build_report(batch_path)


# ---------------------------------------------------------------------------
# IntegrityGateError — the fail-closed gate itself.
# ---------------------------------------------------------------------------


def test_manifest_anchor_mismatch_raises_integrity_gate_error_naming_manifest(
    tmp_path: Path,
) -> None:
    batch_path, _, _ = _minimal_batch(tmp_path)
    document = json.loads(batch_path.read_text())
    document["inputs"]["manifest"]["sha256"] = "sha256:" + "0" * 64  # deliberately wrong
    batch_path.write_text(json.dumps(document))

    with pytest.raises(IntegrityGateError) as excinfo:
        FieldObservationService().build_report(batch_path)
    assert excinfo.value.role == "manifest"


def test_snapshot_anchor_mismatch_raises_integrity_gate_error_naming_snapshot(
    tmp_path: Path,
) -> None:
    batch_path, _, _ = _minimal_batch(tmp_path)
    document = json.loads(batch_path.read_text())
    document["inputs"]["snapshot"]["sha256"] = "sha256:" + "0" * 64  # deliberately wrong
    batch_path.write_text(json.dumps(document))

    with pytest.raises(IntegrityGateError) as excinfo:
        FieldObservationService().build_report(batch_path)
    assert excinfo.value.role == "snapshot"


def test_at3_fail_closed_gate_runs_before_the_evaluator_is_ever_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """task-packets/R2-T01.yaml AT3: a descriptor with a wrong declared
    manifest anchor makes the service raise ``IntegrityGateError`` and the
    reused ``CitationAuditService.build_report`` is provably NOT reached —
    monkeypatched here to raise ``AssertionError`` if it were ever called,
    proving the gate runs strictly BEFORE the evaluator (never the reverse).
    """

    def _must_not_be_called(
        self: CitationAuditService, manifest_path: Path, snapshot_path: Path
    ) -> None:
        raise AssertionError(
            "CitationAuditService.build_report must never be reached when the "
            "integrity gate has already failed"
        )

    monkeypatch.setattr(CitationAuditService, "build_report", _must_not_be_called)

    batch_path, _, _ = _minimal_batch(tmp_path)
    document = json.loads(batch_path.read_text())
    document["inputs"]["manifest"]["sha256"] = "sha256:" + "0" * 64  # deliberately wrong
    batch_path.write_text(json.dumps(document))

    with pytest.raises(IntegrityGateError) as excinfo:
        FieldObservationService().build_report(batch_path)
    assert excinfo.value.role == "manifest"
    # An AssertionError from the monkeypatched evaluator would have
    # propagated as AssertionError, not IntegrityGateError, if the gate had
    # not short-circuited first — pytest.raises above already proves it did.


def test_at3_clean_gate_does_reach_the_evaluator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mirror image of the fail-closed test above: with a CLEAN gate,
    the same monkeypatched ``CitationAuditService.build_report`` IS reached
    (and its ``AssertionError`` propagates unmodified) — proving the earlier
    test's ``IntegrityGateError`` really was caused by the gate short-
    circuiting, not by some other reason the evaluator was never called.
    """

    def _must_be_called(
        self: CitationAuditService, manifest_path: Path, snapshot_path: Path
    ) -> None:
        raise AssertionError("reached, as expected, with a clean gate")

    monkeypatch.setattr(CitationAuditService, "build_report", _must_be_called)

    batch_path, _, _ = _minimal_batch(tmp_path)  # anchors are clean, untouched

    with pytest.raises(AssertionError, match="reached, as expected"):
        FieldObservationService().build_report(batch_path)


# ---------------------------------------------------------------------------
# MissingResolutionError — propagated unchanged from the reused N2 evaluator.
# ---------------------------------------------------------------------------


def test_missing_resolution_propagates_from_the_reused_evaluator(tmp_path: Path) -> None:
    batch_path, _, snapshot_path = _minimal_batch(tmp_path)
    snapshot_document = json.loads(snapshot_path.read_text())
    snapshot_document["resolutions"] = []  # remove the only resolution -> a structural gap
    snapshot_path.write_text(json.dumps(snapshot_document))
    # Re-pin the snapshot anchor to the mutated file's own new hash so this
    # test exercises ONLY MissingResolutionError, not a coincidental anchor
    # mismatch.
    batch_document = json.loads(batch_path.read_text())
    batch_document["inputs"]["snapshot"]["sha256"] = _sha256_of(snapshot_path)
    batch_path.write_text(json.dumps(batch_document))

    with pytest.raises(MissingResolutionError) as excinfo:
        FieldObservationService().build_report(batch_path)
    assert excinfo.value.citation_id == "c1"
