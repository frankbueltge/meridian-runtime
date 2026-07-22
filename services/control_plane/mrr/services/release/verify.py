"""``mrr release verify`` (docs/spec/adr/ADR-0011-RELEASE-RECORD-AND-A4-
APPROVAL-EVENT.md decision 4 / task-packets/E8-T04.yaml R4): two independent
ways to check a persisted ``ReleaseRecord``'s own bundle actually matches
what was recorded — "immutable release" made a CHECKABLE property, never
merely asserted.

- :func:`verify_rebuild` (no ``--bundle-dir``): re-derive the bundle bytes
  FRESH from the archive — a fresh ``ExportService.export`` +
  ``ReportService.render`` into a throwaway temp directory, torn down
  after the check — and compare the freshly computed manifest to the
  STORED record's own ``bundle.files``/``root_hash``. "Trust flows record
  -> archive -> bytes, never bundle -> bundle" (task-packets/E8-T04.yaml
  derived_decisions (d)) — this mode never reads any on-disk bundle
  directory at all, only the object store and (for a public-disclosure
  record) the artifact store via ``ExportService``.
- :func:`verify_bundle_dir`: read an EXISTING on-disk bundle directory's
  own bytes directly (no export, no render, no artifact store) and compare
  THOSE to the same stored record.

Both return the identical :class:`VerifyResult` shape — a match, or a named
list of differing paths (``missing``/``extra``/``changed``) — so
``mrr.services.cli.release_main`` prints one uniform verdict regardless of
which mode ran, and AT3's "the message distinguishes the two verdicts"
follows structurally: the SAME flipped byte makes ``verify_bundle_dir``
report a ``"changed"`` diff at that exact path while ``verify_rebuild``
(re-deriving from the still-intact archive) reports a match, because the
two functions read their bytes from two entirely independent sources.

--- Rebuilding a PUBLIC release needs the SAME attestation the record used ---

A public-disclosure report's OWN content depends on a caller-supplied
``classification_by_object_id`` attestation map (MRR-FR-095's fail-closed
redaction rule, ``mrr.domain.research_report``) — the SAME map
``mrr release create`` required via its own ``--classification-file``.
Rebuilding a public record's bundle WITHOUT re-supplying that same
attestation would re-render a DIFFERENTLY-redacted report and spuriously
mismatch, even though nothing about the archive or the release actually
changed. ``verify_rebuild`` therefore takes an OPTIONAL
``classification_by_object_id`` parameter, mirroring ``ReportService
.render``'s own; ``mrr.services.cli.release_main`` requires
``--classification-file`` for rebuild-mode verify of a public record, exactly
mirroring ``mrr report render``'s own precedent (task-packets/E8-T03.yaml
R4) — a disclosed, necessary addition beyond task-packets/E8-T04.yaml R4's
own (evidently abbreviated) flag list; see this task's own delivery report.
``verify_bundle_dir`` needs no attestation at all: it never re-renders
anything, only reads bytes already on disk.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from mrr.domain.artifacts import ArtifactStore, Classification
from mrr.domain.repositories import EdgeRepository, ObjectRepository
from mrr.domain.research_report import Disclosure, render_html, render_markdown
from mrr.provenance.log import AppendedEvent
from mrr.services.export.service import ExportService
from mrr.services.release.errors import ReleaseRecordKindError
from mrr.services.release.manifest import BundleFileEntry, compute_bundle_manifest
from mrr.services.report.service import ReportService

_RELEASE_RECORD_KIND = "ReleaseRecord"
_RO_CRATE_SUBDIR = "ro-crate"
_REPORT_MD_NAME = "report.md"
_REPORT_HTML_NAME = "report.html"


class _EventJournal(Protocol):
    """Identical in spirit to ``mrr.services.release.bundle._EventJournal``
    — declared independently per this codebase's own established
    per-module Protocol convention.
    """

    def read_all(self) -> list[AppendedEvent]: ...


@dataclass(frozen=True, slots=True)
class PathDiff:
    """One path where the stored record's own ``bundle.files`` and the
    independently-computed bytes disagree.

    ``kind`` is one of:

    - ``"missing"``: the stored record names this path; the independently
      computed bytes do not have it.
    - ``"extra"``: the independently computed bytes have this path; the
      stored record does not name it.
    - ``"changed"``: both have this path, but the content hash differs —
      AT3's own "flipping one byte ... naming exactly that path" case.
    """

    path: str
    kind: str


@dataclass(frozen=True, slots=True)
class VerifyResult:
    release_id: str
    mode: str
    matched: bool
    root_hash: str
    diffs: tuple[PathDiff, ...]


def resolve_release_record(object_repository: ObjectRepository, release_id: str) -> dict[str, Any]:
    """Raises ``mrr.domain.exceptions.ObjectNotFoundError`` if ``release_id``
    does not resolve at all, ``ReleaseRecordKindError`` if it resolves to a
    stored object of the wrong kind.
    """
    stored = object_repository.get_latest(release_id)
    if stored.kind != _RELEASE_RECORD_KIND:
        raise ReleaseRecordKindError(release_id, stored.kind)
    return stored.body


def _diff_files(
    stored_files: list[Mapping[str, str]], actual_files: tuple[BundleFileEntry, ...]
) -> tuple[PathDiff, ...]:
    stored_by_path = {str(entry["path"]): str(entry["sha256"]) for entry in stored_files}
    actual_by_path = {entry.path: entry.sha256 for entry in actual_files}
    diffs: list[PathDiff] = []
    for path in sorted(set(stored_by_path) | set(actual_by_path)):
        stored_hash = stored_by_path.get(path)
        actual_hash = actual_by_path.get(path)
        if stored_hash is None:
            diffs.append(PathDiff(path=path, kind="extra"))
        elif actual_hash is None:
            diffs.append(PathDiff(path=path, kind="missing"))
        elif stored_hash != actual_hash:
            diffs.append(PathDiff(path=path, kind="changed"))
    return tuple(diffs)


def verify_bundle_dir(
    object_repository: ObjectRepository, release_id: str, bundle_dir: Path
) -> VerifyResult:
    """Compare an existing on-disk ``bundle_dir``'s own bytes to the stored
    ``ReleaseRecord`` named by ``release_id``. Never touches the artifact
    store, never re-exports, never re-renders — a pure filesystem read plus
    one object-store lookup.
    """
    body = resolve_release_record(object_repository, release_id)
    manifest = compute_bundle_manifest(bundle_dir)
    stored_bundle = body["bundle"]
    diffs = _diff_files(stored_bundle["files"], manifest.files)
    matched = not diffs and manifest.root_hash == stored_bundle["root_hash"]
    return VerifyResult(
        release_id=release_id,
        mode="bundle-dir",
        matched=matched,
        root_hash=manifest.root_hash,
        diffs=diffs,
    )


def verify_rebuild(
    *,
    object_repository: ObjectRepository,
    edge_repository: EdgeRepository,
    event_log: _EventJournal,
    artifact_store: ArtifactStore,
    release_id: str,
    tmp_parent: Path,
    classification_by_object_id: Mapping[str, Classification] | None = None,
) -> VerifyResult:
    """Re-derive the bundle bytes FRESH from the archive (a throwaway temp
    directory under ``tmp_parent``, always removed before returning) and
    compare to the stored ``ReleaseRecord`` named by ``release_id``. Never
    reads any on-disk bundle directory.
    """
    body = resolve_release_record(object_repository, release_id)
    crate_id = str(body["crate_id"])
    disclosure: Disclosure = body["disclosure"]
    stored_bundle = body["bundle"]

    tmp_dir = Path(tempfile.mkdtemp(dir=tmp_parent, prefix=".release-verify-"))
    try:
        export_service = ExportService(
            object_repository, edge_repository, event_log, artifact_store
        )
        export_service.export(crate_id, tmp_dir / _RO_CRATE_SUBDIR)

        report_service = ReportService(object_repository, edge_repository, event_log)
        report_model = report_service.render(
            crate_id,
            disclosure=disclosure,
            classification_by_object_id=classification_by_object_id,
        )
        (tmp_dir / _REPORT_MD_NAME).write_text(render_markdown(report_model), encoding="utf-8")
        (tmp_dir / _REPORT_HTML_NAME).write_text(render_html(report_model), encoding="utf-8")

        manifest = compute_bundle_manifest(tmp_dir)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    diffs = _diff_files(stored_bundle["files"], manifest.files)
    matched = not diffs and manifest.root_hash == stored_bundle["root_hash"]
    return VerifyResult(
        release_id=release_id,
        mode="rebuild",
        matched=matched,
        root_hash=manifest.root_hash,
        diffs=diffs,
    )
