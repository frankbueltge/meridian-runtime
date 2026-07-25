"""Unit tests for ``mrr.domain.artifact_presence`` (task-packets/A2-T01.yaml,
"Teil 2 — Nachsehen", unit tier). DB-free, no-network, no-filesystem —
every input here is a small, hand-built ``mrr.domain.archive_dump.
ArchivedObject``, never a fixture read from disk (the REAL committed
archive dumps, and every filesystem-touching check, are exercised
separately at the service tier in
tests/unit/services/test_artifact_presence_service.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mrr.domain.archive_dump import ArchivedObject, ArchiveDumpParseError
from mrr.domain.artifact_presence import (
    AmbiguousArtifactStoreReferenceError,
    ArtifactAnchorRow,
    RunManifestStoreReferenceRow,
    check_artifact_presence,
    derive_blob_path,
    extract_artifact_anchors,
    extract_run_manifest_store_references,
    resolve_dump_store_root,
)

_HASH_A = "sha256:" + "a" * 64
_HASH_B = "sha256:" + "b" * 64


def _obj(object_id: str, kind: str, body: dict[str, object]) -> ArchivedObject:
    return ArchivedObject(object_id=object_id, kind=kind, body=body)


# ---------------------------------------------------------------------------
# extract_run_manifest_store_references
# ---------------------------------------------------------------------------


def test_run_manifest_with_no_artifact_store_reference_key_is_not_recorded() -> None:
    objects = [_obj("urn:mrr:run:1", "RunManifest", {})]
    rows = extract_run_manifest_store_references(objects)
    assert rows == (
        RunManifestStoreReferenceRow(run_id="urn:mrr:run:1", status="not_recorded", root=None),
    )


def test_run_manifest_with_recorded_reference_is_extracted() -> None:
    objects = [
        _obj(
            "urn:mrr:run:1",
            "RunManifest",
            {"artifact_store_reference": {"status": "recorded", "root": "/data/artifacts"}},
        )
    ]
    rows = extract_run_manifest_store_references(objects)
    assert rows == (
        RunManifestStoreReferenceRow(
            run_id="urn:mrr:run:1", status="recorded", root="/data/artifacts"
        ),
    )


def test_run_manifest_with_explicit_not_recorded_and_null_root_is_extracted() -> None:
    objects = [
        _obj(
            "urn:mrr:run:1",
            "RunManifest",
            {"artifact_store_reference": {"status": "not_recorded", "root": None}},
        )
    ]
    rows = extract_run_manifest_store_references(objects)
    assert rows == (
        RunManifestStoreReferenceRow(run_id="urn:mrr:run:1", status="not_recorded", root=None),
    )


def test_non_run_manifest_objects_are_skipped() -> None:
    objects = [
        _obj("urn:mrr:evidence-anchor:1", "EvidenceAnchor", {"snapshot_hash": _HASH_A}),
        _obj("urn:mrr:source-record:1", "SourceRecord", {"title": "x"}),
    ]
    assert extract_run_manifest_store_references(objects) == ()


def test_extraction_preserves_objects_order_not_sorted() -> None:
    objects = [
        _obj("urn:mrr:run:z", "RunManifest", {}),
        _obj("urn:mrr:run:a", "RunManifest", {}),
    ]
    rows = extract_run_manifest_store_references(objects)
    assert [row.run_id for row in rows] == ["urn:mrr:run:z", "urn:mrr:run:a"]


@pytest.mark.parametrize(
    "body",
    [
        {"artifact_store_reference": "not-a-dict"},
        {"artifact_store_reference": {"status": "bogus"}},
        {"artifact_store_reference": {"status": "recorded", "root": 42}},
        {"artifact_store_reference": {"status": "recorded"}},
        {"artifact_store_reference": {"status": "recorded", "root": None}},
        {"artifact_store_reference": {"status": "not_recorded", "root": "/data"}},
    ],
)
def test_malformed_artifact_store_reference_raises_archive_dump_parse_error(
    body: dict[str, object],
) -> None:
    objects = [_obj("urn:mrr:run:1", "RunManifest", body)]
    with pytest.raises(ArchiveDumpParseError):
        extract_run_manifest_store_references(objects)


# ---------------------------------------------------------------------------
# extract_artifact_anchors
# ---------------------------------------------------------------------------


def test_anchor_with_snapshot_hash_is_extracted_with_hash() -> None:
    objects = [_obj("urn:mrr:evidence-anchor:1", "EvidenceAnchor", {"snapshot_hash": _HASH_A})]
    with_hash, without_hash = extract_artifact_anchors(objects)
    assert with_hash == (
        ArtifactAnchorRow(anchor_id="urn:mrr:evidence-anchor:1", snapshot_hash=_HASH_A),
    )
    assert without_hash == ()


def test_anchor_with_no_snapshot_hash_key_is_reported_separately() -> None:
    objects = [_obj("urn:mrr:evidence-anchor:1", "EvidenceAnchor", {})]
    with_hash, without_hash = extract_artifact_anchors(objects)
    assert with_hash == ()
    assert without_hash == ("urn:mrr:evidence-anchor:1",)


def test_anchor_with_explicit_null_snapshot_hash_is_reported_separately() -> None:
    objects = [_obj("urn:mrr:evidence-anchor:1", "EvidenceAnchor", {"snapshot_hash": None})]
    with_hash, without_hash = extract_artifact_anchors(objects)
    assert with_hash == ()
    assert without_hash == ("urn:mrr:evidence-anchor:1",)


def test_anchor_with_non_string_snapshot_hash_raises() -> None:
    objects = [_obj("urn:mrr:evidence-anchor:1", "EvidenceAnchor", {"snapshot_hash": 12345})]
    with pytest.raises(ArchiveDumpParseError):
        extract_artifact_anchors(objects)


def test_non_evidence_anchor_objects_are_skipped_by_extract_artifact_anchors() -> None:
    objects = [_obj("urn:mrr:run:1", "RunManifest", {})]
    with_hash, without_hash = extract_artifact_anchors(objects)
    assert with_hash == ()
    assert without_hash == ()


# ---------------------------------------------------------------------------
# resolve_dump_store_root — the "one dump, possibly several runs" logic.
# ---------------------------------------------------------------------------


def test_resolve_dump_store_root_is_none_for_an_empty_manifest_list() -> None:
    assert resolve_dump_store_root(()) is None


def test_resolve_dump_store_root_is_none_when_every_manifest_is_not_recorded() -> None:
    manifests = (
        RunManifestStoreReferenceRow(run_id="r1", status="not_recorded", root=None),
        RunManifestStoreReferenceRow(run_id="r2", status="not_recorded", root=None),
    )
    assert resolve_dump_store_root(manifests) is None


def test_resolve_dump_store_root_returns_the_single_recorded_root() -> None:
    manifests = (RunManifestStoreReferenceRow(run_id="r1", status="recorded", root="/data/a"),)
    assert resolve_dump_store_root(manifests) == "/data/a"


def test_resolve_dump_store_root_dedupes_the_same_root_across_two_runs() -> None:
    manifests = (
        RunManifestStoreReferenceRow(run_id="r1", status="recorded", root="/data/a"),
        RunManifestStoreReferenceRow(run_id="r2", status="recorded", root="/data/a"),
    )
    assert resolve_dump_store_root(manifests) == "/data/a"


def test_resolve_dump_store_root_raises_on_a_mixed_recorded_and_not_recorded_dump() -> None:
    """The review's own provoked counter-example (docs/design/2026-07-26-
    a2-t01-review.md, finding 1): a dump mixing one recorded and one
    not-recorded run used to silently resolve to the recorded run's root,
    which would then falsely report the not-recorded run's own anchors
    ``artifact_missing`` — a VIOLATION where an OBSERVATION belongs, because
    EvidenceAnchor carries no run reference to tell the two runs' anchors
    apart. This is the expected NEXT state, not a corner case: it occurs the
    moment any run recorded under this packet sits in a dump beside an
    older, pre-packet run.
    """
    manifests = (
        RunManifestStoreReferenceRow(run_id="r-old", status="not_recorded", root=None),
        RunManifestStoreReferenceRow(run_id="r-new", status="recorded", root="/tmp/neuer-store"),
    )
    with pytest.raises(AmbiguousArtifactStoreReferenceError) as excinfo:
        resolve_dump_store_root(manifests)
    assert excinfo.value.statuses == ("not_recorded", "recorded")
    assert excinfo.value.roots == ()


def test_resolve_dump_store_root_raises_on_a_mixed_dump_regardless_of_row_order() -> None:
    manifests = (
        RunManifestStoreReferenceRow(run_id="r-new", status="recorded", root="/tmp/neuer-store"),
        RunManifestStoreReferenceRow(run_id="r-old", status="not_recorded", root=None),
    )
    with pytest.raises(AmbiguousArtifactStoreReferenceError) as excinfo:
        resolve_dump_store_root(manifests)
    assert excinfo.value.statuses == ("not_recorded", "recorded")


def test_resolve_dump_store_root_status_disagreement_is_checked_before_root_disagreement() -> None:
    """A mixed-status dump raises naming the STATUS disagreement even when
    the recorded manifests among them would ALSO have disagreed on root —
    status agreement is the first gate, root comparison never runs at all
    once it fails.
    """
    manifests = (
        RunManifestStoreReferenceRow(run_id="r1", status="not_recorded", root=None),
        RunManifestStoreReferenceRow(run_id="r2", status="recorded", root="/data/a"),
        RunManifestStoreReferenceRow(run_id="r3", status="recorded", root="/data/b"),
    )
    with pytest.raises(AmbiguousArtifactStoreReferenceError) as excinfo:
        resolve_dump_store_root(manifests)
    assert excinfo.value.statuses == ("not_recorded", "recorded")
    assert excinfo.value.roots == ()


def test_resolve_dump_store_root_raises_on_two_distinct_recorded_roots() -> None:
    manifests = (
        RunManifestStoreReferenceRow(run_id="r1", status="recorded", root="/data/a"),
        RunManifestStoreReferenceRow(run_id="r2", status="recorded", root="/data/b"),
    )
    with pytest.raises(AmbiguousArtifactStoreReferenceError) as excinfo:
        resolve_dump_store_root(manifests)
    assert excinfo.value.roots == ("/data/a", "/data/b")
    assert excinfo.value.statuses == ()


# ---------------------------------------------------------------------------
# derive_blob_path — the content-addressed layout formula, reimplemented.
# ---------------------------------------------------------------------------


def test_derive_blob_path_matches_the_two_level_shard_layout() -> None:
    content_hash = "sha256:" + "c1" + "d2" + "e" * 60
    path = derive_blob_path("/artifacts", content_hash)
    assert path == Path("/artifacts") / "c1" / "d2" / ("c1d2" + "e" * 60)


def test_derive_blob_path_strips_only_the_sha256_prefix() -> None:
    content_hash = "sha256:" + "f" * 64
    path = derive_blob_path("root", content_hash)
    assert path.name == "f" * 64
    assert "sha256" not in str(path)


# ---------------------------------------------------------------------------
# check_artifact_presence — the closed four-value status, pure comparison.
# ---------------------------------------------------------------------------

_ANCHOR = ArtifactAnchorRow(anchor_id="urn:mrr:evidence-anchor:1", snapshot_hash=_HASH_A)


def test_check_artifact_presence_is_not_recorded_when_store_root_is_none() -> None:
    verdict = check_artifact_presence(
        _ANCHOR, store_root=None, blob_path=None, blob_exists=False, actual_hash=None
    )
    assert verdict.status == "store_reference_not_recorded"
    assert verdict.blob_path is None
    assert verdict.expected_hash == _HASH_A


def test_check_artifact_presence_is_not_recorded_even_if_a_caller_wrongly_passed_a_path() -> None:
    """A defensive case: even if a misbehaving caller computed a blob_path
    despite store_root being None, the verdict is still
    store_reference_not_recorded — see the domain module's own docstring.
    """
    verdict = check_artifact_presence(
        _ANCHOR, store_root=None, blob_path="/some/path", blob_exists=True, actual_hash=_HASH_A
    )
    assert verdict.status == "store_reference_not_recorded"


def test_check_artifact_presence_is_missing_when_blob_does_not_exist() -> None:
    verdict = check_artifact_presence(
        _ANCHOR,
        store_root="/data",
        blob_path="/data/aa/bb/hash",
        blob_exists=False,
        actual_hash=None,
    )
    assert verdict.status == "artifact_missing"
    assert verdict.blob_path == "/data/aa/bb/hash"


def test_check_artifact_presence_is_hash_mismatch_when_hashes_differ() -> None:
    verdict = check_artifact_presence(
        _ANCHOR,
        store_root="/data",
        blob_path="/data/aa/bb/hash",
        blob_exists=True,
        actual_hash=_HASH_B,
    )
    assert verdict.status == "artifact_hash_mismatch"


def test_check_artifact_presence_is_present_when_hashes_match() -> None:
    verdict = check_artifact_presence(
        _ANCHOR,
        store_root="/data",
        blob_path="/data/aa/bb/hash",
        blob_exists=True,
        actual_hash=_HASH_A,
    )
    assert verdict.status == "artifact_present"


def test_violations_and_observations_are_the_expected_disjoint_subsets() -> None:
    """The closed four-value set splits exactly into two VIOLATIONS
    (artifact_missing, artifact_hash_mismatch), one OBSERVATION
    (store_reference_not_recorded), and one hit (artifact_present) — never
    collapsed (task-packets/A2-T01.yaml's hardest rule).
    """
    from typing import get_args

    from mrr.domain.artifact_presence import ArtifactPresenceStatus

    violations = {"artifact_missing", "artifact_hash_mismatch"}
    observations = {"store_reference_not_recorded"}
    assert violations.isdisjoint(observations)
    assert violations | observations | {"artifact_present"} == set(get_args(ArtifactPresenceStatus))
