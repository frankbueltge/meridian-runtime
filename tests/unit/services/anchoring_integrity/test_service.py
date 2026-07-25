"""Unit tests for ``mrr.services.anchoring_integrity.service
.AnchoringIntegrityService`` (task-packets/N2-T02b.yaml R5/R7, unit tier).
DB-free, no-network: every fixture is a small, synthetic archive dump +
anchoring-batch descriptor written under ``tmp_path`` — the REAL committed
``corpora/archive-integrity`` batch is exercised separately by the
acceptance tests in tests/contract/test_anchoring_integrity_acceptance.py.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from mrr.domain.anchoring_integrity import IntegrityGateError
from mrr.services.anchoring_integrity import service as anchoring_integrity_service_module
from mrr.services.anchoring_integrity.service import (
    AnchoringIntegrityInputError,
    AnchoringIntegrityService,
)


def _sha256_of(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _dump_text(rows: list[tuple[str, str, str]]) -> str:
    """A minimal, valid ``objects`` COPY block: just the three columns this
    packet's parser requires (``id, kind, body``) — a real dump also
    carries ``revision``/``api_version``/etc., but those are irrelevant to
    every check this service performs."""
    lines = ["COPY mrr_test.objects (id, kind, body) FROM stdin;"]
    for object_id, kind, body in rows:
        lines.append(f"{object_id}\t{kind}\t{body}")
    lines.append(r"\.")
    return "\n".join(lines) + "\n"


def _write_dump(path: Path, rows: list[tuple[str, str, str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump_text(rows), encoding="utf-8")
    return path


_CLEAN_ROWS = [
    ("urn:mrr:source-record:1", "SourceRecord", '{"title": "Work One"}'),
    (
        "urn:mrr:evidence-anchor:1",
        "EvidenceAnchor",
        '{"source_record_id": "urn:mrr:source-record:1"}',
    ),
    (
        "urn:mrr:claim:1",
        "Claim",
        '{"evidence_relations": ["urn:mrr:evidence-anchor:1"], "counterevidence_relations": []}',
    ),
]


def _minimal_batch(tmp_path: Path, *, rows: list[tuple[str, str, str]] | None = None) -> Path:
    """Write a minimal, valid anchoring-batch descriptor + single archive
    dump under ``tmp_path`` and return the descriptor's own path."""
    dump_path = tmp_path / "dumps" / "mrr_test.sql"
    _write_dump(dump_path, rows if rows is not None else _CLEAN_ROWS)

    batch_path = tmp_path / "anchoring-batch.v1.json"
    batch_path.write_text(
        json.dumps(
            {
                "schema_version": "archive-anchoring-batch.v1",
                "batch_id": "synthetic-batch",
                "observation_kind": "archive-anchoring-integrity",
                "audit_target": "a synthetic test target",
                "dumps": [
                    {
                        "schema_name": "mrr_test",
                        "path": "dumps/mrr_test.sql",
                        "sha256": _sha256_of(dump_path),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return batch_path


# ---------------------------------------------------------------------------
# Happy path.
# ---------------------------------------------------------------------------


def test_build_report_happy_path_reports_zero_violations(tmp_path: Path) -> None:
    batch_path = _minimal_batch(tmp_path)

    report = AnchoringIntegrityService().build_report(batch_path)

    assert report.batch_id == "synthetic-batch"
    assert len(report.dumps) == 1
    dump = report.dumps[0]
    assert dump.file_anchor.matched is True
    assert dump.object_counts.total == 3
    assert dump.object_counts.by_kind == {
        "Claim": 1,
        "EvidenceAnchor": 1,
        "SourceRecord": 1,
    }
    assert dump.violations.anchor_dangling == 0
    assert dump.violations.claim_reference_dangling == 0
    assert dump.observations.source_unanchored == 0
    assert dump.observations.anchor_unreferenced == 0


def test_batch_dump_paths_resolved_relative_to_descriptor_directory_not_cwd(
    tmp_path: Path,
) -> None:
    batch_path = _minimal_batch(tmp_path)
    report = AnchoringIntegrityService().build_report(batch_path.resolve())
    assert report.dumps[0].object_counts.total == 3


def test_dangling_anchor_and_dangling_claim_reference_are_reported_as_violations(
    tmp_path: Path,
) -> None:
    rows = [
        ("urn:mrr:sr:1", "SourceRecord", '{"title": "Work"}'),
        ("urn:mrr:ea:1", "EvidenceAnchor", '{"source_record_id": "urn:mrr:sr:does-not-exist"}'),
        (
            "urn:mrr:claim:1",
            "Claim",
            '{"evidence_relations": ["urn:mrr:ea:does-not-exist"], '
            '"counterevidence_relations": []}',
        ),
    ]
    batch_path = _minimal_batch(tmp_path, rows=rows)

    report = AnchoringIntegrityService().build_report(batch_path)

    dump = report.dumps[0]
    assert dump.violations.anchor_dangling == 1
    assert dump.violations.claim_reference_dangling == 1
    # And the source itself, having no VALID anchor pointing at it, is
    # ALSO reported as an unanchored-source observation — never collapsed
    # with the anchor_dangling violation above into one number.
    assert dump.observations.source_unanchored == 1


def test_report_honesty_header_names_n2_t03_and_observations(tmp_path: Path) -> None:
    batch_path = _minimal_batch(tmp_path)
    report = AnchoringIntegrityService().build_report(batch_path)
    assert report.anchoring_is_not_support is True
    assert "N2-T03" in report.note


# ---------------------------------------------------------------------------
# AnchoringIntegrityInputError — file-level dependency failures.
# ---------------------------------------------------------------------------


def test_missing_batch_file_raises_anchoring_integrity_input_error(tmp_path: Path) -> None:
    with pytest.raises(AnchoringIntegrityInputError):
        AnchoringIntegrityService().build_report(tmp_path / "does-not-exist.json")


def test_batch_with_invalid_json_raises_anchoring_integrity_input_error(tmp_path: Path) -> None:
    batch_path = tmp_path / "bad.json"
    batch_path.write_text("{not valid json")
    with pytest.raises(AnchoringIntegrityInputError):
        AnchoringIntegrityService().build_report(batch_path)


def test_batch_with_wrong_top_level_shape_raises_anchoring_integrity_input_error(
    tmp_path: Path,
) -> None:
    batch_path = tmp_path / "not-an-object.json"
    batch_path.write_text(json.dumps(["a", "list", "not", "an", "object"]))
    with pytest.raises(AnchoringIntegrityInputError):
        AnchoringIntegrityService().build_report(batch_path)


def test_batch_missing_required_key_raises_anchoring_integrity_input_error(tmp_path: Path) -> None:
    batch_path = _minimal_batch(tmp_path)
    document = json.loads(batch_path.read_text())
    del document["batch_id"]
    batch_path.write_text(json.dumps(document))
    with pytest.raises(AnchoringIntegrityInputError):
        AnchoringIntegrityService().build_report(batch_path)


def test_batch_with_empty_dumps_list_raises_anchoring_integrity_input_error(
    tmp_path: Path,
) -> None:
    batch_path = _minimal_batch(tmp_path)
    document = json.loads(batch_path.read_text())
    document["dumps"] = []
    batch_path.write_text(json.dumps(document))
    with pytest.raises(AnchoringIntegrityInputError, match="empty"):
        AnchoringIntegrityService().build_report(batch_path)


def test_batch_with_dumps_not_a_list_raises_anchoring_integrity_input_error(
    tmp_path: Path,
) -> None:
    batch_path = _minimal_batch(tmp_path)
    document = json.loads(batch_path.read_text())
    document["dumps"] = "not-a-list"
    batch_path.write_text(json.dumps(document))
    with pytest.raises(AnchoringIntegrityInputError):
        AnchoringIntegrityService().build_report(batch_path)


def test_missing_declared_dump_file_raises_anchoring_integrity_input_error(
    tmp_path: Path,
) -> None:
    batch_path = _minimal_batch(tmp_path)
    (tmp_path / "dumps" / "mrr_test.sql").unlink()
    with pytest.raises(AnchoringIntegrityInputError):
        AnchoringIntegrityService().build_report(batch_path)


def test_batch_supports_more_than_two_declared_dumps_the_open_set(tmp_path: Path) -> None:
    """Unlike R2-T01's fixed two-role batch, this packet's ``dumps[]`` is
    OPEN (task-packets/N2-T02b.yaml R4) — three dumps here, not two."""
    dump_paths = []
    for i in range(3):
        rows = [
            (f"urn:mrr:sr:{i}", "SourceRecord", f'{{"title": "Work {i}"}}'),
        ]
        dump_paths.append(_write_dump(tmp_path / f"dumps/mrr_{i}.sql", rows))

    batch_path = tmp_path / "anchoring-batch.v1.json"
    batch_path.write_text(
        json.dumps(
            {
                "schema_version": "archive-anchoring-batch.v1",
                "batch_id": "three-dump-batch",
                "observation_kind": "archive-anchoring-integrity",
                "audit_target": "a synthetic test target",
                "dumps": [
                    {
                        "schema_name": f"mrr_{i}",
                        "path": f"dumps/mrr_{i}.sql",
                        "sha256": _sha256_of(p),
                    }
                    for i, p in enumerate(dump_paths)
                ],
            }
        ),
        encoding="utf-8",
    )

    report = AnchoringIntegrityService().build_report(batch_path)
    assert [dump.schema_name for dump in report.dumps] == ["mrr_0", "mrr_1", "mrr_2"]


# ---------------------------------------------------------------------------
# IntegrityGateError — the fail-closed gate itself.
# ---------------------------------------------------------------------------


def test_dump_anchor_mismatch_raises_integrity_gate_error_naming_the_dump(
    tmp_path: Path,
) -> None:
    batch_path = _minimal_batch(tmp_path)
    document = json.loads(batch_path.read_text())
    document["dumps"][0]["sha256"] = "sha256:" + "0" * 64  # deliberately wrong
    batch_path.write_text(json.dumps(document))

    with pytest.raises(IntegrityGateError) as excinfo:
        AnchoringIntegrityService().build_report(batch_path)
    assert excinfo.value.schema_name == "mrr_test"


def test_at4_fail_closed_gate_runs_before_any_dump_is_ever_parsed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """task-packets/N2-T02b.yaml AT4: a descriptor with a wrong declared
    dump anchor makes the service raise ``IntegrityGateError`` and
    ``mrr.domain.archive_dump.parse_objects_copy_block`` is provably NOT
    reached — monkeypatched here to raise ``AssertionError`` if it were
    ever called, proving the gate runs strictly BEFORE any dump is parsed.
    """

    def _must_not_be_called(dump_text: str) -> None:
        raise AssertionError(
            "parse_objects_copy_block must never be reached when the integrity gate has "
            "already failed"
        )

    monkeypatch.setattr(
        anchoring_integrity_service_module, "parse_objects_copy_block", _must_not_be_called
    )

    batch_path = _minimal_batch(tmp_path)
    document = json.loads(batch_path.read_text())
    document["dumps"][0]["sha256"] = "sha256:" + "0" * 64  # deliberately wrong
    batch_path.write_text(json.dumps(document))

    with pytest.raises(IntegrityGateError) as excinfo:
        AnchoringIntegrityService().build_report(batch_path)
    assert excinfo.value.schema_name == "mrr_test"
    # An AssertionError from the monkeypatched parser would have propagated
    # as AssertionError, not IntegrityGateError, if the gate had not
    # short-circuited first — pytest.raises above already proves it did.


def test_at4_clean_gate_does_reach_the_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mirror image of the fail-closed test above: with a CLEAN gate,
    the same monkeypatched ``parse_objects_copy_block`` IS reached (and its
    ``AssertionError`` propagates unmodified) — proving the earlier test's
    ``IntegrityGateError`` really was caused by the gate short-circuiting,
    not by some other reason the parser was never called."""

    def _must_be_called(dump_text: str) -> None:
        raise AssertionError("reached, as expected, with a clean gate")

    monkeypatch.setattr(
        anchoring_integrity_service_module, "parse_objects_copy_block", _must_be_called
    )

    batch_path = _minimal_batch(tmp_path)  # anchor is clean, untouched

    with pytest.raises(AssertionError, match="reached, as expected"):
        AnchoringIntegrityService().build_report(batch_path)


# ---------------------------------------------------------------------------
# ArchiveDumpParseError — propagated unchanged, only once the gate is clean.
# ---------------------------------------------------------------------------


def test_malformed_dump_after_a_clean_gate_raises_archive_dump_parse_error(
    tmp_path: Path,
) -> None:
    from mrr.domain.archive_dump import ArchiveDumpParseError

    dump_path = tmp_path / "dumps" / "mrr_test.sql"
    dump_path.parent.mkdir(parents=True, exist_ok=True)
    dump_path.write_text("not a COPY block at all\n", encoding="utf-8")

    batch_path = tmp_path / "anchoring-batch.v1.json"
    batch_path.write_text(
        json.dumps(
            {
                "schema_version": "archive-anchoring-batch.v1",
                "batch_id": "synthetic-batch",
                "observation_kind": "archive-anchoring-integrity",
                "audit_target": "a synthetic test target",
                "dumps": [
                    {
                        "schema_name": "mrr_test",
                        "path": "dumps/mrr_test.sql",
                        "sha256": _sha256_of(dump_path),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ArchiveDumpParseError):
        AnchoringIntegrityService().build_report(batch_path)
