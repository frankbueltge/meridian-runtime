"""``assemble_and_release`` (docs/spec/adr/ADR-0011-RELEASE-RECORD-AND-A4-
APPROVAL-EVENT.md decision 3 / task-packets/E8-T04.yaml R3): composes the
already-shipped E8-T01/E8-T02 RO-Crate export
(``mrr.services.export.service.ExportService.export``) and the E8-T03 report
renders (``mrr.services.report.service.ReportService.render`` plus
``mrr.domain.research_report.render_markdown``/``render_html``) into ONE
temp directory, computes the deterministic bundle manifest over it
(``mrr.services.release.manifest``), persists the ``ReleaseRecord`` (R2's
atomic revision+event write, ``mrr.services.release.service.ReleaseService
.create``), writes ``release-manifest.json``/``release-record.json`` into
the same temp directory, and finalizes with ONE atomic rename onto
``--output-dir``. ZERO new closure, render, or redaction logic lives here
(task-packets/E8-T04.yaml reviewer_resolution (4): "bundle assembly REUSES
ExportService and ReportService by composition — zero new closure, render,
or redaction logic"); every content byte in the bundle comes from those two
already-tested services, called exactly as their own CLIs already call
them.

--- Bundle directory layout ---------------------------------------------------

    <output-dir>/
      ro-crate/                  (ExportService.export's own output tree)
      report.md                  (render_markdown(ReportService.render(...)))
      report.html                (render_html(ReportService.render(...)))
      release-manifest.json      (the bundle manifest: files + root_hash)
      release-record.json        (the PERSISTED record's own body, verbatim)

--- The fixed order and its ONE named inconsistent state (reviewer_resolution (5)) ---

    1. assemble-content:  write ro-crate/, report.md, report.html into a
                           temp directory (a sibling of --output-dir, same
                           filesystem, so the final rename is atomic —
                           mirrors ExportService._write_export's own
                           discipline); compute the bundle manifest over
                           that temp directory (release-manifest.json and
                           release-record.json do not exist yet, so no
                           explicit exclusion is even needed at this step —
                           see mrr.services.release.manifest's own
                           EXCLUDED_FILENAMES, applied defensively anyway).
    2. persist:            ReleaseService.create(...) — the ONE database
                           transaction boundary. Either the ReleaseRecord
                           revision-1 insert AND its release.approved event
                           BOTH land, or NEITHER does
                           (mrr.persistence.unit_of_work
                           .record_object_revision_with_event's own
                           guarantee) — step 2 itself can never leave a
                           PARTIAL record/event pair.
    3. finalize:            write <tmp>/release-manifest.json and
                           <tmp>/release-record.json (the persisted
                           record's own body), then os.replace(<tmp>,
                           --output-dir) as the single LAST act.

Steps 1 and 3 can each fail with NOTHING durable written anywhere: a plain
filesystem/domain error, the temp directory is removed, and --output-dir is
never touched — an ordinary, safely-retryable failure. Step 2 is the ONE
transaction boundary in the whole function. The ONLY inconsistent state this
function can therefore produce is: **step 2 succeeds, then step 3 fails** —
a ``ReleaseRecord`` exists in the database (with its ``release.approved``
event) but no corresponding bundle directory exists anywhere on disk (the
temp directory is STILL removed in this case; nothing partial is ever left
visible, whether at ``--output-dir`` or at an orphaned temp path). This
function raises :class:`mrr.services.release.errors
.ReleaseBundleFinalizationError` — carrying the persisted ``release_id``/
``revision`` — for EXACTLY this case, so ``mrr.services.cli.release_main``
can name the exact inconsistent state verbatim in its own error output
(task-packets/E8-T04.yaml reviewer_resolution (5)) and point at
``mrr release verify --release-id <id>`` as the recovery path: the record
is real and readable; rebuilding its bundle from the archive either
reproduces it (nothing was actually lost, only the LOCAL COPY at the target
failed to materialize) or, if the archive has itself since changed, reports
the mismatch loudly — never papered over.

No SECOND inconsistent state exists (task-packets/E8-T04.yaml stop_condition
1's own check, resolved here rather than discovered mid-implementation):
step 1 never touches the database at all (``ExportService``/
``ReportService`` are both read-only — see their own module docstrings'
"this service writes NOTHING" sections), and step 2 is a single atomic
transaction by construction, so it cannot itself leave a partial
record/event pair for step 3 to inherit.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from mrr.crypto.canonical import canonicalize
from mrr.domain.artifacts import ArtifactStore, Classification
from mrr.domain.repositories import EdgeRepository, ObjectRepository
from mrr.domain.research_report import Disclosure, render_html, render_markdown
from mrr.persistence.unit_of_work import RecordRevisionWithEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.export.service import ExportService

# Re-exported under a release-scoped name so mrr.services.cli.release_main
# (and this module's own assemble_and_release) can do their own cheap,
# local, NFR-012 pre-check without reaching past this package into
# mrr.services.export.service directly — the SAME rule (and, in fact, the
# SAME function) `mrr export ro-crate`'s own --output-dir check already
# applies, so both commands' "an existing EMPTY directory is not a
# conflict" behavior can never drift apart.
from mrr.services.export.service import output_path_conflict as output_dir_conflict
from mrr.services.release.errors import ReleaseBundleFinalizationError
from mrr.services.release.manifest import compute_bundle_manifest
from mrr.services.release.service import ReleaseService
from mrr.services.report.service import ReportService

#: mypy strict's `--no-implicit-reexport` treats a renamed `import X as Y`
#: as private to this module unless explicitly re-exported — `__all__`
#: below is exactly that explicit re-export list, so
#: `mrr.services.cli.release_main` can import `output_dir_conflict` (and
#: everything else this module's own public surface offers) from here.
__all__ = [
    "ReleaseBundleResult",
    "assemble_and_release",
    "output_dir_conflict",
]

_RO_CRATE_SUBDIR = "ro-crate"
_REPORT_MD_NAME = "report.md"
_REPORT_HTML_NAME = "report.html"
_RELEASE_MANIFEST_NAME = "release-manifest.json"
_RELEASE_RECORD_NAME = "release-record.json"


class _EventJournal(Protocol):
    """The one read operation ``ExportService``/``ReportService`` need from
    an event log — identical in spirit to their own independently-declared
    ``_EventJournal`` Protocols (see either module's own docstring for why
    this codebase declares this Protocol independently per consuming
    module rather than sharing one).
    """

    def read_all(self) -> list[AppendedEvent]: ...


@dataclass(frozen=True, slots=True)
class ReleaseBundleResult:
    """Every fact ``mrr release create`` prints on success (task-packets/
    E8-T04.yaml R4's exit-0 JSON line)."""

    release_id: str
    revision: int
    crate_id: str
    disclosure: str
    approval_mode: str
    root_hash: str
    file_count: int
    output_dir: Path


def assemble_and_release(
    *,
    object_repository: ObjectRepository,
    edge_repository: EdgeRepository,
    event_log: _EventJournal,
    artifact_store: ArtifactStore,
    record: RecordRevisionWithEvent,
    crate_id: str,
    disclosure: Disclosure,
    classification_by_object_id: Mapping[str, Classification] | None,
    approved_by: str,
    approval_statement: str,
    approval_mode: str,
    policy_version: str,
    correlation_id: str,
    output_dir: Path,
) -> ReleaseBundleResult:
    """Run the full three-step sequence described in the module docstring.

    Raises:
        ValueError: ``output_dir`` already exists (file, or non-empty
            directory) — checked FIRST, before any other work.
        mrr.domain.exceptions.ObjectNotFoundError,
        mrr.services.export.service.MissingArtifactBytesError,
        mrr.services.release.errors.NonPersonApproverError,
        mrr.services.release.errors.EmptyApprovalStatementError,
        mrr.services.release.errors.DualApprovalNotSupportedError,
        mrr.services.release.errors.ReleaseCrateKindError,
        mrr.services.release.errors.BundleRootHashMismatchError: any
            ordinary refusal from step 1 (assemble) or step 2 (persist) —
            nothing was persisted, nothing was left on disk.
        mrr.services.release.errors.ReleaseBundleFinalizationError: step 2
            succeeded but step 3 (finalize) failed — see the module
            docstring's "the ONE named inconsistent state" section.
    """
    if output_dir_conflict(output_dir):
        raise ValueError(
            f"--output-dir {output_dir} already exists (as a file, or as a non-empty "
            "directory) — refusing to write over or into it"
        )

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(
        tempfile.mkdtemp(dir=output_dir.parent, prefix=f".{output_dir.name}.release-tmp-")
    )
    try:
        # --- 1. assemble-content ---
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

        # --- 2. persist (the one transaction boundary) ---
        release_service = ReleaseService(object_repository, record)
        stored = release_service.create(
            crate_id=crate_id,
            disclosure=disclosure,
            bundle=manifest,
            approved_by=approved_by,
            approval_statement=approval_statement,
            approval_mode=approval_mode,
            policy_version=policy_version,
            correlation_id=correlation_id,
        )

        # --- 3. finalize ---
        try:
            (tmp_dir / _RELEASE_MANIFEST_NAME).write_bytes(
                canonicalize(
                    {
                        "files": [{"path": f.path, "sha256": f.sha256} for f in manifest.files],
                        "root_hash": manifest.root_hash,
                    }
                )
            )
            (tmp_dir / _RELEASE_RECORD_NAME).write_bytes(canonicalize(stored.body))
            os.replace(tmp_dir, output_dir)
        except BaseException as exc:
            raise ReleaseBundleFinalizationError(stored.id, stored.revision, cause=exc) from exc

        return ReleaseBundleResult(
            release_id=stored.id,
            revision=stored.revision,
            crate_id=crate_id,
            disclosure=disclosure,
            approval_mode=approval_mode,
            root_hash=manifest.root_hash,
            file_count=len(manifest.files),
            output_dir=output_dir,
        )
    except BaseException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
