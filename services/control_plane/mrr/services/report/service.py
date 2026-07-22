"""``ReportService`` (task-packets/E8-T03.yaml, MRR-FR-100/-101/-104,
MRR-FR-095): the application-layer, READ-ONLY service that composes
``mrr.services.export.service.ExportService`` (for the R2 export closure,
via its new ``resolve_closure`` method — task-packets/E8-T03.yaml R2) and
``mrr.services.projection.service.ProjectionService`` (for unresolved
critical corrections and per-claim provenance) into one
``mrr.domain.research_report.ResearchReport`` model, ready for that module's
own ``render_markdown``/``render_html``. Third task of Epic E8; the closest
templates are ``mrr.services.export.service.ExportService`` itself (the same
``ObjectRepository``/``EdgeRepository``/narrow event-journal Protocol
dependency triple, the same "writes NOTHING" discipline, and the same
"internally composes ``ProjectionService`` rather than re-reading the graph
a second, divergent way" pattern that module's own docstring already
documents for its OWN provenance BFS) and ``mrr.domain.research_report``
itself (read that module's docstring first — it owns every actual
redaction/shaping decision; this service only performs I/O and hands
already-loaded values to ``build_report``).

--- This service writes NOTHING ----------------------------------------------

Every method here only calls ``ExportService.resolve_closure`` (itself
``ObjectRepository.get_latest``/``EdgeRepository.edges_from`` reads) and
``ProjectionService.build_public_correction_view``/``.build_provenance_map``
(the same reads) — never ``insert_revision``, ``add_edge``, ``append``, or
``ArtifactStore.put``. There is no ``RecordRevisionWithEvent`` dependency
anywhere on this class (task-packets/E8-T03.yaml R2: "no
RecordRevisionWithEvent anywhere"), matching ``ExportService``'s own
identical stance.

--- Composing ExportService without an ``ArtifactStore`` --------------------

``ExportService.resolve_closure`` never reads ``self._artifact_store``
(only ``export``'s own separate artifact-byte-fetch step does — see that
module's docstring) — but ``ExportService.__init__`` still REQUIRES one
positionally, since task-packets/E8-T03.yaml scopes that file's own allowed
change to "R2 extraction only — behavior-identical", ruling out widening its
constructor to make the parameter optional. ``mrr report render`` has no
``--artifact-root`` flag at all (task-packets/E8-T03.yaml R4's own flag
list) — the report never touches artifact bytes (see the packet's own
governing instructions) — so this module constructs the internal
``ExportService`` with :class:`_NeverInvokedArtifactStore`, a minimal stand-in
whose every method raises ``AssertionError`` if ever actually called. This is
provably safe: :meth:`ReportService.render` calls ONLY ``resolve_closure``,
never ``export``, so ``self._artifact_store`` is never read. Constructing a
REAL ``LocalFilesystemArtifactStore`` instead was considered and rejected —
its own constructor eagerly creates the given root directory on disk
(``self._root.mkdir(parents=True, exist_ok=True)``), which would be a
surprising, undocumented filesystem side effect for a command that accepts
no artifact-root flag at all.

--- Corrections: crate-scoped, over the reused fail-closed projection --------

``ProjectionService.build_public_correction_view`` (task-packets/E6-T05.yaml)
is SYSTEM-WIDE — every unresolved critical correction ever recorded, not
scoped to any one crate (that module's own docstring: "one row per
correction ... the SAME population" as the equally system-wide claim table).
This service therefore calls it once, then filters the result to exactly the
rows whose own ``affected_object_ids``/``impact_object_ids`` name an object
actually present in THIS crate's own closure — crate-rootedness (task-
packets/E8-T03.yaml reviewer_resolution (2)) layered on top of an unscoped
service, by a plain filter, never by widening ``ProjectionService`` itself
(out of this task's ``allowed_paths``). See :func:`_corrections_for_closure`.

For ``disclosure == "internal"``, this method calls the SAME
``build_public_correction_view`` with ``mrr.domain.research_report
.ALWAYS_PUBLIC_ATTESTATION`` (never a caller-supplied map — task-packets/
E8-T03.yaml R4 forbids one for internal disclosure at the CLI) rather than a
second, separately-written "give me everything" method — see that
attestation class's own docstring for why this is "reuse the function
verbatim, drive it through its non-redacting branch", not a fork.

--- Every correction this composition CAN discover is already unresolved-critical ---

``ProjectionService`` offers no OTHER public correction-listing method
within this packet's own ``allowed_paths`` (only ``build_claim_table``'s
per-row ``unresolved_correction_ids`` and ``build_public_correction_view``
itself, both scoped to MRR-FR-095's "unresolved critical" population) — so
"every discovered correction" (task-packets/E8-T03.yaml R1(5)) is, in this
implementation, exactly the unresolved-critical set this composition can
reach; a resolved or non-critical correction is outside what this
composition discovers at all. Flagged here, and in the packet report, as a
disclosed scope boundary rather than a silent gap — see ``mrr.domain
.research_report``'s own module docstring for the identical note at the
model-building layer.

--- Provenance summary: a second, independent call to ``build_provenance_map`` ---

``ExportClosure.provenance_edges`` (from ``resolve_closure``) is the
DEDUPLICATED UNION of every proposed claim's own transitive provenance —
flattened, so it cannot be regrouped back into "this claim's own edges"
once a multi-hop edge's ``source_id`` is an intermediate node rather than the
claim itself. R1(8) ("per claim, ... its provenance edges") therefore needs
one FRESH call to ``ProjectionService.build_provenance_map(claim_id)`` per
proposed claim — the exact same already-tested method ``ExportService
.resolve_closure`` itself calls internally, called here a second time for a
different purpose (grouping by claim rather than flattening into a union) —
never a forked traversal.

--- E8-T05: the OPTIONAL ``release_id`` parameter (R3) -----------------------

``render`` gains one new, OPTIONAL keyword parameter, ``release_id: Urn |
None = None`` (task-packets/E8-T05.yaml R3: "ReportService gains the
optional release context parameter and computes the banner via R2"). When
``None`` (every existing call site), NOTHING changes — ``build_report`` is
called exactly as before, with ``release_banner`` simply omitted (defaults
to ``None`` there too), producing byte-identical output to E8-T03 (R3's own
regression requirement). When given, this method resolves the R2 banner via
``mrr.services.release.service.ReleaseService.status`` (constructed with a
never-invoked ``RecordRevisionWithEvent`` stand-in — mirroring THIS module's
own ``_NeverInvokedArtifactStore`` precedent immediately below, one section
over, for the identical "read-only call path, but the constructor still
needs the write dependency" situation; imported directly from
``mrr.services.release.service`` rather than composed through
``mrr.services.release.supersede.resolve_release_status``, which would
otherwise close a genuine import cycle — that module imports
``mrr.services.release.bundle``, which imports THIS module, for
``assemble_and_release``'s own ``ReportService`` composition), then resolves
the RAW ``CorrectionEvent`` body for every ``correction_id`` the returned
banner's own ``affecting_corrections`` names (a plain ``ObjectRepository
.get_latest`` per id — this service already holds one), and hands both into
``mrr.domain.research_report.ReleaseBannerInput`` for ``build_report`` to
redact and shape (see that module's own "E8-T05: the OPTIONAL release-status
banner" section for why the RAW bodies, not pre-redacted text, cross this
boundary — this service performs I/O only, every actual redaction decision
stays exactly where it already lives, ``mrr.domain.research_report``'s own).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from mrr.contracts import Urn
from mrr.crypto.canonical import JSONValue
from mrr.domain.artifacts import ArtifactDescriptor, Classification
from mrr.domain.exceptions import ObjectNotFoundError
from mrr.domain.projection import ProvenanceEdge
from mrr.domain.public_correction_view import PublicCorrectionRow
from mrr.domain.repositories import EdgeRepository, ObjectRepository, StoredObject
from mrr.domain.research_report import (
    ALWAYS_PUBLIC_ATTESTATION,
    Disclosure,
    ReleaseBannerInput,
    ResearchReport,
    build_report,
)
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.export.service import ExportClosure, ExportService
from mrr.services.projection.service import ProjectionService
from mrr.services.release.service import ReleaseService


class _EventJournal(Protocol):
    """The one read operation this service needs from an event log —
    identical to ``mrr.services.export.service._EventJournal``/``mrr
    .services.projection.service._EventJournal`` (not imported from either:
    both are private to their own module — see either module's own docstring
    for why this codebase declares this Protocol independently per consuming
    module rather than sharing one).
    """

    def read_all(self) -> list[AppendedEvent]: ...


class _NeverInvokedArtifactStore:
    """A stand-in for ``mrr.domain.artifacts.ArtifactStore`` that raises on
    every call — used only to satisfy ``ExportService.__init__``'s required
    parameter when composing it for :meth:`resolve_closure` alone, which
    never reads it. See the module docstring's "Composing ExportService
    without an ArtifactStore" section for why this exists instead of a real
    adapter.
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
        raise AssertionError(
            "ReportService's internal ExportService never fetches artifact bytes "
            "(resolve_closure does not read the ArtifactStore) — this stand-in "
            "should never actually be invoked"
        )

    def get(self, content_hash: str) -> bytes:
        raise AssertionError(
            "ReportService's internal ExportService never fetches artifact bytes "
            "(resolve_closure does not read the ArtifactStore) — this stand-in "
            "should never actually be invoked"
        )

    def stat(self, content_hash: str) -> ArtifactDescriptor:
        raise AssertionError(
            "ReportService's internal ExportService never fetches artifact bytes "
            "(resolve_closure does not read the ArtifactStore) — this stand-in "
            "should never actually be invoked"
        )

    def exists(self, content_hash: str) -> bool:
        raise AssertionError(
            "ReportService's internal ExportService never fetches artifact bytes "
            "(resolve_closure does not read the ArtifactStore) — this stand-in "
            "should never actually be invoked"
        )


def _never_invoked_record(
    obj: StoredObject, expected_current_revision: int | None, event: DomainEvent
) -> tuple[StoredObject, AppendedEvent]:
    """A ``RecordRevisionWithEvent`` stand-in that always raises — E8-T05's
    own addition, used only to satisfy ``ReleaseService.__init__``'s
    required ``record`` parameter when composing it for
    :meth:`ReportService._resolve_release_banner_input` alone, which calls
    only ``ReleaseService.status`` (read-only — never writes). Mirrors
    ``_NeverInvokedArtifactStore`` immediately above: same rationale, same
    shape, for the same reason (a constructor requires a write dependency
    this particular call path never actually reaches).
    """
    raise AssertionError(
        "ReportService's internal ReleaseService never writes (status() is read-only) — "
        "this stand-in should never actually be invoked"
    )


def _corrections_for_closure(
    corrections: list[PublicCorrectionRow], closure: ExportClosure
) -> list[PublicCorrectionRow]:
    """Crate-scope ``corrections`` (``ProjectionService
    .build_public_correction_view``'s system-wide result) down to the rows
    whose own ``affected_object_ids``/``impact_object_ids`` name at least one
    object actually present in ``closure.object_bodies`` — see the module
    docstring's "Corrections: crate-scoped" section. Sorted by
    ``correction_id`` for a deterministic result independent of the
    caller's own row order.
    """
    closure_ids = set(closure.object_bodies)
    scoped = [
        row
        for row in corrections
        if closure_ids.intersection((*row.affected_object_ids, *row.impact_object_ids))
    ]
    return sorted(scoped, key=lambda row: row.correction_id)


class ReportService:
    """docs/spec/01_SYSTEM_SPEC.md Stage 11/section 7.9 ("Projection
    Service"), implemented per task-packets/E8-T03.yaml. See the module
    docstring for the full design rationale — above all, that this class
    writes no object/revision/event/edge/artifact anywhere; it is a
    pure-read-and-compose layer over ``ExportService``/``ProjectionService``,
    handing the results to ``mrr.domain.research_report.build_report`` for
    every actual shaping/redaction decision.
    """

    def __init__(
        self,
        object_repository: ObjectRepository,
        edge_repository: EdgeRepository,
        event_log: _EventJournal,
    ) -> None:
        self._object_repository = object_repository
        self._edge_repository = edge_repository
        self._event_log = event_log
        self._export_service = ExportService(
            object_repository, edge_repository, event_log, _NeverInvokedArtifactStore()
        )
        self._projection_service = ProjectionService(object_repository, edge_repository, event_log)
        self._release_service = ReleaseService(
            object_repository, _never_invoked_record, event_log=event_log
        )

    def render(
        self,
        crate_id: Urn,
        *,
        disclosure: Disclosure,
        classification_by_object_id: Mapping[str, Classification] | None = None,
        release_id: Urn | None = None,
    ) -> ResearchReport:
        """Build the full :class:`mrr.domain.research_report.ResearchReport`
        model for ``crate_id``. Pure composition — every actual shaping and
        redaction decision is ``mrr.domain.research_report.build_report``'s.

        Args:
            crate_id: the ``EvidenceCrate`` this report is rooted at — the
                SAME anchor ``ExportService.resolve_closure`` resolves
                against.
            disclosure: ``"internal"`` (full content) or ``"public"``
                (MRR-FR-095 plus the E6-T05 fail-closed redaction rule).
            classification_by_object_id: the caller-supplied attestation map
                (task-packets/E6-T05.yaml's bridge) — used only when
                ``disclosure == "public"``; ignored (never read) for
                ``"internal"``, mirroring ``mrr.domain.research_report
                .build_report``'s own identical stance. ``None`` is treated
                as an empty mapping (AT2: "an empty attestation mapping
                shows structure ... and not one byte of any stored
                assertion/finding/summary string").
            release_id: E8-T05 R3's own OPTIONAL release-context parameter —
                ``None`` (the default) renders byte-identical to E8-T03
                output (no "Release status" block at all). When given, the
                urn of an already-persisted ``ReleaseRecord`` whose banner
                (verdict/``superseded_by``/affecting corrections/anomaly —
                see the module docstring's own "E8-T05" section) is computed
                and rendered FIRST, before section 1. This release need not
                be rooted on ``crate_id`` at all — this method never checks
                — a caller asking for a banner from an unrelated release's
                own context is a caller error this method does not itself
                guard against, matching this class's own "pure composition,
                no invented cross-checks" stance elsewhere.

        Raises:
            ValueError: ``crate_id`` resolves to a stored object whose
                ``kind`` is not ``EvidenceCrate`` (from ``resolve_closure``).
            mrr.domain.exceptions.ObjectNotFoundError: ``crate_id``, or any
                urn the crate's own arrays name, does not resolve to any
                stored object — carries the exact missing urn; or
                ``release_id`` (when given) does not resolve to any stored
                object.
            mrr.services.release.errors.ReleaseRecordKindError: ``release_id``
                (when given) resolves to a stored object whose ``kind`` is
                not ``"ReleaseRecord"``.
        """
        closure = self._export_service.resolve_closure(crate_id)

        attestation = (
            ALWAYS_PUBLIC_ATTESTATION
            if disclosure == "internal"
            else (classification_by_object_id or {})
        )
        all_corrections = self._projection_service.build_public_correction_view(
            classification_by_object_id=attestation
        )
        corrections = _corrections_for_closure(all_corrections, closure)

        crate_body = closure.object_bodies[crate_id]
        proposed_claims_value = crate_body.get("proposed_claims", [])
        proposed_claim_urns = (
            proposed_claims_value if isinstance(proposed_claims_value, list) else []
        )
        proposed_claim_ids = sorted(str(u) for u in proposed_claim_urns)
        provenance_by_claim: dict[str, tuple[ProvenanceEdge, ...]] = {
            claim_id: self._projection_service.build_provenance_map(claim_id).edges
            for claim_id in proposed_claim_ids
        }

        release_banner = self._resolve_release_banner_input(release_id)

        return build_report(
            object_bodies=closure.object_bodies,
            crate_id=crate_id,
            corrections=corrections,
            provenance_by_claim=provenance_by_claim,
            disclosure=disclosure,
            classification_by_object_id=classification_by_object_id or {},
            release_banner=release_banner,
        )

    def _resolve_release_banner_input(self, release_id: Urn | None) -> ReleaseBannerInput | None:
        """``None`` straight through when ``release_id`` is absent — see
        the module docstring's "E8-T05: the OPTIONAL release_id parameter"
        section. Otherwise resolves the R2 banner and every raw
        ``CorrectionEvent`` body its own ``affecting_corrections`` names,
        for ``mrr.domain.research_report`` to redact and shape itself — this
        method performs I/O only; it never redacts anything (no
        ``classification_by_object_id`` is read here at all).
        """
        if release_id is None:
            return None

        banner = self._release_service.status(release_id)
        correction_bodies_by_id: dict[str, Mapping[str, JSONValue]] = {}
        for affecting in banner.affecting_corrections:
            resolved = self._get_object_body_or_none(affecting.correction_id)
            if resolved is not None:
                correction_bodies_by_id[affecting.correction_id] = resolved
        return ReleaseBannerInput(banner=banner, correction_bodies_by_id=correction_bodies_by_id)

    def _get_object_body_or_none(self, object_id: str) -> Mapping[str, JSONValue] | None:
        try:
            return self._object_repository.get_latest(object_id).body
        except ObjectNotFoundError:
            return None
