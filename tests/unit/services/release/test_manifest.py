"""Unit tests for ``mrr.services.release.manifest`` (task-packets/E8-T04.yaml,
ADR-0011 decision 1/3) — entirely DB-free, pure filesystem + hashing,
exercising the manifest/root-hash rule R5 names: "manifest/root-hash rule
(incl. the two excluded files), determinism".

Acceptance-test mapping:

- "root_hash covers content files only, EXCLUDING release-manifest.json and
  release-record.json" -> ``test_excluded_filenames_never_appear_in_the_manifest``.
- determinism (two computations of the same tree yield the identical
  manifest) -> ``test_two_computations_of_the_same_tree_are_identical``.
- ``compute_root_hash`` recomputes deterministically regardless of input
  order -> ``test_compute_root_hash_is_independent_of_input_order``.
- a single flipped byte changes the root_hash and the affected file's own
  hash, nothing else -> ``test_flipping_one_byte_changes_only_that_files_hash``.
"""

from __future__ import annotations

from pathlib import Path

from mrr.crypto.hashing import content_hash
from mrr.services.release.manifest import (
    EXCLUDED_FILENAMES,
    compute_bundle_manifest,
    compute_root_hash,
)


def _write_tree(root: Path) -> None:
    (root / "report.md").write_text("# report\n", encoding="utf-8")
    (root / "report.html").write_text("<html></html>", encoding="utf-8")
    (root / "ro-crate").mkdir()
    (root / "ro-crate" / "ro-crate-metadata.json").write_text("{}", encoding="utf-8")
    objects_dir = root / "ro-crate" / "objects"
    objects_dir.mkdir()
    (objects_dir / "urn_mrr_evidence-crate_x.json").write_text('{"a": 1}', encoding="utf-8")


def test_manifest_files_are_sorted_by_path(tmp_path: Path) -> None:
    _write_tree(tmp_path)

    manifest = compute_bundle_manifest(tmp_path)

    paths = [entry.path for entry in manifest.files]
    assert paths == sorted(paths)
    assert paths == [
        "report.html",
        "report.md",
        "ro-crate/objects/urn_mrr_evidence-crate_x.json",
        "ro-crate/ro-crate-metadata.json",
    ]


def test_excluded_filenames_never_appear_in_the_manifest(tmp_path: Path) -> None:
    _write_tree(tmp_path)
    (tmp_path / "release-manifest.json").write_text('{"files": []}', encoding="utf-8")
    (tmp_path / "release-record.json").write_text("{}", encoding="utf-8")

    manifest = compute_bundle_manifest(tmp_path)

    paths = {entry.path for entry in manifest.files}
    assert paths.isdisjoint(EXCLUDED_FILENAMES)
    assert "release-manifest.json" not in paths
    assert "release-record.json" not in paths
    # Confirms the exclusion actually removed files that WOULD otherwise
    # have been present, not merely that this fixture never wrote them.
    assert len(paths) == 4


def test_each_file_hash_matches_content_hash_of_its_own_bytes(tmp_path: Path) -> None:
    _write_tree(tmp_path)

    manifest = compute_bundle_manifest(tmp_path)

    for entry in manifest.files:
        raw_bytes = (tmp_path / entry.path).read_bytes()
        assert entry.sha256 == content_hash(raw_bytes)


def test_two_computations_of_the_same_tree_are_identical(tmp_path: Path) -> None:
    _write_tree(tmp_path)

    first = compute_bundle_manifest(tmp_path)
    second = compute_bundle_manifest(tmp_path)

    assert first == second
    assert first.root_hash == second.root_hash


def test_compute_root_hash_is_independent_of_input_order() -> None:
    files = [("b.json", "sha256:" + "2" * 64), ("a.json", "sha256:" + "1" * 64)]

    forward = compute_root_hash(files)
    reversed_order = compute_root_hash(list(reversed(files)))

    assert forward == reversed_order


def test_compute_root_hash_changes_when_any_hash_changes() -> None:
    files_a = [("a.json", "sha256:" + "1" * 64), ("b.json", "sha256:" + "2" * 64)]
    files_b = [("a.json", "sha256:" + "1" * 64), ("b.json", "sha256:" + "3" * 64)]

    assert compute_root_hash(files_a) != compute_root_hash(files_b)


def test_flipping_one_byte_changes_only_that_files_hash(tmp_path: Path) -> None:
    _write_tree(tmp_path)
    before = compute_bundle_manifest(tmp_path)
    before_by_path = {entry.path: entry.sha256 for entry in before.files}

    target = tmp_path / "report.md"
    original = target.read_bytes()
    flipped = bytes([original[0] ^ 0x01]) + original[1:]
    target.write_bytes(flipped)

    after = compute_bundle_manifest(tmp_path)
    after_by_path = {entry.path: entry.sha256 for entry in after.files}

    assert after.root_hash != before.root_hash
    changed_paths = {path for path in before_by_path if before_by_path[path] != after_by_path[path]}
    assert changed_paths == {"report.md"}


def test_empty_directory_produces_an_empty_manifest_with_a_stable_root_hash(
    tmp_path: Path,
) -> None:
    manifest = compute_bundle_manifest(tmp_path)

    assert manifest.files == ()
    assert manifest.root_hash == content_hash(b"")
