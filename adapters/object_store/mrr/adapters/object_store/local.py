"""Local filesystem implementation of ``mrr.domain.artifacts.ArtifactStore``
(task-packets/E1-T07.yaml).

Layout on disk, rooted at the constructor's ``root`` directory, for an
artifact whose content hash is ``sha256:<hex>``:

    <root>/<hex[0:2]>/<hex[2:4]>/<hex>              the raw bytes
    <root>/<hex[0:2]>/<hex[2:4]>/<hex>.meta.json     its ArtifactDescriptor

Both files live in the same two-level shard directory (derived from the
first four hex characters, so no single directory ever holds more than a
few hundred artifacts even at large scale) and are always written together.
A sidecar file is necessary because MRR-FR-051 requires five metadata
fields (media type, size, producer run, creation time, classification)
alongside the hash itself, and a plain byte store has nowhere else to put
them without embedding them in the blob (which would change the content
hash of the artifact itself).

Re-put metadata rule (task-packets/E1-T07.yaml: "if metadata differs on a
re-put of identical bytes, decide a rule and document it"): **bytes are the
identity; first-writer metadata wins.** If ``put`` is called with data that
hashes to a key this store already holds (verified against the stored
bytes, not merely assumed from key equality), the *existing* descriptor is
returned unchanged — the newly supplied ``media_type``/``producer_run_id``/
``classification``/``created_at`` are silently ignored, not compared,
merged, or used to overwrite. This mirrors how content addressing already
treats "identical bytes" as one indistinguishable object: two calls with the
same bytes but a different claimed ``producer_run_id`` are, from the
content-addressing model's point of view, two provenance claims about the
very same artifact, not two different artifacts — a legitimate and expected
occurrence (e.g. a deterministic pipeline rerun producing byte-identical
output under a new run id). Silently discarding the second caller's metadata
is a deliberate, documented trade-off, not silent data loss of anything the
store promised to keep: the store never promised to keep more than one
descriptor per key. Rejecting the re-put outright was considered and set
aside — it would make content-addressed deduplication unusable for exactly
the deterministic-rerun case it exists to serve. This rule is flagged as an
open specification question in this task's pull request for reviewer
scrutiny.

Atomicity: every write (blob or sidecar) goes through a temp file created in
the *same* shard directory as its final path, followed by ``os.replace``,
which is atomic on POSIX and Windows alike when both paths are on the same
filesystem. A crash or exception between creating the temp file and the
final rename leaves at most an orphaned ``.tmp`` file (cleaned up on the
same code path via a ``try``/``except``) and never a partially written blob
readable at the target key.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from mrr.crypto.hashing import content_hash as _compute_content_hash
from mrr.domain.artifacts import (
    ArtifactDescriptor,
    Classification,
    require_valid_content_hash,
)
from mrr.domain.exceptions import ArtifactIntegrityError, ArtifactNotFoundError

#: Length of the ``sha256:`` prefix stripped before deriving on-disk paths.
_SHA256_PREFIX_LEN = len("sha256:")

#: How many leading hex characters form each of the two shard directory
#: levels (four hex characters total -> up to 65,536 shard directories).
_SHARD_LEVEL_CHARS = 2


class LocalFilesystemArtifactStore:
    """Content-addressed artifact store backed by the local filesystem.

    Implements ``mrr.domain.artifacts.ArtifactStore`` using nothing but the
    standard library (``pathlib``, ``os``, ``tempfile``, ``json``) — no
    framework or object-storage SDK import, matching this namespace root's
    import-linter contract entry.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def put(
        self,
        data: bytes,
        *,
        media_type: str,
        producer_run_id: str,
        classification: Classification,
        created_at: datetime,
    ) -> ArtifactDescriptor:
        key = _compute_content_hash(data)
        hex_digest = key[_SHA256_PREFIX_LEN:]
        blob_path = self._blob_path(hex_digest)
        meta_path = self._meta_path(hex_digest)

        if blob_path.is_file() and meta_path.is_file():
            # Idempotent re-put. Verify the *existing* bytes still hash to
            # the key before trusting them — a re-put is exactly the moment
            # pre-existing on-disk corruption would otherwise go unnoticed,
            # since a plain `exists()` check never reads the bytes at all.
            existing_bytes = blob_path.read_bytes()
            actual = _compute_content_hash(existing_bytes)
            if actual != key:
                raise ArtifactIntegrityError(expected=key, actual=actual)
            # First-writer-wins: return the stored descriptor, ignoring this
            # call's metadata. See the module docstring for the full rule
            # and its rationale.
            return self._read_descriptor(meta_path)

        # Fresh write (also covers recovery from a prior crash that wrote
        # one of the two files but not the other — content addressing
        # guarantees `data` is byte-identical to whatever produced this key
        # before, so re-writing the blob here is always safe).
        descriptor = ArtifactDescriptor(
            content_hash=key,
            media_type=media_type,
            size_bytes=len(data),
            producer_run_id=producer_run_id,
            created_at=created_at,
            classification=classification,
        )
        self._atomic_write(blob_path, data)
        self._atomic_write(meta_path, _serialize_descriptor(descriptor).encode("utf-8"))
        return descriptor

    def get(self, content_hash: str) -> bytes:
        require_valid_content_hash(content_hash)
        hex_digest = content_hash[_SHA256_PREFIX_LEN:]
        blob_path = self._blob_path(hex_digest)

        if not blob_path.is_file():
            raise ArtifactNotFoundError(content_hash)

        data = blob_path.read_bytes()
        actual = _compute_content_hash(data)
        if actual != content_hash:
            raise ArtifactIntegrityError(expected=content_hash, actual=actual)
        return data

    def stat(self, content_hash: str) -> ArtifactDescriptor:
        require_valid_content_hash(content_hash)
        hex_digest = content_hash[_SHA256_PREFIX_LEN:]
        blob_path = self._blob_path(hex_digest)
        meta_path = self._meta_path(hex_digest)

        if not blob_path.is_file() or not meta_path.is_file():
            raise ArtifactNotFoundError(content_hash)

        data = blob_path.read_bytes()
        actual = _compute_content_hash(data)
        if actual != content_hash:
            raise ArtifactIntegrityError(expected=content_hash, actual=actual)
        return self._read_descriptor(meta_path)

    def exists(self, content_hash: str) -> bool:
        require_valid_content_hash(content_hash)
        hex_digest = content_hash[_SHA256_PREFIX_LEN:]
        return self._blob_path(hex_digest).is_file()

    # -- on-disk layout -----------------------------------------------------

    def _shard_dir(self, hex_digest: str) -> Path:
        return (
            self._root
            / hex_digest[:_SHARD_LEVEL_CHARS]
            / hex_digest[_SHARD_LEVEL_CHARS : 2 * _SHARD_LEVEL_CHARS]
        )

    def _blob_path(self, hex_digest: str) -> Path:
        return self._shard_dir(hex_digest) / hex_digest

    def _meta_path(self, hex_digest: str) -> Path:
        return self._shard_dir(hex_digest) / f"{hex_digest}.meta.json"

    def _atomic_write(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as tmp_file:
                tmp_file.write(data)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            os.replace(tmp_name, path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp_name)
            raise

    def _read_descriptor(self, meta_path: Path) -> ArtifactDescriptor:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
        return ArtifactDescriptor(
            content_hash=payload["content_hash"],
            media_type=payload["media_type"],
            size_bytes=payload["size_bytes"],
            producer_run_id=payload["producer_run_id"],
            created_at=datetime.fromisoformat(payload["created_at"]),
            classification=payload["classification"],
        )


def _serialize_descriptor(descriptor: ArtifactDescriptor) -> str:
    payload = {
        "content_hash": descriptor.content_hash,
        "media_type": descriptor.media_type,
        "size_bytes": descriptor.size_bytes,
        "producer_run_id": descriptor.producer_run_id,
        "created_at": descriptor.created_at.isoformat(),
        "classification": descriptor.classification,
    }
    return json.dumps(payload, sort_keys=True)
