"""Unit tests for ``mrr.services.artifact_presence.service
.ArtifactPresenceService`` (task-packets/A2-T01.yaml, "Teil 2 — Nachsehen").

Two kinds of fixture:

- The two REAL committed archive dumps under ``archive/dumps/`` — this is
  where the packet's own fixed, pre-computed acceptance oracle
  (docs/design/2026-07-26-a2-derivation-artifact-store-reference.md) is
  reproduced mechanically: 17/34 EvidenceAnchors, ALL
  ``store_reference_not_recorded``, ZERO violations, for
  ``mrr_k1t04_real_run_v2``/``mrr_run2_corroboration_floor_v1`` respectively.
  Neither dump has ever had a recorded root (this packet did not exist when
  they were committed), so this also exercises the "not_recorded" path
  against real data, not only synthetic fixtures.
- Small, synthetic dumps written under ``tmp_path`` with a SYNTHETIC
  recorded root — the only way to exercise ``artifact_present``/
  ``artifact_missing``/``artifact_hash_mismatch`` at all, since none of
  those three statuses occurs anywhere in the two real dumps (task-packets/
  A2-T01.yaml acceptance criteria: "a test with a synthetic recorded root
  proves all three other statuses really occur").
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from mrr.domain.archive_dump import ArchiveDumpParseError
from mrr.domain.artifact_presence import AmbiguousArtifactStoreRootError
from mrr.services.artifact_presence.service import (
    ArtifactPresenceInputError,
    ArtifactPresenceService,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DUMPS_DIR = REPO_ROOT / "archive" / "dumps"

_COPY_COLUMNS = (
    "id",
    "revision",
    "api_version",
    "kind",
    "practice_id",
    "created_at",
    "created_by",
    "content_hash",
    "supersedes",
    "labels",
    "body",
)


def _content_hash(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _copy_row(object_id: str, kind: str, body: dict[str, object]) -> str:
    """One synthetic ``objects`` COPY data row — mirrors the real dumps'
    own column layout exactly (task-packets/A2-T01.yaml's own extraction
    code only ever reads ``id``/``kind``/``body``, but the header's declared
    columns must still line up field-for-field with every row).
    """
    fields = {
        "id": object_id,
        "revision": "1",
        "api_version": "mrr/v1alpha1",
        "kind": kind,
        "practice_id": "urn:mrr:practice:01KY1SNY86X0GDE2N9TVZKT4YF",
        "created_at": "2026-01-01 00:00:00+00",
        "created_by": "urn:mrr:agent-role:01KY1SNY86X0GDE2N9TVZKT4YD",
        "content_hash": "sha256:" + "0" * 64,
        "supersedes": "\\N",
        "labels": "\\N",
        "body": json.dumps(body, separators=(",", ":")),
    }
    return "\t".join(fields[column] for column in _COPY_COLUMNS)


def _build_dump_text(rows: list[str]) -> str:
    header = f"COPY test_schema.objects ({', '.join(_COPY_COLUMNS)}) FROM stdin;\n"
    return header + "\n".join(rows) + "\n\\.\n"


def _write_blob(root: Path, content_hash: str, data: bytes) -> None:
    hex_digest = content_hash.removeprefix("sha256:")
    blob_path = root / hex_digest[:2] / hex_digest[2:4] / hex_digest
    blob_path.parent.mkdir(parents=True, exist_ok=True)
    blob_path.write_bytes(data)


# ---------------------------------------------------------------------------
# The fixed, pre-computed acceptance oracle over the two REAL committed
# dumps (docs/design/2026-07-26-a2-derivation-artifact-store-reference.md).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dump_name", "expected_anchor_count"),
    [
        ("mrr_k1t04_real_run_v2.sql", 17),
        ("mrr_run2_corroboration_floor_v1.sql", 34),
    ],
)
def test_acceptance_oracle_real_dumps_are_exclusively_not_recorded_zero_violations(
    dump_name: str, expected_anchor_count: int
) -> None:
    report = ArtifactPresenceService().build_report(DUMPS_DIR / dump_name)

    assert len(report.anchors) == expected_anchor_count
    assert report.store_reference_status == "not_recorded"
    assert report.store_root is None
    assert report.anchors_without_snapshot_hash == ()

    assert report.observations.store_reference_not_recorded == expected_anchor_count
    assert all(anchor.status == "store_reference_not_recorded" for anchor in report.anchors)

    # The sharpest cross-check (task-packets/A2-T01.yaml "sharp_case"): zero
    # violations, both fields, always.
    assert report.violations.artifact_missing == 0
    assert report.violations.artifact_hash_mismatch == 0


def test_acceptance_oracle_reports_the_real_run_manifest_ids() -> None:
    report = ArtifactPresenceService().build_report(DUMPS_DIR / "mrr_k1t04_real_run_v2.sql")
    assert report.run_manifest_ids == ("urn:mrr:run:01KY1SNYPJKX5XYZGG9G3EFBWV",)


def test_acceptance_oracle_corroboration_dump_has_two_run_manifests() -> None:
    report = ArtifactPresenceService().build_report(
        DUMPS_DIR / "mrr_run2_corroboration_floor_v1.sql"
    )
    assert len(report.run_manifest_ids) == 2


# ---------------------------------------------------------------------------
# Synthetic recorded root — proves the other three statuses really occur
# (task-packets/A2-T01.yaml acceptance criteria; none of the three occurs
# anywhere in the two real dumps).
# ---------------------------------------------------------------------------


def test_synthetic_recorded_root_produces_present_missing_and_hash_mismatch(
    tmp_path: Path,
) -> None:
    store_root = tmp_path / "store"
    store_root.mkdir()

    present_bytes = b"present blob content"
    present_hash = _content_hash(present_bytes)
    _write_blob(store_root, present_hash, present_bytes)

    missing_hash = _content_hash(b"never written to this store")

    corrupted_original_bytes = b"the original, honest content"
    corrupted_hash = _content_hash(corrupted_original_bytes)
    _write_blob(store_root, corrupted_hash, b"a tampered, different byte string")

    rows = [
        _copy_row(
            "urn:mrr:run:01SYNTH0000000000000000001",
            "RunManifest",
            {"artifact_store_reference": {"status": "recorded", "root": str(store_root)}},
        ),
        _copy_row(
            "urn:mrr:evidence-anchor:present",
            "EvidenceAnchor",
            {"snapshot_hash": present_hash},
        ),
        _copy_row(
            "urn:mrr:evidence-anchor:missing",
            "EvidenceAnchor",
            {"snapshot_hash": missing_hash},
        ),
        _copy_row(
            "urn:mrr:evidence-anchor:mismatch",
            "EvidenceAnchor",
            {"snapshot_hash": corrupted_hash},
        ),
    ]
    dump_path = tmp_path / "synthetic-dump.sql"
    dump_path.write_text(_build_dump_text(rows), encoding="utf-8")

    report = ArtifactPresenceService().build_report(dump_path)

    statuses = {anchor.anchor_id: anchor.status for anchor in report.anchors}
    assert statuses["urn:mrr:evidence-anchor:present"] == "artifact_present"
    assert statuses["urn:mrr:evidence-anchor:missing"] == "artifact_missing"
    assert statuses["urn:mrr:evidence-anchor:mismatch"] == "artifact_hash_mismatch"

    assert report.store_reference_status == "recorded"
    assert report.store_root == str(store_root)
    assert report.violations.artifact_missing == 1
    assert report.violations.artifact_hash_mismatch == 1
    assert report.observations.store_reference_not_recorded == 0


def test_synthetic_dump_with_no_recorded_root_is_not_recorded_for_every_anchor(
    tmp_path: Path,
) -> None:
    rows = [
        _copy_row("urn:mrr:run:01SYNTH0000000000000000002", "RunManifest", {}),
        _copy_row(
            "urn:mrr:evidence-anchor:a",
            "EvidenceAnchor",
            {"snapshot_hash": _content_hash(b"whatever")},
        ),
    ]
    dump_path = tmp_path / "synthetic-not-recorded.sql"
    dump_path.write_text(_build_dump_text(rows), encoding="utf-8")

    report = ArtifactPresenceService().build_report(dump_path)

    assert report.store_reference_status == "not_recorded"
    assert report.anchors[0].status == "store_reference_not_recorded"
    assert report.violations.artifact_missing == 0
    assert report.violations.artifact_hash_mismatch == 0


def test_synthetic_dump_reports_anchors_without_a_snapshot_hash_separately(
    tmp_path: Path,
) -> None:
    rows = [
        _copy_row("urn:mrr:run:01SYNTH0000000000000000003", "RunManifest", {}),
        _copy_row("urn:mrr:evidence-anchor:no-hash", "EvidenceAnchor", {}),
    ]
    dump_path = tmp_path / "synthetic-no-hash.sql"
    dump_path.write_text(_build_dump_text(rows), encoding="utf-8")

    report = ArtifactPresenceService().build_report(dump_path)

    assert report.anchors == ()
    assert report.anchors_without_snapshot_hash == ("urn:mrr:evidence-anchor:no-hash",)


# ---------------------------------------------------------------------------
# Typed refusals — mirrors CitationAuditService/AnchoringIntegrityService's
# own two-kind split (exit 2 vs exit 3 at the CLI).
# ---------------------------------------------------------------------------


def test_missing_dump_file_raises_input_error(tmp_path: Path) -> None:
    with pytest.raises(ArtifactPresenceInputError):
        ArtifactPresenceService().build_report(tmp_path / "does-not-exist.sql")


def test_dump_that_is_not_valid_utf8_raises_input_error(tmp_path: Path) -> None:
    dump_path = tmp_path / "bad-encoding.sql"
    dump_path.write_bytes(b"\xff\xfe not valid utf-8")
    with pytest.raises(ArtifactPresenceInputError):
        ArtifactPresenceService().build_report(dump_path)


def test_dump_with_no_objects_copy_block_raises_archive_dump_parse_error(tmp_path: Path) -> None:
    dump_path = tmp_path / "no-copy-block.sql"
    dump_path.write_text("-- just a comment, no COPY block at all\n", encoding="utf-8")
    with pytest.raises(ArchiveDumpParseError):
        ArtifactPresenceService().build_report(dump_path)


def test_dump_with_malformed_artifact_store_reference_raises_archive_dump_parse_error(
    tmp_path: Path,
) -> None:
    rows = [
        _copy_row(
            "urn:mrr:run:01SYNTH0000000000000000004",
            "RunManifest",
            {
                "artifact_store_reference": {"status": "recorded"}
            },  # missing root: biconditional violated
        ),
    ]
    dump_path = tmp_path / "malformed-reference.sql"
    dump_path.write_text(_build_dump_text(rows), encoding="utf-8")
    with pytest.raises(ArchiveDumpParseError):
        ArtifactPresenceService().build_report(dump_path)


def test_dump_with_two_distinct_recorded_roots_raises_ambiguous_error(tmp_path: Path) -> None:
    rows = [
        _copy_row(
            "urn:mrr:run:01SYNTH0000000000000000005",
            "RunManifest",
            {"artifact_store_reference": {"status": "recorded", "root": "/store/a"}},
        ),
        _copy_row(
            "urn:mrr:run:01SYNTH0000000000000000006",
            "RunManifest",
            {"artifact_store_reference": {"status": "recorded", "root": "/store/b"}},
        ),
        _copy_row(
            "urn:mrr:evidence-anchor:a",
            "EvidenceAnchor",
            {"snapshot_hash": _content_hash(b"whatever")},
        ),
    ]
    dump_path = tmp_path / "ambiguous-root.sql"
    dump_path.write_text(_build_dump_text(rows), encoding="utf-8")
    with pytest.raises(AmbiguousArtifactStoreRootError):
        ArtifactPresenceService().build_report(dump_path)


# ---------------------------------------------------------------------------
# Determinism — two runs over the same dump render identically.
# ---------------------------------------------------------------------------


def test_two_builds_over_the_same_real_dump_produce_an_equal_report() -> None:
    service = ArtifactPresenceService()
    first = service.build_report(DUMPS_DIR / "mrr_k1t04_real_run_v2.sql")
    second = service.build_report(DUMPS_DIR / "mrr_k1t04_real_run_v2.sql")
    assert first == second
