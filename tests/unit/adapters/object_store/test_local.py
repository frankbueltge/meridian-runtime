"""Unit tests for mrr.adapters.object_store.local.LocalFilesystemArtifactStore
(E1-T07), against a real ``tmp_path`` filesystem — no database, no
container, fully local per task-packets/E1-T07.yaml.

Covers the packet's named acceptance tests: put-then-get round-trips exactly,
the computed hash matches mrr.crypto.hashing, put is idempotent (one blob,
equal descriptor), get on an unknown hash raises a typed not-found error,
corrupting a stored blob then reading it raises a typed integrity error, and
writes are atomic (no lingering temp files).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from mrr.adapters.object_store.local import LocalFilesystemArtifactStore
from mrr.crypto.hashing import content_hash
from mrr.domain.artifacts import ArtifactDescriptor, ArtifactStore
from mrr.domain.exceptions import (
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    InvalidContentHashError,
)

_RUN_ID = "urn:mrr:run-manifest:01ARZ3NDEKTSV4RRFFQ69G5FAV"
_OTHER_RUN_ID = "urn:mrr:run-manifest:01ARZ3NDEKTSV4RRFFQ69G5FBV"


def _store(tmp_path: Path) -> LocalFilesystemArtifactStore:
    return LocalFilesystemArtifactStore(tmp_path / "artifacts")


def _put(
    store: LocalFilesystemArtifactStore, data: bytes, **overrides: object
) -> ArtifactDescriptor:
    kwargs: dict[str, object] = {
        "media_type": "application/octet-stream",
        "producer_run_id": _RUN_ID,
        "classification": "INTERNAL",
        "created_at": datetime.now(UTC),
    }
    kwargs.update(overrides)
    return store.put(data, **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Protocol conformance.
# ---------------------------------------------------------------------------


def test_local_store_satisfies_artifact_store_protocol(tmp_path: Path) -> None:
    store: ArtifactStore = _store(tmp_path)
    assert isinstance(store, ArtifactStore)


# ---------------------------------------------------------------------------
# put / get round-trip and hash correctness.
# ---------------------------------------------------------------------------


def test_put_then_get_returns_identical_bytes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    data = b"the quick brown fox jumps over the lazy dog"

    descriptor = _put(store, data)

    assert store.get(descriptor.content_hash) == data


def test_put_computes_hash_equal_to_mrr_crypto_hashing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    data = b"artifact bytes for hashing"

    descriptor = _put(store, data)

    assert descriptor.content_hash == content_hash(data)


def test_put_records_every_mrr_fr_051_field(tmp_path: Path) -> None:
    store = _store(tmp_path)
    created_at = datetime.now(UTC)
    data = b"payload"

    descriptor = _put(
        store,
        data,
        media_type="text/csv",
        producer_run_id=_RUN_ID,
        classification="RESTRICTED",
        created_at=created_at,
    )

    assert descriptor.media_type == "text/csv"
    assert descriptor.size_bytes == len(data)
    assert descriptor.producer_run_id == _RUN_ID
    assert descriptor.created_at == created_at
    assert descriptor.classification == "RESTRICTED"

    stat_descriptor = store.stat(descriptor.content_hash)
    assert stat_descriptor == descriptor


def test_put_empty_bytes_round_trips(tmp_path: Path) -> None:
    store = _store(tmp_path)

    descriptor = _put(store, b"")

    assert descriptor.size_bytes == 0
    assert store.get(descriptor.content_hash) == b""


# ---------------------------------------------------------------------------
# Idempotent put.
# ---------------------------------------------------------------------------


def _iter_blob_files(root: Path) -> list[Path]:
    return [
        path for path in root.rglob("*") if path.is_file() and not path.name.endswith(".meta.json")
    ]


def test_put_is_idempotent_one_blob_and_equal_descriptor(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    store = LocalFilesystemArtifactStore(root)
    data = b"identical content, put twice"

    first = _put(store, data, producer_run_id=_RUN_ID)
    second = _put(store, data, producer_run_id=_RUN_ID)

    assert first == second
    assert len(_iter_blob_files(root)) == 1


def test_put_idempotent_re_put_ignores_new_metadata_first_writer_wins(tmp_path: Path) -> None:
    """Re-putting identical bytes with *different* metadata does not raise
    and does not change the stored descriptor — see the adapter module
    docstring's "first-writer metadata wins" rule.
    """
    store = _store(tmp_path)
    data = b"same bytes, different claimed metadata"

    first = _put(
        store,
        data,
        media_type="application/json",
        producer_run_id=_RUN_ID,
        classification="INTERNAL",
    )
    second = _put(
        store,
        data,
        media_type="text/plain",
        producer_run_id=_OTHER_RUN_ID,
        classification="RESTRICTED",
    )

    assert second == first
    assert store.stat(first.content_hash).producer_run_id == _RUN_ID


# ---------------------------------------------------------------------------
# get on an unknown hash.
# ---------------------------------------------------------------------------


def test_get_unknown_hash_raises_not_found(tmp_path: Path) -> None:
    store = _store(tmp_path)
    unknown = "sha256:" + "0" * 64

    with pytest.raises(ArtifactNotFoundError) as excinfo:
        store.get(unknown)
    assert excinfo.value.content_hash == unknown


def test_stat_unknown_hash_raises_not_found(tmp_path: Path) -> None:
    store = _store(tmp_path)
    unknown = "sha256:" + "1" * 64

    with pytest.raises(ArtifactNotFoundError):
        store.stat(unknown)


def test_get_rejects_malformed_content_hash(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(InvalidContentHashError):
        store.get("not-a-valid-hash")


def test_stat_rejects_malformed_content_hash(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(InvalidContentHashError):
        store.stat("not-a-valid-hash")


def test_exists_rejects_malformed_content_hash(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(InvalidContentHashError):
        store.exists("not-a-valid-hash")


# ---------------------------------------------------------------------------
# Integrity verification on read (corruption/tamper detection).
# ---------------------------------------------------------------------------


def _blob_path_for(root: Path, content_hash_value: str) -> Path:
    hex_digest = content_hash_value.removeprefix("sha256:")
    return root / hex_digest[0:2] / hex_digest[2:4] / hex_digest


def test_get_raises_integrity_error_on_corrupted_blob(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    store = LocalFilesystemArtifactStore(root)
    descriptor = _put(store, b"original, uncorrupted bytes")

    blob_path = _blob_path_for(root, descriptor.content_hash)
    blob_path.write_bytes(b"tampered bytes, different content")

    with pytest.raises(ArtifactIntegrityError) as excinfo:
        store.get(descriptor.content_hash)
    assert excinfo.value.expected == descriptor.content_hash
    assert excinfo.value.actual == content_hash(b"tampered bytes, different content")


def test_stat_raises_integrity_error_on_corrupted_blob(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    store = LocalFilesystemArtifactStore(root)
    descriptor = _put(store, b"original, uncorrupted bytes for stat")

    blob_path = _blob_path_for(root, descriptor.content_hash)
    blob_path.write_bytes(b"corrupted")

    with pytest.raises(ArtifactIntegrityError):
        store.stat(descriptor.content_hash)


def test_put_raises_integrity_error_when_pre_existing_blob_is_corrupted(
    tmp_path: Path,
) -> None:
    """A re-put of bytes whose key already has a (corrupted) blob on disk
    must not silently accept the corruption as "already stored".
    """
    root = tmp_path / "artifacts"
    store = LocalFilesystemArtifactStore(root)
    data = b"bytes that will be corrupted before the second put"
    descriptor = _put(store, data)

    blob_path = _blob_path_for(root, descriptor.content_hash)
    blob_path.write_bytes(b"corrupted before re-put")

    with pytest.raises(ArtifactIntegrityError):
        _put(store, data)


# ---------------------------------------------------------------------------
# exists().
# ---------------------------------------------------------------------------


def test_exists_true_after_put(tmp_path: Path) -> None:
    store = _store(tmp_path)
    descriptor = _put(store, b"present")
    assert store.exists(descriptor.content_hash) is True


def test_exists_false_for_unknown_hash(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.exists("sha256:" + "f" * 64) is False


# ---------------------------------------------------------------------------
# Atomic writes — no lingering temp files.
# ---------------------------------------------------------------------------


def test_put_leaves_no_temp_files_behind(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    store = LocalFilesystemArtifactStore(root)

    _put(store, b"clean atomic write")

    leftover_tmp_files = list(root.rglob("*.tmp"))
    assert leftover_tmp_files == []


def test_multiple_puts_leave_no_temp_files_behind(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    store = LocalFilesystemArtifactStore(root)

    for index in range(5):
        _put(store, f"payload number {index}".encode())

    assert list(root.rglob("*.tmp")) == []


# ---------------------------------------------------------------------------
# Sharded on-disk layout.
# ---------------------------------------------------------------------------


def test_blob_and_sidecar_are_sharded_by_hash_prefix(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    store = LocalFilesystemArtifactStore(root)
    descriptor = _put(store, b"sharding check")

    hex_digest = descriptor.content_hash.removeprefix("sha256:")
    expected_blob = root / hex_digest[0:2] / hex_digest[2:4] / hex_digest
    expected_meta = root / hex_digest[0:2] / hex_digest[2:4] / f"{hex_digest}.meta.json"

    assert expected_blob.is_file()
    assert expected_meta.is_file()
