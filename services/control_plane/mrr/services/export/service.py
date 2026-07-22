"""``ExportService`` (task-packets/E8-T01.yaml, EXTENDED by task-packets/
E8-T02.yaml R4 — see "Provenance edges feed the PROV layer (E8-T02)" below —
and by task-packets/E8-T03.yaml R2, which extracts the closure-resolution
half of ``export`` into a new public, read-only :meth:`ExportService
.resolve_closure` method, behavior-identical by construction — see that
method's own docstring and :class:`ExportClosure`): the application-layer,
READ-ONLY service that turns one sealed ``EvidenceCrate`` and its provenance
neighborhood into a self-contained, offline-verifiable RO-Crate 1.1 export
directory. First task of Epic E8; the closest template is ``mrr.services
.projection.service.ProjectionService`` (E3-T07) — the same
``ObjectRepository``/``EdgeRepository``/narrow event-journal Protocol
dependency triple, the same "writes NOTHING" discipline, and the same
event-log-scan discovery technique for a kind this service needs to find
without a dedicated "list all" repository method (here: ``VerificationResult``,
exactly as that service discovers claims and corrections). ``ExportService``
additionally depends on ``mrr.domain.artifacts.ArtifactStore`` (E1-T07),
since a crate's own artifact BYTES — unlike every other object this task
exports — live outside ``ObjectRepository`` entirely.

--- E8-T03's R2 extraction: one closure, two consumers -----------------------

``mrr.services.report.service.ReportService`` needs the EXACT SAME closure
(object bodies, provenance edges, artifact refs) this service already
resolves for export, so a research report never diverges from what the
export ships (task-packets/E8-T03.yaml derived_decisions (a): "a report
statement whose object is absent from the RO-Crate export would be a
projection of nothing verifiable"). :meth:`resolve_closure` is exactly the
FIRST three-plus-one steps of ``export``'s own prior body (crate resolution,
the three ``_CRATE_URN_ARRAY_FIELDS``, the per-claim provenance BFS, R2d
verification discovery) with the fourth (R3's artifact-BYTE fetch) left OUT
— ``export`` itself now calls :meth:`resolve_closure` and then performs ONLY
that fourth step (fetch bytes, build the plan, write the tree) on top. No
line of the closure algorithm changed; task-packets/E8-T01.yaml's/E8-T02
.yaml's own test suites pass UNMODIFIED against this refactor, which is the
proof of behavior-identity task-packets/E8-T03.yaml stop_condition 2 demands.

The pure shaping half (file naming, the RO-Crate metadata document itself)
is ``mrr.domain.ro_crate`` — read that module's docstring first for the
full RO-Crate design; this module's job is exactly the I/O this task's own
R1 explicitly excludes from that pure module: resolving urns, walking the
provenance graph, fetching artifact bytes, and writing the export directory
to disk, atomically.

--- Provenance edges feed the PROV layer (task-packets/E8-T02.yaml R4) --------

E8-T01's own ``export`` already called ``ProjectionService.build_provenance_
map`` once per proposed claim (see "Reusing the provenance BFS" below) and
then DISCARDED every ``ProvenanceMap.edges`` hop the instant it had used it
to resolve one more urn into ``object_bodies`` — R4 is exactly, and only,
"stop discarding them": every hop from every one of those per-claim calls is
now also accumulated into a ``set[ProvenanceEdge]`` (``ProvenanceEdge`` is
frozen/``slots``, hence hashable, hence a plain ``set`` already IS "the
union ... deduplicated" R4 asks for — no bespoke dedup logic needed), then
sorted by ``(source_id, relation, target_id, via)`` — the exact same key
``ProjectionService._trace_provenance`` already sorts a SINGLE claim's own
hops by, reused here (not re-derived) across the UNION of every proposed
claim's hops, for the identical reason: a deterministic order regardless of
dict/set iteration order or claim-processing order. The resulting tuple is
handed to ``mrr.domain.ro_crate.build_export`` as its new ``provenance_edges``
parameter, exactly once, per export — no second repository/store call was
added (the edges were already being read; only their post-use handling
changed), no closure change (which urns get resolved into ``object_bodies``
is byte-for-byte identical to E8-T01 — R4's own "no closure change"), no CLI
change (``mrr.services.cli.export_main`` calls ``ExportService.export`` the
same way it always did).

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

--- task-packets/E8-T06.yaml: a SECOND closure root, claim-graph-rooted -------

Closes docs/design/2026-07-22-erste-nutzung-befunde.md's Befund 2: the real
K1-T04 run's ``EvidenceCrate`` shipped with EMPTY ``proposed_claims``/
``source_records``/``evidence_anchors`` — the crate seals the run's
*inputs*, the claim graph is a separate downstream step that never links
back to it — so :meth:`resolve_closure` above, crate-rooted by construction,
exports only the crate itself (``object_count: 1``) for that real run, even
though the schema holds the full claim graph. :meth:`resolve_closure_from_
claims` is the additive, backward-compatible fix (Richtung 2 of that
finding, not Richtung 1's "populate the crate retroactively", which would
require mutating an already-sealed object): a SECOND way to seed the exact
same closure algorithm, rooted on claims instead of a crate.

**Fact-lock, verified first-hand against the real schema before writing a
line of this section** (task-packets/E8-T06.yaml stop_condition: "verify the
crux yourself... fact-lock, don't assume") — queried directly with
``MRR_TEST_DATABASE_URL`` against the ``mrr_k1t04_real_run_v2`` schema (the
real K1-T04 run, still on disk in the shared test Postgres instance):

- ``ProjectionService.build_claim_table()`` enumerates exactly 2 claims (the
  two real model-collapse claims — "4 Claims" in the finding record counted
  REVISIONS, not distinct claims).
- For the Hammond claim (``evidence_relations`` names 1 anchor,
  ``counterevidence_relations`` names 13), ``ProjectionService
  .build_provenance_map(claim_id)`` returns exactly 2 edges — ``ruled_by`` ->
  MethodRuling and ``governed_by_protocol`` -> MethodProtocol — and NEITHER
  targets the anchor. Confirmed: the existing provenance BFS does not, and
  structurally cannot, reach a claim's own evidence from the claim alone.
- ALL 17 real ``EvidenceAnchor`` objects have ``run_id: null``. The
  RunManifest is reachable ONLY via ``crate.run_id`` — never from any claim
  or anchor. A claim-rooted export of this real graph honestly does NOT
  include the run manifest, and never fabricates one.
- **Surprise, beyond what the finding record's own fact-lock anticipated:**
  the Hammond claim's own ``verification_ids`` field is ``[]`` — EMPTY —
  even though two real ``VerificationResult`` objects (a ``pass`` and a
  ``fail``: the disagreement this whole packet exists to surface) target it
  by ``target_id``. Resolving R1's declared ``Claim.verification_ids`` field
  alone would find NEITHER of them. What DOES find them, unmodified, is
  :meth:`_discover_verifications_targeting` below (R2d) — already shared
  by both roots via :meth:`_resolve_provenance_and_verifications`. R1's
  declared-field map still lists ``verification_ids`` (a real, schema-
  declared field, resolved for completeness and for any future claim that
  DOES populate it — a referenced id that fails to resolve is still a fail-
  fast refusal, R1's own contract) — but in the real data today, it is the
  event-log-scan step, not this field, doing the actual verification-
  discovery work. Documented here precisely because the packet's own
  derivation prose could be read as "the declared field suffices" —fact-
  locking this catches a real gap that would have caused a silent
  under-export.

**R1's resolver** (:meth:`_resolve_declared_reference_fields`): a FIXED,
documented per-kind map of declared reference fields
(:data:`_DECLARED_REFERENCE_FIELDS`), resolved via ``ObjectRepository
.get_latest`` (fail-fast — a referenced urn that does not resolve raises
``ObjectNotFoundError`` naming it, mirroring step 2's identical stance for
the crate's own ``_CRATE_URN_ARRAY_FIELDS``), applied transitively to
FIXPOINT: a newly-discovered ``EvidenceAnchor``/``VerificationResult`` has
its OWN declared fields resolved on the next pass too, until no pass
discovers a new object. This is explicitly NOT a re-seed of
``ProjectionService.build_provenance_map`` (the fact-lock above proves that
BFS cannot reach a claim's evidence at all) — it is the SAME "resolve
declared reference-field arrays, get_latest each" operation step 2 above
already performs for the crate's own three URN arrays, extended to the
claim's and anchor's own reference fields, added HERE (never in
``mrr.services.projection.service``, which stays reused-verbatim per this
packet's own ``forbidden_changes``).

:meth:`resolve_closure_from_claims` composes, in order: (1) resolve the root
claim ids (``claim_ids`` given -> each validated to be a ``Claim``, fail-fast
otherwise; ``None`` -> every claim ``ProjectionService.build_claim_table``
enumerates, refusing via :class:`NoClaimsToExportError` if that is empty —
task-packets/E8-T06.yaml invariant, "never ships an empty bundle"); (2) seed
``object_bodies`` with those root claims; (3) run R1's fixpoint resolver;
(4) call the SAME :meth:`_resolve_provenance_and_verifications` step
:meth:`resolve_closure` calls (R2c's provenance BFS per root claim, PLUS
R2d's verification discovery over every claim now in the closure) — one
shared private core, not a fork. The resulting :class:`ExportClosure` carries
``crate_id=None`` and an EMPTY ``artifact_refs`` (derived_decisions (a): a
claim-rooted export ships no artifact bytes and needs no crate — the
research OUTPUT is the claim graph; the artifacts are the run's INPUTS).

``resolve_closure``'s own steps 3-4 (the provenance BFS loop plus R2d
verification discovery) are extracted, UNCHANGED, into the private
:meth:`_resolve_provenance_and_verifications` — this extraction, plus
threading ``closure.crate_id`` (rather than a re-read local variable)
through the new shared :meth:`_write_closure`, is the ENTIRE "shared private
core" refactor this packet performs on the crate-rooted path; no line of the
actual R2 closure algorithm changed, which is the behavior-identity proof
task-packets/E8-T01.yaml's/E8-T02.yaml's own test suites passing UNMODIFIED
against this refactor demonstrates (task-packets/E8-T06.yaml stop_condition
1). ``ExportClosure.crate_id`` becomes ``Urn | None`` — every existing
reader was checked at derivation (grepped across services/packages/tests):
neither :meth:`export` nor ``mrr.services.report.service.ReportService
.render`` nor any test ever read ``ExportClosure.crate_id`` directly (both
already carry their OWN ``crate_id`` parameter), so this widening is
source-compatible everywhere; :meth:`_write_closure` is the one new reader.

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
from typing import Literal, Protocol

from mrr.contracts import Urn
from mrr.crypto.canonical import JSONValue, canonicalize
from mrr.domain.artifacts import ArtifactStore
from mrr.domain.exceptions import ArtifactNotFoundError, DomainError, ObjectNotFoundError
from mrr.domain.projection import ProvenanceEdge
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

#: The one other kind task-packets/E8-T06.yaml's R1 declared-reference-field
#: map names — see :data:`_DECLARED_REFERENCE_FIELDS`.
_EVIDENCE_ANCHOR_KIND = "EvidenceAnchor"

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

#: task-packets/E8-T06.yaml R1: the FIXED, fact-locked (verified against the
#: real K1-T04 schema — see the module docstring's own "E8-T06" section)
#: per-kind declared-reference-field map :meth:`ExportService
#: ._resolve_declared_reference_fields` resolves to fixpoint. Every field
#: named here is a real, schema-declared reference field (schemas/claim
#: .schema.json, schemas/evidence-anchor.schema.json, schemas/verification-
#: result.schema.json) — never a guessed vocabulary (AGENTS.md rule 3).
#: ``SourceFamily`` is deliberately NOT a key: claim.source_family_ids is
#: resolved (a SourceFamily urn is added to the closure), but a SourceFamily
#: object's own ``member_source_ids`` is a leaf for this resolver — not part
#: of the fact-locked map, so not traversed further (a future packet that
#: needs the member SourceRecords too is a one-line addition here, per
#: derived_decisions (b), never an implicit widening).
_DECLARED_REFERENCE_FIELDS: Mapping[str, tuple[str, ...]] = {
    _CLAIM_KIND: (
        "evidence_relations",
        "counterevidence_relations",
        "verification_ids",
        "source_family_ids",
    ),
    _EVIDENCE_ANCHOR_KIND: ("source_record_id", "run_id"),
    _VERIFICATION_RESULT_KIND: ("evidence_inspected",),
}


def _reference_field_urns(value: JSONValue | None) -> list[str]:
    """Every urn a single R1 declared-reference-field VALUE names — either a
    plain, nullable urn string (``EvidenceAnchor.source_record_id``/
    ``run_id``) or an array of urn strings (every ``Claim``/
    ``VerificationResult`` field in :data:`_DECLARED_REFERENCE_FIELDS`).
    ``None``, an absent field, or an empty string yields no urns — R1's own
    "each [anchor field] only when the field is non-empty", applied
    uniformly to every field this resolver ever reads (an empty array
    already yields no urns without special-casing).
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, Sequence):
        return [str(item) for item in value if item]
    return []


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


