"""Content-addressed artifact store interface, per docs/spec/02_DOMAIN_MODEL.md
section 2.7 (Artifact) and MRR-FR-051 ("Every artifact MUST have a media
type, byte size, SHA-256 content hash, producer run, creation time, and
disclosure classification"). MRR-FR-056 requires a sealed crate's artifacts
to stay immutable; MRR-NFR-004 requires storage providers to sit behind an
interface (vendor neutrality).

This module is framework-free (no filesystem, database, or object-storage
SDK import anywhere in it — MRR-NFR-010, enforced by the import-linter
contract in pyproject.toml and by
tests/unit/architecture/test_import_boundaries.py). It declares only:

- ``ArtifactDescriptor`` — the frozen dataclass carrying exactly the six
  MRR-FR-051 fields;
- ``ArtifactStore`` — the ``Protocol`` a concrete adapter implements. The
  first concrete implementation is the local filesystem adapter in
  ``mrr.adapters.object_store.local`` (task-packets/E1-T07.yaml); a future
  MinIO/S3-compatible adapter can implement the same Protocol without this
  module changing at all.

Immutability is enforced by *absence*: the Protocol offers ``put``, ``get``,
``stat``, and ``exists`` — no ``delete``, no ``update``, no way to rebind an
existing key to different bytes. A key can only ever be produced by hashing
the bytes it names, so "distinct bytes, same key" is not a state any
conforming adapter can reach (docs/spec/02_DOMAIN_MODEL.md section 2.7,
"storage locator changes do not change artifact identity; byte changes do").

Content addressing reuses ``mrr.crypto.hashing`` (the ``sha256:<64 hex>``
format and its compiled ``SHA256_PATTERN``) rather than re-implementing
hashing here — the store key IS an artifact's content hash, verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from mrr.crypto.hashing import SHA256_PATTERN
from mrr.domain.exceptions import InvalidContentHashError
from mrr.domain.identity import is_valid_urn

#: The exact five-value classification enum from schemas/common.schema.json
#: ``$defs.artifactRef.properties.classification`` (docs/spec/02_DOMAIN_MODEL.md
#: section 4, disclosure classification).
#:
#: ``mrr.contracts.common.Classification`` already mirrors this same enum,
#: but is deliberately NOT imported here. ``mrr.contracts.common`` imports
#: ``mrr.domain.identity.URN_PATTERN``, i.e. ``mrr.contracts`` already
#: depends on ``mrr.domain`` — importing ``mrr.contracts`` back from
#: ``mrr.domain`` would turn that into a package cycle (domain -> contracts
#: -> domain), which no other module in this monorepo does. Re-declaring the
#: Literal locally instead follows the precedent ``mrr.contracts.common``
#: itself already sets for this exact enum: its own docstring calls it "the
#: repeated classification enum ... used inline by several schemas" and
#: duplicates it again in ``task_bundle.py``/``research_score.py`` call
#: sites, unlike ``urn`` or ``sha256``, which that module explicitly keeps
#: single-sourced via ``Annotated`` aliases over the compiled patterns in
#: ``mrr.domain.identity``/``mrr.crypto.hashing``. A classification enum
#: appearing in a third place (here) is consistent with a pattern that
#: already appears in two.
Classification = Literal[
    "PUBLIC",
    "INTERNAL",
    "RESTRICTED",
    "SENSITIVE",
    "PARTICIPANT_IDENTIFIABLE",
]


def require_valid_content_hash(content_hash: str) -> None:
    """Raise :class:`mrr.domain.exceptions.InvalidContentHashError` unless
    ``content_hash`` matches the exact ``$defs.sha256`` pattern
    (``mrr.crypto.hashing.SHA256_PATTERN``, ``sha256:<64 lowercase hex>``).

    Adapters call this before any lookup (``get``/``stat``/``exists``) so a
    malformed key fails closed instead of being silently treated as
    "not found", or — worse — matched against on-disk content by accident.
    """
    if not SHA256_PATTERN.match(content_hash):
        raise InvalidContentHashError(content_hash)


@dataclass(frozen=True, slots=True)
class ArtifactDescriptor:
    """The six MRR-FR-051 fields describing one immutable stored artifact.

    ``content_hash`` is both the descriptor's own identity field and the
    store's lookup key (``ArtifactStore.get``/``stat``/``exists`` all take a
    ``content_hash`` string, not a separate artifact id) — the store IS
    content-addressed, per the task-packet's derived decision.

    Deliberately excludes the storage locator, encryption metadata, retention
    policy, semantic role, and redacted-derivatives fields that
    docs/spec/02_DOMAIN_MODEL.md section 2.7 also lists for the eventual
    first-class ``Artifact`` object: MRR-FR-051 only mandates the six fields
    below, and this task packet scopes the store to exactly those six (see
    the module docstring and the PR's "open specification questions" for the
    deferred fields).
    """

    content_hash: str
    media_type: str
    size_bytes: int
    producer_run_id: str
    created_at: datetime
    classification: Classification

    def __post_init__(self) -> None:
        require_valid_content_hash(self.content_hash)
        if not self.media_type:
            raise ValueError("media_type must not be empty")
        if self.size_bytes < 0:
            raise ValueError(f"size_bytes must be >= 0, got {self.size_bytes}")
        if not is_valid_urn(self.producer_run_id):
            raise ValueError(f"producer_run_id is not a valid MRR urn: {self.producer_run_id!r}")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be an aware datetime")


@runtime_checkable
class ArtifactStore(Protocol):
    """Content-addressed byte store for immutable artifacts.

    No update, delete, or overwrite method is offered anywhere on this
    interface — the only write operation is ``put``, and per the invariants
    below it never rebinds an existing key to different bytes. Immutability
    is enforced by the shape of this Protocol, not by a runtime check a
    caller could bypass.

    Invariants every conforming implementation MUST uphold
    (task-packets/E1-T07.yaml):

    - the store key is the artifact's SHA-256 content hash; identical bytes
      map to one blob, and distinct bytes cannot share a key;
    - ``put`` is idempotent — putting identical bytes twice yields the same
      descriptor and one stored blob, never a second copy or an error;
    - stored bytes are immutable — a key can never be rebound to different
      bytes;
    - ``get``/``stat`` verify integrity on every read and fail closed with
      :class:`mrr.domain.exceptions.ArtifactIntegrityError` if a stored
      blob's bytes no longer hash to its key, rather than returning wrong
      bytes;
    - writes are atomic — a failed or partial write never leaves a readable
      blob at the target key.
    """

    def put(
        self,
        data: bytes,
        *,
        media_type: str,
        producer_run_id: str,
        classification: Classification,
        created_at: datetime,
    ) -> ArtifactDescriptor:
        """Store ``data``, keyed by its SHA-256 content hash, and return its
        descriptor.

        Idempotent: if ``data`` already hashes to a key this store already
        holds, no second copy is written and no error is raised — see the
        concrete adapter's docstring for the exact re-put metadata rule.

        Raises:
            mrr.domain.exceptions.ArtifactIntegrityError: if a blob already
                stored under this content's key no longer hashes to that key
                (pre-existing on-disk corruption discovered on re-put).
        """
        ...

    def get(self, content_hash: str) -> bytes:
        """Return the exact bytes stored under ``content_hash``.

        Raises:
            mrr.domain.exceptions.InvalidContentHashError: if
                ``content_hash`` does not match the ``sha256:<64 hex>``
                pattern.
            mrr.domain.exceptions.ArtifactNotFoundError: if no artifact is
                stored under ``content_hash``.
            mrr.domain.exceptions.ArtifactIntegrityError: if the stored
                bytes no longer hash to ``content_hash`` (corruption or
                tampering) — never returns the mismatched bytes.
        """
        ...

    def stat(self, content_hash: str) -> ArtifactDescriptor:
        """Return the descriptor stored under ``content_hash``, without
        returning the bytes themselves.

        Raises the same errors as :meth:`get`, for the same reasons
        (including :class:`~mrr.domain.exceptions.ArtifactIntegrityError` on
        a corrupted blob — ``stat`` verifies integrity too, not only
        ``get``).
        """
        ...

    def exists(self, content_hash: str) -> bool:
        """Return ``True`` if an artifact is stored under ``content_hash``.

        Raises:
            mrr.domain.exceptions.InvalidContentHashError: if
                ``content_hash`` does not match the ``sha256:<64 hex>``
                pattern — even existence checks fail closed on malformed
                input rather than simply returning ``False``.
        """
        ...
