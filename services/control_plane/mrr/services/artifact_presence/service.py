"""``ArtifactPresenceService`` (task-packets/A2-T01.yaml, "Teil 2 —
Nachsehen"): the read-only, NO-NETWORK, NO-DATABASE application-layer
service behind ``mrr audit artifacts --dump <path>``. Reads ONE
already-committed archive dump directly — mirrors
``mrr.services.citation_audit.service``'s own direct-file shape
(``--manifest``/``--snapshot``, no batch descriptor), not
``mrr.services.anchoring_integrity``'s/``mrr.services.support_audit``'s own
``--batch`` descriptor shape: task-packets/A2-T01.yaml's own objective names
a single ``--dump <path>`` argument, with no descriptor to pin a hash
against, so this service has no fail-closed dump-hash gate either (there is
nothing committed to gate against — the caller names the dump directly).

--- This service opens no database connection and no network connection -----

``sqlalchemy`` is never imported anywhere in this module. Its own I/O is
reading the ``--dump`` file and, when a root was recorded, stat-ing/reading
the candidate blob bytes under it — all from the local filesystem.

--- Never a LocalFilesystemArtifactStore ------------------------------------

This service never constructs ``mrr.adapters.object_store.local
.LocalFilesystemArtifactStore`` — see ``mrr.domain.artifact_presence``'s own
module docstring for why: that class's constructor ``mkdir``\\s its root
the moment it is instantiated, a write side effect a read-only audit tool
must never have, especially over a recorded root that may not exist on this
machine at all (the exact situation docs/design/2026-07-26-a1-fact-lock-
artifact-bytes.md documents). Presence/hash checks below are a plain
``Path.is_file()``/``read_bytes()``/``hashlib.sha256`` — no write, ever.

--- Typed refusals: two kinds, two outcomes at the CLI -----------------------

:class:`ArtifactPresenceInputError` covers every "this input cannot even be
read as data" failure — a missing/unreadable ``--dump`` file, or invalid
UTF-8 (mirrors ``mrr.services.citation_audit.service
.CitationAuditInputError``; ``mrr.services.cli.artifact_presence_main`` maps
this to exit 2). Every OTHER typed error here —
``mrr.domain.archive_dump.ArchiveDumpParseError`` (the dump's ``objects``
COPY block, a RunManifest's ``artifact_store_reference``, or an
EvidenceAnchor's ``snapshot_hash`` is structurally malformed) and
``mrr.domain.artifact_presence.AmbiguousArtifactStoreReferenceError`` (the
dump's RunManifest objects disagree on status or recorded root) — is a
REFUSAL about the DATA's own structure or consistency, not about file I/O
(``artifact_presence_main`` maps both to exit 3).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from mrr.domain.archive_dump import parse_objects_copy_block
from mrr.domain.artifact_presence import (
    ArtifactPresenceVerdict,
    check_artifact_presence,
    derive_blob_path,
    extract_artifact_anchors,
    extract_run_manifest_store_references,
    resolve_dump_store_root,
)
from mrr.domain.artifact_presence_report import (
    ArtifactPresenceReport,
    build_artifact_presence_report,
)
from mrr.domain.exceptions import DomainError

__all__ = ["ArtifactPresenceInputError", "ArtifactPresenceService"]


class ArtifactPresenceInputError(DomainError):
    """Raised when ``--dump`` cannot even be read as data — missing,
    unreadable, or not valid UTF-8. Carries ``path`` and a human-readable
    ``detail``; mapped to exit 2 (MRR-NFR-012 "dependency unavailable") at
    the CLI, never exit 3 — this is not a refusal about the dump's own
    structure, it is "this input does not exist as usable data at all".
    """

    def __init__(self, path: Path, detail: str) -> None:
        self.path = path
        self.detail = detail
        super().__init__(f"{path}: {detail}")


def _hash_file(path: Path) -> str:
    """The ``"sha256:<hex>"`` digest of an already-checked-to-exist file's
    bytes — mirrors ``mrr.services.anchoring_integrity.service
    ._hash_bytes``'s identical convention.
    """
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


class ArtifactPresenceService:
    """docs/design/2026-07-26-a2-derivation-artifact-store-reference.md's
    "Teil 2" section: reads the committed dump at ``--dump``, resolves the
    dump's recorded artifact-store root from its RunManifest object(s), and
    — for every EvidenceAnchor with a ``snapshot_hash`` — derives the
    expected blob path and checks its presence and hash. See the module
    docstring for the full design rationale, above all that this class opens
    no database connection, no network connection, and never writes to the
    filesystem.
    """

    def build_report(self, dump_path: Path) -> ArtifactPresenceReport:
        """Build the full :class:`mrr.domain.artifact_presence_report
        .ArtifactPresenceReport` for the committed dump at ``dump_path``.

        Raises:
            ArtifactPresenceInputError: ``dump_path`` is missing,
                unreadable, or not valid UTF-8.
            mrr.domain.archive_dump.ArchiveDumpParseError: the dump's
                ``objects`` COPY block is structurally malformed, or a
                RunManifest's ``artifact_store_reference``/an
                EvidenceAnchor's ``snapshot_hash`` has the wrong shape.
            mrr.domain.artifact_presence.AmbiguousArtifactStoreReferenceError:
                the dump's RunManifest objects disagree on status (some
                recorded, some not), or all agree on "recorded" but declare
                more than one distinct root.
        """
        try:
            raw_bytes = dump_path.read_bytes()
        except OSError as exc:
            raise ArtifactPresenceInputError(dump_path, f"cannot read file ({exc})") from exc
        try:
            dump_text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ArtifactPresenceInputError(dump_path, f"not valid UTF-8 ({exc})") from exc

        objects = parse_objects_copy_block(dump_text)  # ArchiveDumpParseError propagates (refusal)

        manifests = extract_run_manifest_store_references(objects)
        anchors_with_hash, anchors_without_hash = extract_artifact_anchors(objects)

        # AmbiguousArtifactStoreReferenceError propagates (refusal) — see
        # the module docstring's "typed refusals" section.
        store_root = resolve_dump_store_root(manifests)

        verdicts: list[ArtifactPresenceVerdict] = []
        for anchor in anchors_with_hash:
            if store_root is None:
                # No recorded root anywhere in this dump — no filesystem
                # check is even attempted (see
                # mrr.domain.artifact_presence.check_artifact_presence's own
                # docstring for why this is the well-behaved caller path).
                verdicts.append(
                    check_artifact_presence(
                        anchor,
                        store_root=None,
                        blob_path=None,
                        blob_exists=False,
                        actual_hash=None,
                    )
                )
                continue

            blob_path = derive_blob_path(store_root, anchor.snapshot_hash)
            blob_exists = blob_path.is_file()
            actual_hash = _hash_file(blob_path) if blob_exists else None
            verdicts.append(
                check_artifact_presence(
                    anchor,
                    store_root=store_root,
                    blob_path=str(blob_path),
                    blob_exists=blob_exists,
                    actual_hash=actual_hash,
                )
            )

        return build_artifact_presence_report(
            dump_path=str(dump_path),
            store_root=store_root,
            run_manifest_ids=[manifest.run_id for manifest in manifests],
            verdicts=verdicts,
            anchors_without_snapshot_hash=anchors_without_hash,
        )