class NoClaimsToExportError(DomainError):
    """Raised by ``ExportService.resolve_closure_from_claims``/``.export_from
    _claims`` (task-packets/E8-T06.yaml invariant: "a zero-claim claim-rooted
    export refuses, never ships an empty bundle") when the resolved root
    claim set is empty — either ``claim_ids=None`` (``--all-claims``) over a
    schema ``ProjectionService.build_claim_table`` enumerates zero claims
    for, or an explicitly-supplied, empty ``claim_ids`` sequence. Never
    raised for a NON-empty explicit ``claim_ids`` list whose entries fail to
    resolve — that is ``ObjectNotFoundError``/``ValueError`` instead (each
    urn's own typed refusal, R1), naming the offending urn rather than this
    generic, message-only "there was nothing to export at all" case.
    """

    def __init__(self) -> None:
        super().__init__("no claims to export")


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
class ExportClosure:
    """The exact object-bodies mapping, provenance edges, and artifact refs
    ``ExportService.export`` computes en route to writing an RO-Crate export
    — extracted by task-packets/E8-T03.yaml R2 into
    :meth:`ExportService.resolve_closure`, a public, READ-ONLY method, so
    ``mrr.services.report.service.ReportService`` can build a research-report
    projection over the EXACT SAME closure the export ships, without
    duplicating one line of the R2 closure algorithm (task-packets/
    E8-T03.yaml reviewer_resolution (2): "one definition of 'what belongs to
    this run's record', not a second competing one" — derived_decisions (a):
    "divergence between report content and export content is a bug by
    definition").

    Deliberately excludes artifact BYTES: ``export``'s own R3 step (fetching
    every content hash from the ``ArtifactStore``, aggregating every miss)
    is NOT performed by :meth:`resolve_closure` — see that method's own
    docstring. ``artifact_refs`` here is the crate's own declared
    ``ArtifactRef`` array (``artifact_id``/``content_hash``/
    ``classification`` — metadata a caller can read without ever touching
    the byte store), not the fetched payloads themselves; a caller that
    needs the bytes (only ``export`` does) fetches them separately via
    :meth:`ExportService.export`` or its own ``ArtifactStore``.

    EXTENDED by task-packets/E8-T06.yaml R1: ``crate_id`` becomes ``Urn |
    None`` — ``None`` for a claim-rooted closure (:meth:`ExportService
    .resolve_closure_from_claims`), meaning "no crate anchors this export"
    (derived_decisions (a)), never a placeholder or invented urn. See the
    module docstring's "E8-T06" section for why every existing reader of
    this field is unaffected by the widening.
    """

    crate_id: Urn | None
    object_bodies: Mapping[str, Mapping[str, JSONValue]]
    provenance_edges: tuple[ProvenanceEdge, ...]
    artifact_refs: tuple[Mapping[str, JSONValue], ...]


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

    EXTENDED by task-packets/E8-T06.yaml R2 ("the exit-0 JSON line reports
    the root kind"): ``root`` is ``"crate"`` for :meth:`ExportService.export`
    (``crate_id`` always populated, ``claim_ids`` always empty) or
    ``"claims"`` for :meth:`ExportService.export_from_claims` (``crate_id``
    always ``None``, ``claim_ids`` the sorted, exported claim urns). Purely
    additive: every pre-E8-T06 field keeps its exact crate-rooted meaning,
    and ``mrr.services.cli.export_main``'s own crate-rooted JSON payload
    keeps every pre-existing key with its pre-existing value — only new keys
    are added (checked at derivation: no E8-T01..T05 test asserts
    ``set(result_line.keys()) == ...``, only individual key lookups).
    """

    root: Literal["crate", "claims"]
    crate_id: Urn | None
    claim_ids: tuple[Urn, ...]
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

    def resolve_closure(self, crate_id: Urn) -> ExportClosure:
        """R2 alone: resolve ``crate_id``'s full export closure — the
        object-bodies mapping, the deduplicated/sorted provenance edges, and
        the crate's own artifact refs — WITHOUT fetching any artifact BYTES
        and WITHOUT writing anything to disk (task-packets/E8-T03.yaml R2's
        own extraction requirement: "returning the exact object-bodies
        mapping, provenance edges, and artifact refs export() computes
        today"). See the module docstring's "The R2 closure, built in four
        steps" section for the full algorithm this method performs (steps
        1-3 plus the R2d verification discovery; R3's artifact-byte fetch is
        deliberately NOT step 4 of this method — :meth:`export` performs it
        separately, immediately after calling this method, so a caller that
        only needs the closure's STRUCTURAL shape (``mrr.services.report
        .service.ReportService``, which builds a narrative projection that
        never quotes an artifact's raw bytes) never touches the
        ``ArtifactStore`` at all).

        This is a pure extraction: every line below is copied unchanged from
        ``export``'s own prior body (steps 1-3 plus verification discovery),
        proven behavior-identical by task-packets/E8-T01.yaml/E8-T02.yaml's
        own test suites passing unmodified after this refactor (task-packets/
        E8-T03.yaml stop_condition 2; task-packets/E8-T06.yaml stop_condition
        1 re-proves the identical guarantee against the E8-T06 refactor
        below, which extracts steps 3-4 into :meth:`_resolve_provenance_and
        _verifications` — see that method's own docstring).

        Raises:
            ValueError: ``crate_id`` resolves to a stored object whose
                ``kind`` is not ``EvidenceCrate``.
            mrr.domain.exceptions.ObjectNotFoundError: ``crate_id``, or any
                urn the crate's own ``source_records``/``evidence_anchors``/
                ``proposed_claims`` arrays name, does not resolve to any
                stored object — carries the exact missing urn.
        """
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

        object_bodies, sorted_provenance_edges = self._resolve_provenance_and_verifications(
            object_bodies, crate.body.get("proposed_claims", [])
        )

        return ExportClosure(
            crate_id=crate_id,
            object_bodies=object_bodies,
            provenance_edges=sorted_provenance_edges,
            artifact_refs=tuple(crate.body.get("artifacts", [])),
        )

    def resolve_closure_from_claims(self, claim_ids: Sequence[Urn] | None = None) -> ExportClosure:
        """R1 (task-packets/E8-T06.yaml): the claim-graph-rooted counterpart
        to :meth:`resolve_closure` — see the module docstring's own "E8-T06"
        section for the full fact-locked design rationale (the declared-
        reference-field map, the fixpoint algorithm, and why this is NOT a
        re-seed of ``ProjectionService.build_provenance_map``).

        Args:
            claim_ids: explicit claim urns (``--claim-id``, repeatable at
                the CLI). Each MUST resolve to a stored object of kind
                ``Claim`` — a typed refusal names the offending urn
                otherwise (``ObjectNotFoundError`` if it resolves to
                nothing, ``ValueError`` if it resolves to a non-``Claim``
                kind — mirrors :meth:`resolve_closure`'s own identical
                crate-kind check). ``None`` (``--all-claims``, the default)
                enumerates EVERY claim ``ProjectionService.build_claim_table``
                discovers in the schema (derived_decisions (b): "each
                archival schema is exactly one run's world").

        Raises:
            mrr.domain.exceptions.ObjectNotFoundError: an explicit
                ``claim_ids`` entry, or any urn the R1 declared-reference-
                field resolver subsequently discovers, does not resolve to
                any stored object — carries the exact missing urn.
            ValueError: an explicit ``claim_ids`` entry resolves to a
                stored object whose ``kind`` is not ``Claim``.
            NoClaimsToExportError: the resolved root claim set is empty —
                task-packets/E8-T06.yaml invariant, "never ships an empty
                bundle".
        """
        root_claim_ids = self._resolve_root_claim_ids(claim_ids)

        object_bodies: dict[str, Mapping[str, JSONValue]] = {
            claim_id: self._object_repository.get_latest(claim_id).body
            for claim_id in root_claim_ids
        }
        self._resolve_declared_reference_fields(object_bodies)

        object_bodies, sorted_provenance_edges = self._resolve_provenance_and_verifications(
            object_bodies, root_claim_ids
        )

        return ExportClosure(
            crate_id=None,
            object_bodies=object_bodies,
            provenance_edges=sorted_provenance_edges,
            artifact_refs=(),
        )

    def export(self, crate_id: Urn, output_dir: Path) -> ExportResult:
        """Export the RO-Crate closure (R2, via :meth:`resolve_closure`) for
        ``crate_id`` into ``output_dir``, atomically (R3). See the module
        docstring for the full closure algorithm and the atomic-write
        discipline.

        Raises:
            ValueError: ``output_dir`` already exists (file, or non-empty
                directory) — checked first, before any read; or
                ``crate_id`` resolves to a stored object whose ``kind`` is
                not ``EvidenceCrate`` (see :meth:`resolve_closure`).
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

        closure = self.resolve_closure(crate_id)
        plan, total_bytes = self._write_closure(closure, output_dir)

        return ExportResult(
            root="crate",
            crate_id=crate_id,
            claim_ids=(),
            output_dir=output_dir,
            object_count=len(plan.objects),
            artifact_count=len(plan.artifacts),
            total_bytes=total_bytes,
        )

    def export_from_claims(self, claim_ids: Sequence[Urn] | None, output_dir: Path) -> ExportResult:
        """Export the claim-graph-rooted closure (R1, via :meth:`resolve_
        closure_from_claims`) into ``output_dir``, atomically (R3, shared
        with :meth:`export` via :meth:`_write_closure`) — task-packets/
        E8-T06.yaml's claim-rooted counterpart to :meth:`export`. Fetches NO
        artifact bytes (``closure.artifact_refs`` is always empty for a
        claim-rooted closure — derived_decisions (a)) and touches no
        ``ArtifactStore`` beyond the harmless, always-empty
        :meth:`_fetch_artifact_bytes` call :meth:`_write_closure` already
        shares with the crate-rooted path.

        Raises:
            ValueError: ``output_dir`` already exists (file, or non-empty
                directory) — checked first, before any read; or an explicit
                ``claim_ids`` entry resolves to a non-``Claim`` kind (see
                :meth:`resolve_closure_from_claims`).
            mrr.domain.exceptions.ObjectNotFoundError: an explicit
                ``claim_ids`` entry, or any urn R1's resolver subsequently
                discovers, does not resolve to any stored object.
            NoClaimsToExportError: the resolved root claim set is empty.
        """
        if output_path_conflict(output_dir):
            raise ValueError(
                f"--output-dir {output_dir} already exists (as a file, or as a non-empty "
                "directory) — refusing to write over or into it"
            )

        closure = self.resolve_closure_from_claims(claim_ids)
        plan, total_bytes = self._write_closure(closure, output_dir)

        exported_claim_ids = tuple(
            sorted(
                urn
                for urn, body in closure.object_bodies.items()
                if body.get("kind") == _CLAIM_KIND
            )
        )
        return ExportResult(
            root="claims",
            crate_id=None,
            claim_ids=exported_claim_ids,
            output_dir=output_dir,
            object_count=len(plan.objects),
            artifact_count=len(plan.artifacts),
            total_bytes=total_bytes,
        )

    # ------------------------------------------------------------------
    # Internal helpers.
    # ------------------------------------------------------------------

    def _resolve_provenance_and_verifications(
        self,
        object_bodies: dict[str, Mapping[str, JSONValue]],
        root_claim_ids: Sequence[str],
    ) -> tuple[dict[str, Mapping[str, JSONValue]], tuple[ProvenanceEdge, ...]]:
        """R2c (the per-root-claim provenance BFS) plus R2d (verification
        discovery over every claim now in the closure) — extracted VERBATIM
        from :meth:`resolve_closure`'s own prior inline body (task-packets/
        E8-T06.yaml: "the shared private core"), now called by BOTH
        :meth:`resolve_closure` (seeded from the crate's own
        ``proposed_claims``) and :meth:`resolve_closure_from_claims` (seeded
        from the caller's/``--all-claims``'s root claim ids). No line of the
        original algorithm changed — mutates and returns ``object_bodies``
        in place (matching the pre-extraction code's own mutation style) plus
        the deduplicated, sorted provenance-edge tuple.

        R2d's own verification discovery matters MORE for the claim-rooted
        path than the module docstring's "E8-T06" section might suggest at
        first read: the real K1-T04 Hammond claim's own ``verification_ids``
        field is empty even though two real ``VerificationResult`` objects
        target it (fact-locked directly against the real schema — see that
        section). This event-log-scan step, unchanged from E8-T01, is what
        actually finds them; R1's declared-field resolver
        (:meth:`_resolve_declared_reference_fields`) also resolves
        ``Claim.verification_ids`` for completeness/fail-fast honesty, but
        does not, by itself, discover these two in the real data.
        """
        projection_service = ProjectionService(
            self._object_repository, self._edge_repository, self._event_log
        )
        provenance_edges: set[ProvenanceEdge] = set()
        for claim_id in sorted(root_claim_ids):
            provenance = projection_service.build_provenance_map(claim_id)
            provenance_edges.update(provenance.edges)
            for edge in provenance.edges:
                if edge.target_id not in object_bodies:
                    object_bodies[edge.target_id] = self._object_repository.get_latest(
                        edge.target_id
                    ).body

        claim_ids_in_closure = {
            urn for urn, body in object_bodies.items() if body.get("kind") == _CLAIM_KIND
        }
        object_bodies.update(self._discover_verifications_targeting(claim_ids_in_closure))

        sorted_provenance_edges = tuple(
            sorted(
                provenance_edges,
                key=lambda edge: (edge.source_id, edge.relation, edge.target_id, edge.via),
            )
        )
        return object_bodies, sorted_provenance_edges

    def _resolve_root_claim_ids(self, claim_ids: Sequence[Urn] | None) -> tuple[Urn, ...]:
        """Resolve :meth:`resolve_closure_from_claims`'s own ``claim_ids``
        argument to the exact, sorted, deduplicated root claim urn set — see
        that method's own docstring for the two branches' full rationale.
        Raises :class:`NoClaimsToExportError` whenever the RESULT is empty,
        regardless of which branch produced it (an explicit-but-empty
        ``claim_ids`` sequence refuses identically to an empty
        ``--all-claims`` enumeration — both are "a zero-claim claim-rooted
        export", task-packets/E8-T06.yaml's own invariant, phrased as an
        outcome, not as one specific code path).
        """
        if claim_ids is None:
            projection_service = ProjectionService(
                self._object_repository, self._edge_repository, self._event_log
            )
            resolved: tuple[Urn, ...] = tuple(
                sorted(row.claim_id for row in projection_service.build_claim_table())
            )
        else:
            validated: list[Urn] = []
            for claim_id in claim_ids:
                obj = self._object_repository.get_latest(claim_id)
                if obj.kind != _CLAIM_KIND:
                    raise ValueError(
                        f"--claim-id {claim_id!r} resolves to a stored object of kind "
                        f"{obj.kind!r}, not {_CLAIM_KIND!r}"
                    )
                validated.append(claim_id)
            resolved = tuple(sorted(set(validated)))

        if not resolved:
            raise NoClaimsToExportError()
        return resolved

    def _resolve_declared_reference_fields(
        self, object_bodies: dict[str, Mapping[str, JSONValue]]
    ) -> None:
        """R1's own declared-reference-field resolver — see the module
        docstring's "E8-T06" section for the full design rationale. Mutates
        ``object_bodies`` IN PLACE to its fixpoint: every object already
        present whose ``kind`` is a key of :data:`_DECLARED_REFERENCE_FIELDS`
        has every urn its own declared fields name resolved via
        ``ObjectRepository.get_latest`` (FAIL-FAST — a referenced urn that
        does not resolve raises ``ObjectNotFoundError`` naming it, R1: "a
        referenced urn that does not resolve is a typed refusal naming it");
        a newly-discovered object is itself expanded on the next pass, so a
        ``VerificationResult`` reached via ``Claim.verification_ids`` has its
        own ``evidence_inspected`` anchors pulled in too, and so on, until no
        pass discovers anything new. Terminates on any finite graph: every
        object is expanded at most once (tracked by the ``expanded`` set
        below), and ``object_bodies`` itself already prevents re-fetching a
        urn seen on an earlier pass.
        """
        frontier: list[str] = list(object_bodies)
        expanded: set[str] = set()
        while frontier:
            next_frontier: list[str] = []
            for object_id in frontier:
                if object_id in expanded:
                    continue
                expanded.add(object_id)
                body = object_bodies[object_id]
                fields = _DECLARED_REFERENCE_FIELDS.get(str(body.get("kind")))
                if fields is None:
                    continue
                for field_name in fields:
                    for urn in _reference_field_urns(body.get(field_name)):
                        if urn not in object_bodies:
                            object_bodies[urn] = self._object_repository.get_latest(urn).body
                            next_frontier.append(urn)
            frontier = next_frontier

    def _write_closure(self, closure: ExportClosure, output_dir: Path) -> tuple[ExportPlan, int]:
        """R3's actual write, shared by :meth:`export` (crate-rooted) and
        :meth:`export_from_claims` (claim-rooted, task-packets/E8-T06.yaml):
        fetch every artifact byte ``closure.artifact_refs`` names (empty,
        hence a no-op, for a claim-rooted closure), build the export plan +
        metadata (``mrr.domain.ro_crate.build_export``, threading ``closure
        .crate_id`` into that function's own now-Optional ``crate_urn``
        parameter — R3), and write the tree atomically (see the module
        docstring's "atomic, all-or-nothing directory write" section).
        """
        artifact_bytes, missing_content_hashes = self._fetch_artifact_bytes(closure.artifact_refs)
        if missing_content_hashes:
            raise MissingArtifactBytesError(missing_content_hashes)

        artifact_sizes = {content_hash: len(data) for content_hash, data in artifact_bytes.items()}
        plan, metadata = build_export(
            crate_urn=closure.crate_id,
            object_bodies=closure.object_bodies,
            artifact_sizes=artifact_sizes,
            provenance_edges=closure.provenance_edges,
        )

        total_bytes = self._write_export(output_dir, plan, metadata, artifact_bytes)
        return plan, total_bytes

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
