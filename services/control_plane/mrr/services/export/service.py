"""``ExportService`` (task-packets/E8-T01.yaml): the application-layer,
READ-ONLY service that turns one sealed ``EvidenceCrate`` and its
provenance neighborhood into a self-contained, offline-verifiable RO-Crate
1.1 export directory. First task of Epic E8; the closest template is
``mrr.services.projection.service.ProjectionService`` (E3-T07) — the same
``ObjectRepository``/``EdgeRepository``/narrow event-journal Protocol
dependency triple, the same "writes NOTHING" discipline, and the same
event-log-scan discovery technique for a kind this service needs to find
without a dedicated "list all" repository method (here:
``VerificationResult``, exactly as that service discovers claims and
corrections). ``ExportService`` additionally depends on
``mrr.domain.artifacts.ArtifactStore`` (E1-T07), since a crate's own
artifact BYTES — unlike every other object this task exports — live outside
``ObjectRepository`` entirely.

The pure shaping half (file naming, the RO-Crate metadata document itself)
is ``mrr.domain.ro_crate`` — read that module's docstring first for the
full RO-Crate design; this module's job is exactly the I/O this task's own
R1 explicitly excludes from that pure module: resolving urns, walking the
provenance graph, fetching artifact bytes, and writing the export directory
to disk, atomically.

--- This service writes NOTHING -----------------------------------------------

``export`` calls only ``ObjectRepository.get_latest``, ``EdgeRepository
.edges_from`` (via the internal ``ProjectionService`` it composes — see
below), ``_EventJournal.read_all``, and ``ArtifactStore.get`` — never
``insert_revision``, ``add_edge``, ``append``, or ``ArtifactStore.put``.
There is no ``RecordRevisionWithEvent``/``RecordEdgeWithEvent`` dependency
anywhere on this class, matching ``ProjectionService``'s own identical
stance and for the identical reason: task-packets/E8-T01.yaml's own
``forbidden_changes`` names ``migrations/**`` (no new tables — export is
read-only) and every one of this task's invariants is phrased as a
guarantee about READING an already-sealed crate, never about producing new
authoritative state. The one filesystem WRITE this service performs — the
export directory itself — is not a write to any of AGENTS.md's named
sources of truth (database, event log, object storage's SEALED artifact
bytes); it is a derived, re-creatable rendering of what those sources
already say, exactly as "narrative reports are projections" already
licenses for ``ProjectionService``'s own claim table.

--- Reusing the provenance BFS: composition, not a fork (R2(c), a stop_condition) --

task-packets/E8-T01.yaml's own derivation text names
"``mrr.domain.projection.build_provenance_map``" as the BFS this task must
reuse. That function does not actually exist there: ``mrr.domain.projection``
(the pure module) holds only the ``ProvenanceEdge``/``ProvenanceMap``
dataclasses and unrelated claim-table shaping functions; the ACTUAL
traversal — ``build_provenance_map``, ``_trace_provenance``,
``_outgoing_provenance_hops`` — lives on the application-layer
``mrr.services.projection.service.ProjectionService`` (E3-T07), since a BFS
over ``ObjectRepository``/``EdgeRepository`` is inherently I/O, which the
pure domain layer cannot perform at all. This is a minor imprecision in the
packet's own derivation prose, not a specification conflict requiring the
stop_condition ("if reusing the BFS requires modifying that module ...
STOP"): no modification to ``mrr.domain.projection`` is needed, and no
divergent fork of the traversal logic was written here either. ``export``
satisfies the actual, intended requirement — R2(c)'s own words, "reusing
that module's traversal semantics, not re-inventing them" — by COMPOSING an
internal ``ProjectionService`` instance (built from the exact same
``ObjectRepository``/``EdgeRepository``/event-journal dependencies this
service already holds) and calling its already-shipped, already-tested
``build_provenance_map`` once per proposed claim. This is a well-precedented
pattern in this codebase, not a new one: ``mrr.services.verification.service
.VerificationService`` already composes ``mrr.services.claim.service
.ClaimService`` the same way, and ``mrr.services.cli.verification_orchestration``
composes both. Flagged here, and in the packet report, for reviewer
scrutiny of the derivation-text/actual-code mismatch specifically.

--- The R2 closure, built in four steps ---------------------------------------

1. The crate itself (R2a): ``ObjectRepository.get_latest(crate_id)``,
   checked to actually be of kind ``EvidenceCrate`` — anything else is a
   caller-input refusal (``ValueError``), not a domain fact this service
   invents an interpretation for.
2. Every object the crate's own ``source_records``/``evidence_anchors``/
   ``proposed_claims`` arrays name (R2b) — resolved via ``get_latest``,
   FAIL-FAST: the first urn that does not resolve raises
   ``mrr.domain.exceptions.ObjectNotFoundError`` (already carrying that
   exact urn) immediately, matching R2's own "an explicit refusal naming
   the URN" — task-packets/E8-T01.yaml does not additionally ask this case
   to be aggregated the way R3 explicitly asks artifact-byte misses to be
   (see step 4); resolving referenced objects one at a time and stopping at
   the first miss is the more literal reading of R2's own, singular
   "naming the URN" phrasing. The crate's ``artifacts`` array is handled
   separately (step 4) — it names artifact BYTES via ``ArtifactRef``
   descriptors, not ``ObjectRepository``-resolvable objects at all (see
   ``mrr.domain.ro_crate``'s own docstring, "Artifacts get File entities
   only").
3. For each of the crate's OWN ``proposed_claims`` (not any claim
   subsequently discovered — see below), the full transitive closure
   ``ProjectionService.build_provenance_map`` already computes (R2c). Since
   that BFS is ALREADY fully transitive per claim (it expands its frontier
   from every newly discovered id, of any kind, including another claim
   reached multi-hop), there is no need for THIS service to re-seed the BFS
   for a claim discovered only via another claim's own edges — one call per
   top-level proposed claim already reaches everything reachable from it.
4. Every ``VerificationResult`` whose ``target_id`` names a ``Claim``-kind
   object already in the closure built by steps 1-3 (R2d) — "an included
   claim" is read as ANY such object, not narrowly the crate's own
   ``proposed_claims`` array, since a claim reached only transitively in
   step 3 (via some edge from a top-level proposed claim to another Claim)
   is, structurally, exactly as "included" as one named directly by the
   crate. Discovery mirrors ``ProjectionService``'s own claim/correction
   discovery mechanism exactly: scan ``_EventJournal.read_all()`` for
   ``"verification.recorded"`` events (the event
   ``mrr.services.verification.service.VerificationService.record`` always
   appends, transcribed here rather than imported — see
   ``ProjectionService``'s own module docstring for why that event-type
   string is never actually exported by its owning module), resolve each
   candidate id, and keep it only if it actually resolves to a
   ``VerificationResult`` whose own ``target_id`` is in the claim set — a
   dangling event with no resolvable object behind it is skipped, not an
   error (the identical fail-soft stance ``ProjectionService.build_claim_table``
   already documents for its own dangling-genesis-event case).

Then, and only then (R3): every artifact byte referenced by the crate's own
``artifacts`` array is fetched from ``ArtifactStore.get`` — EVERY content
hash is attempted, and every miss is collected before this method raises
``MissingArtifactBytesError`` naming ALL of them in one report (not
first-miss-wins — the one place in this whole closure where R3's own text
explicitly asks for that stronger guarantee, unlike step 2 above).

--- Atomic, all-or-nothing directory write (R3) --------------------------------

``export`` refuses BEFORE any work — before even resolving ``crate_id`` — if
``output_dir`` already exists as a file, or as a non-empty directory
(``output_path_conflict``, also called by ``mrr.services.cli.export_main``
as its own cheap, local, NFR-012 pre-flight check — see that module's
docstring for why the same check legitimately runs in both places). Once
the full plan is built, every file is written into a temp directory created
as a SIBLING of ``output_dir`` (same parent, hence same filesystem, hence
``os.replace`` is atomic), and ``os.replace(tmp_dir, output_dir)`` is the
LAST act. On POSIX, ``os.replace`` on a directory target either creates
``output_dir`` fresh (nothing there before) or atomically replaces an
existing EMPTY directory in place (confirmed directly against this
project's own runtime target, not assumed from documentation prose alone —
Python's own ``os.replace`` docs are easy to misread here); a non-empty
directory or a file at the target raises ``OSError`` at that point too — a
second, defense-in-depth guarantee behind the up-front
``output_path_conflict`` check, not the primary one. Any exception raised
while the temp directory is being populated is caught, the temp directory
is removed, and the exception re-raised — ``output_dir`` itself is never
touched until the single atomic rename, so a failure partway through
leaves, at most, an orphaned temp directory next to it, never a partial
tree visible AT the target path (task-packets/E8-T01.yaml invariant).
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from mrr.contracts import Urn
from mrr.crypto.canonical import JSONValue, canonicalize
from mrr.domain.artifacts import ArtifactStore
from mrr.domain.exceptions import ArtifactNotFoundError, DomainError, ObjectNotFoundError
from mrr.domain.repositories import EdgeRepository, ObjectRepository
from mrr.domain.ro_crate import METADATA_FILE_NAME, ExportPlan, build_export
from mrr.provenance.log import AppendedEvent
from mrr.services.projection.service import ProjectionService

#: task-packets/E8-T01.yaml R2a: the ONLY kind ``--crate-id`` may resolve to.
_EVIDENCE_CRATE_KIND = "EvidenceCrate"

#: The kind checked when deciding whether an included object counts as "an
#: included claim" for R2d's VerificationResult discovery (see the module
#: docstring's closure step 4).
_CLAIM_KIND = "Claim"

#: The kind a discovered event's own object must actually resolve to for
#: R2d — matches ``mrr.contracts.verification_result.VerificationResult
#: .kind``'s own ``Literal["VerificationResult"]``.
_VERIFICATION_RESULT_KIND = "VerificationResult"

#: ``mrr.services.verification.service.VerificationService.record`` always
#: appends exactly this event type — transcribed here, not imported, for
#: the same reason ``mrr.services.projection.service`` transcribes
#: "claim.created"/"correction.recorded" rather than importing them (see
#: that module's own module docstring, "Claim/correction discovery").
_VERIFICATION_RECORDED_EVENT_TYPE = "verification.recorded"

#: R2b: the three crate fields naming id-addressable objects, resolved via
#: ``ObjectRepository.get_latest`` — distinct from ``artifacts`` (R2b, R3),
#: which names artifact BYTES via ``ArtifactRef`` descriptors instead (see
#: ``mrr.domain.ro_crate``'s own docstring, "Artifacts get File entities
#: only").
_CRATE_URN_ARRAY_FIELDS: tuple[str, ...] = ("source_records", "evidence_anchors", "proposed_claims")

#: The two subdirectories every export tree has, beyond the metadata file at
#: its root — created up front so every ``ExportedObject``/``ExportedArtifact
#: .relative_path`` (``objects/...``/``artifacts/...``) can be written
#: without a per-file ``mkdir``.
_EXPORT_SUBDIRECTORIES: tuple[str, ...] = ("objects", "artifacts")


class MissingArtifactBytesError(DomainError):
    """Raised by ``ExportService.export`` when one or more content hashes
    named by the crate's own ``artifacts`` (``ArtifactRef``) array have no
    corresponding blob in the given ``ArtifactStore`` — see the module
    docstring's "atomic, all-or-nothing" and closure sections. Carries every
    missing hash discovered in a single pass (task-packets/E8-T01.yaml R3:
    "one report, not first-miss-wins"), sorted for a deterministic message.
    """

    def __init__(self, missing_content_hashes: Sequence[str]) -> None:
        self.missing_content_hashes = tuple(sorted(missing_content_hashes))
        super().__init__(
            "missing artifact bytes for content hash(es): " + ", ".join(self.missing_content_hashes)
        )


class _EventJournal(Protocol):
    """The one read operation this service needs from an event log —
    identical to ``mrr.services.projection.service._EventJournal`` (not
    imported from there: that class is private to its own module, matching
    ``mrr.services.correction.service._EventJournal``'s own precedent of an
    independently-declared, structurally-identical narrower-than-``EventLog``
    Protocol per consuming module).
    """

    def read_all(self) -> list[AppendedEvent]: ...


@dataclass(frozen=True, slots=True)
class ExportResult:
    """Every fact ``mrr export ro-crate`` prints (task-packets/E8-T01.yaml
    R5's exit-0 JSON line) — mirrors
    ``mrr.services.cli.verification_orchestration.VerificationRecordingResult``'s
    own "everything a caller needs, without re-running anything" shape.
    ``total_bytes`` sums every byte actually written to disk: every object
    file's canonical bytes, every artifact's raw bytes, and the metadata
    document's own canonical bytes — the size of the WHOLE exported tree,
    not merely the objects/artifacts (a disclosed reading; task-packets/
    E8-T01.yaml does not itself define what "total bytes" ranges over).
    """

    crate_id: Urn
    output_dir: Path
    object_count: int
    artifact_count: int
    total_bytes: int


def output_path_conflict(output_dir: Path) -> bool:
    """``True`` iff ``output_dir`` already exists as a file, or as a
    non-empty directory (task-packets/E8-T01.yaml R3: "the target path
    existing beforehand (file or non-empty dir) is a refusal before any
    work"). An existing, EMPTY directory is NOT a conflict — see
    ``ExportService.export``'s own docstring for why ``os.replace`` can
    still atomically take its place.

    Shared, verbatim, between ``ExportService.export``'s own authoritative
    check (this invariant must hold regardless of caller — a future caller
    might construct and call this service directly, bypassing the CLI) and
    ``mrr.services.cli.export_main``'s cheap, local, NFR-012 pre-flight
    check (run before opening a database connection at all) — one function,
    not two independent copies that could drift.
    """
    if not output_dir.exists():
        return False
    if output_dir.is_file():
        return True
    return any(output_dir.iterdir())


class ExportService:
    """docs/spec/01_SYSTEM_SPEC.md MRR-FR-055 (first half), implemented per
    task-packets/E8-T01.yaml. See the module docstring for the full design
    rationale — above all, that this class writes no object/revision/
    event/edge/artifact anywhere; it is a pure read-and-render layer over
    already-sealed state, exactly as ``ProjectionService`` is for the claim
    table and provenance map.
    """

    def __init__(
        self,
        object_repository: ObjectRepository,
        edge_repository: EdgeRepository,
        event_log: _EventJournal,
        artifact_store: ArtifactStore,
    ) -> None:
        self._object_repository = object_repository
        self._edge_repository = edge_repository
        self._event_log = event_log
        self._artifact_store = artifact_store

    def export(self, crate_id: Urn, output_dir: Path) -> ExportResult:
        """Export the RO-Crate closure (R2) for ``crate_id`` into
        ``output_dir``, atomically (R3). See the module docstring for the
        full four-step closure algorithm and the atomic-write discipline.

        Raises:
            ValueError: ``output_dir`` already exists (file, or non-empty
                directory) — checked first, before any read; or
                ``crate_id`` resolves to a stored object whose ``kind`` is
                not ``EvidenceCrate``.
            mrr.domain.exceptions.ObjectNotFoundError: ``crate_id``, or any
                urn the crate's own ``source_records``/``evidence_anchors``/
                ``proposed_claims`` arrays name, does not resolve to any
                stored object — carries the exact missing urn.
            MissingArtifactBytesError: one or more content hashes named by
                the crate's own ``artifacts`` array have no corresponding
                blob in the given ``ArtifactStore`` — carries every missing
                hash.
        """
        if output_path_conflict(output_dir):
            raise ValueError(
                f"--output-dir {output_dir} already exists (as a file, or as a non-empty "
                "directory) — refusing to write over or into it"
            )

        crate = self._object_repository.get_latest(crate_id)
        if crate.kind != _EVIDENCE_CRATE_KIND:
            raise ValueError(
                f"--crate-id {crate_id!r} resolves to a stored object of kind {crate.kind!r}, "
                f"not {_EVIDENCE_CRATE_KIND!r}"
            )

        object_bodies: dict[str, Mapping[str, JSONValue]] = {crate_id: crate.body}
        for field_name in _CRATE_URN_ARRAY_FIELDS:
            for urn in crate.body.get(field_name, []):
                if urn not in object_bodies:
                    object_bodies[urn] = self._object_repository.get_latest(urn).body

        projection_service = ProjectionService(
            self._object_repository, self._edge_repository, self._event_log
        )
        for claim_id in sorted(crate.body.get("proposed_claims", [])):
            provenance = projection_service.build_provenance_map(claim_id)
            for edge in provenance.edges:
                if edge.target_id not in object_bodies:
                    object_bodies[edge.target_id] = self._object_repository.get_latest(
                        edge.target_id
                    ).body

        claim_ids_in_closure = {
            urn for urn, body in object_bodies.items() if body.get("kind") == _CLAIM_KIND
        }
        object_bodies.update(self._discover_verifications_targeting(claim_ids_in_closure))

        artifact_refs = crate.body.get("artifacts", [])
        artifact_bytes, missing_content_hashes = self._fetch_artifact_bytes(artifact_refs)
        if missing_content_hashes:
            raise MissingArtifactBytesError(missing_content_hashes)

        artifact_sizes = {content_hash: len(data) for content_hash, data in artifact_bytes.items()}
        plan, metadata = build_export(
            crate_urn=crate_id, object_bodies=object_bodies, artifact_sizes=artifact_sizes
        )

        total_bytes = self._write_export(output_dir, plan, metadata, artifact_bytes)

        return ExportResult(
            crate_id=crate_id,
            output_dir=output_dir,
            object_count=len(plan.objects),
            artifact_count=len(plan.artifacts),
            total_bytes=total_bytes,
        )

    # ------------------------------------------------------------------
    # Internal helpers.
    # ------------------------------------------------------------------

    def _discover_verifications_targeting(
        self, claim_ids: set[str]
    ) -> dict[str, Mapping[str, JSONValue]]:
        """R2d — see the module docstring's closure step 4 for the full
        rationale. Fails soft on a dangling ``verification.recorded`` event
        (no resolvable object behind it, or a resolved object that is not
        actually a ``VerificationResult``) rather than raising, matching
        ``ProjectionService.build_claim_table``'s own identical stance.
        """
        candidate_ids = {
            appended.event.object_id
            for appended in self._event_log.read_all()
            if appended.event.event_type == _VERIFICATION_RECORDED_EVENT_TYPE
        }
        matched: dict[str, Mapping[str, JSONValue]] = {}
        for verification_id in candidate_ids:
            try:
                obj = self._object_repository.get_latest(verification_id)
            except ObjectNotFoundError:
                continue
            if obj.kind == _VERIFICATION_RESULT_KIND and obj.body.get("target_id") in claim_ids:
                matched[verification_id] = obj.body
        return matched

    def _fetch_artifact_bytes(
        self, artifact_refs: Sequence[Mapping[str, JSONValue]]
    ) -> tuple[dict[str, bytes], list[str]]:
        """R3 — fetch every distinct content hash the crate's own
        ``artifacts`` array names, checking ALL of them before reporting any
        failure (never first-miss-wins). Returns ``(fetched_bytes,
        sorted_missing_hashes)``; the caller raises
        ``MissingArtifactBytesError`` iff the second element is non-empty.
        """
        content_hashes = sorted({str(ref["content_hash"]) for ref in artifact_refs})
        fetched: dict[str, bytes] = {}
        missing: list[str] = []
        for content_hash in content_hashes:
            try:
                fetched[content_hash] = self._artifact_store.get(content_hash)
            except ArtifactNotFoundError:
                missing.append(content_hash)
        return fetched, missing

    def _write_export(
        self,
        output_dir: Path,
        plan: ExportPlan,
        metadata: dict[str, JSONValue],
        artifact_bytes: Mapping[str, bytes],
    ) -> int:
        """Assemble the full export tree in a temp sibling directory, then
        ``os.replace`` it onto ``output_dir`` as the last act — see the
        module docstring's "atomic, all-or-nothing directory write" section.
        """
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = Path(
            tempfile.mkdtemp(dir=output_dir.parent, prefix=f".{output_dir.name}.export-tmp-")
        )
        try:
            for subdirectory in _EXPORT_SUBDIRECTORIES:
                (tmp_dir / subdirectory).mkdir()

            total_bytes = 0
            for obj in plan.objects:
                (tmp_dir / obj.relative_path).write_bytes(obj.canonical_bytes)
                total_bytes += len(obj.canonical_bytes)
            for artifact in plan.artifacts:
                data = artifact_bytes[artifact.content_hash]
                (tmp_dir / artifact.relative_path).write_bytes(data)
                total_bytes += len(data)

            metadata_bytes = canonicalize(metadata)
            (tmp_dir / METADATA_FILE_NAME).write_bytes(metadata_bytes)
            total_bytes += len(metadata_bytes)

            os.replace(tmp_dir, output_dir)
            return total_bytes
        except BaseException:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise
