"""``ProjectionService`` (task-packets/E3-T07.yaml): the application-layer,
READ-ONLY service that builds docs/spec/01_SYSTEM_SPEC.md section 7.9's
"reports ... from a fixed graph revision" over the already-persisted claim/
evidence/correction graph — two builders, ``build_claim_table`` and
``build_provenance_map``. Seventh and final task of Epic E3 (claim,
evidence, correction kernel); the closest templates are
``mrr.services.correction.service.CorrectionImpactService`` (E3-T06, the
same ``ObjectRepository``/``EdgeRepository``/``_EventJournal`` dependency
triple and the same query-driven BFS shape for ``_gather_impact_edges``/
``_trace_provenance`` below) and ``mrr.domain.projection`` (this service's
own pure-domain dependency, which carries the full MRR-FR-095 "resolved"
derivation and the return-shape dataclasses — read that module's docstring
first).

--- This service writes NOTHING ----------------------------------------------

Every method here only calls ``ObjectRepository.get_latest``,
``EdgeRepository.edges_from``, and ``_EventJournal.read_all`` — never
``insert_revision``, ``add_edge``, or ``append``. There is no
``RecordRevisionWithEvent``/``RecordEdgeWithEvent`` dependency anywhere on
this class (unlike every other E3 service module), because there is nothing
for it to record: task-packets/E3-T07.yaml's own forbidden_changes names
"any NEW authoritative state" explicitly, and AGENTS.md's source-of-truth
discipline is unambiguous ("Narrative reports are projections and are never
the primary research record"). Both builders are pure reads reshaped through
``mrr.domain.projection``'s pure functions — calling either one twice against
an unchanged graph yields an identical result (task-packets/E3-T07.yaml's own
acceptance test), which is exactly what makes this a re-derivable projection
rather than a second, competing source of truth.

--- Claim/correction discovery: the event log, not a new "list all" method ---

Neither ``mrr.domain.repositories.ObjectRepository`` nor its PostgreSQL
implementation offers a "list every object of kind X" method — the only
lookups are by a specific ``id`` (``get_latest``/``get_revision``/
``list_revisions``). This task's own ``allowed_paths`` does not include
``packages/persistence/**`` (E1-T05 is reuse-as-is, exactly like every prior
E3 task), so adding one there is not available here either. ``build_claim_table``
therefore discovers every claim/correction id that has EVER existed by
scanning ``_EventJournal.read_all()`` for the two events every ``create()``/
``record()`` in this codebase already, unconditionally, appends at genesis —
``"claim.created"`` (``mrr.services.claim.service.ClaimService.create``) and
``"correction.recorded"`` (``mrr.services.correction.service.
CorrectionImpactService.record``) — collecting each event's own
``object_id``. This is a read of the append-only domain event log (itself
one of AGENTS.md's named sources of truth, "authoritative for audit
history"), not a new authoritative index: no event is written, mutated, or
interpreted beyond its ``event_type``/``object_id`` fields, and a claim or
correction that has since moved to any later status/revision is still
discovered (its genesis event never disappears), so "one row per claim"
means every claim ever created, exactly matching task-packets/E3-T07.yaml's
own phrasing ("one row per claim").

A caller that already knows the exact ids it wants a projection over (e.g. a
test, or a narrower "just these claims" report) is not served by a
parameterized alternative here — task-packets/E3-T07.yaml's own signature
sketch (``build_claim_table(...) -> list[ClaimTableRow]``) leaves the
parameter list open, and discovery-by-event-log was chosen as the more
complete, more literal reading of "one row per claim" (ALL claims) over a
narrower caller-supplied-subset design, which would silently make "the claim
table" mean "the claim table for whichever ids the caller happened to pass"
instead.

--- Provenance map: typed edges AND declared object-field references ---------

``build_provenance_map`` traces every OUTGOING typed edge reachable from a
claim, transitively (breadth-first, cycle-safe via a visited set — the same
termination shape ``mrr.domain.correction_impact.compute_impact`` uses for
the same reason), with NO ``edge_type`` filter: task-packets/E3-T07.yaml
gives no curated category mapping for "provenance" the way MRR-FR-091 gives
one for impact (``mrr.domain.correction_impact.FR091_IMPACT_CATEGORY_EDGE_TYPES``)
— inventing a narrower allowlist here would itself be inventing unstated
scope (AGENTS.md rule 3), so every edge type in
``mrr.domain.repositories.EDGE_VOCABULARY`` is eligible, and the map shows
whatever the claim is actually, structurally connected to.

This alone cannot reach a ``SourceRecord`` or a run: no service in this
codebase writes an edge from an ``EvidenceAnchor`` to the ``SourceRecord``/
``RunManifest`` it resolves against (confirmed by inspection of
``mrr.services.evidence.service``/``mrr.services.claim.service`` — an
anchor's ``source_record_id``/``run_id`` are plain declared object fields,
never graph edges; ``mrr.services.source_family.service.SourceFamilyService``
documents the identical "representation is additive, nothing here writes an
edge" stance for its own ``member_source_ids``). Since task-packets/
E3-T07.yaml's own text explicitly names "evidence anchors / source records /
runs" as what the provenance map traces to, tracing edges alone would make
two of those three destination kinds structurally unreachable regardless of
what is actually recorded. ``_field_reference_hops`` therefore additionally
follows an already-discovered ``EvidenceAnchor``'s own ``source_record_id``/
``run_id`` fields as extra hops (``ProvenanceEdge.via == "field"``,
``edge_id is None`` since there is no edge row to name — see
``mrr.domain.projection.ProvenanceEdge``'s own docstring), mirroring
``mrr.services.claim.service``'s own documented "Field-vs-edge consistency"
precedent that an object field and a graph edge are two independent,
independently-inspectable carriers of relationship information in this
codebase, not one one implicitly derived from the other. This is flagged
here for the same reviewer scrutiny that module gives its own field/edge
design choice: a stricter reading of "typed-edge paths" that excludes field
references entirely was considered and rejected, because it would silently
make "source records / runs" an unreachable, dead phrase in this task's own
approved design text.

Every hop — edge or field — is included ONLY if its target object actually
resolves via ``ObjectRepository.get_latest`` at build time (task-packets/
E3-T07.yaml invariant: "the provenance map only lists edges/objects that
actually exist"); a dangling edge or a field reference to an id nothing
ever stored is silently excluded, never represented with a placeholder.

--- MRR-MTH-004 ceiling-gate extension (task-packets/K1-T02.yaml) -----------

``build_claim_table`` additionally resolves, for each discovered claim, its
own ``ruled_by`` edge(s) via the ALREADY-HELD ``self._edge_repository``, and
the ``MethodRuling`` -> ``MethodProtocol`` -> ``MethodProfile`` chain behind
the MOST RECENT one via the ALREADY-HELD ``self._object_repository`` (no new
constructor dependency) — then passes the resolved ``ruled_ceiling``/
``profile_max_ceiling`` pair into ``mrr.domain.projection.
build_claim_table_row``, which reuses ``mrr.domain.claim_ceiling.
ceiling_violation_reason`` directly (this service never re-implements the
gate logic). A claim with no ``ruled_by`` edge at all — every claim that
existed before this task, and any claim never ruled under a profile — omits
both keyword arguments entirely, so ``build_claim_table_row`` reports
``ceiling_checked=False``, byte-identical to pre-K1-T02 behavior.

A claim may carry MULTIPLE ``ruled_by`` edges over time (task-packets/
K1-T02.yaml specification_gaps: neither prevented nor deduplicated
anywhere). Unlike ``ClaimService._transition``'s own fail-closed "any
attached ruling reports a violation" re-check (a WRITE-time gate, where
rejecting is the safe default), this READ-only projection resolves only the
MOST RECENT ``ruled_by`` edge (``edges_from`` returns oldest-first, so the
last element) to decide what to DISPLAY — a deliberately simpler, disclosed
choice for "what is this claim's ceiling status right now," distinct from
"should a write be rejected." Flagged for reviewer scrutiny alongside
task-packets/K1-T02.yaml's own identical multi-ruling disclosure.
"""

from __future__ import annotations

from typing import Protocol

from mrr.contracts import Urn
from mrr.domain.exceptions import ClaimNotFoundError, ObjectNotFoundError
from mrr.domain.projection import (
    ClaimTableRow,
    ProvenanceEdge,
    ProvenanceMap,
    build_claim_table_row,
)
from mrr.domain.repositories import EdgeRepository, ObjectRepository, StoredObject
from mrr.provenance.log import AppendedEvent

#: task-packets/E3-T02.yaml/E3-T06.yaml's own event-type conventions,
#: transcribed here (not imported — neither ``mrr.services.claim.service``
#: nor ``mrr.services.correction.service`` exports its event-type string
#: constants, matching every other service module's own local, undeclared
#: string literals for this purpose). See the module docstring's "Claim/
#: correction discovery" section for why these two events are the discovery
#: mechanism.
_CLAIM_CREATED_EVENT_TYPE = "claim.created"
_CORRECTION_RECORDED_EVENT_TYPE = "correction.recorded"

_CLAIM_KIND = "Claim"
_CORRECTION_KIND = "CorrectionEvent"
_EVIDENCE_ANCHOR_KIND = "EvidenceAnchor"

#: The one edge type MRR-MTH-004's ceiling-gate projection extension
#: resolves — see the module docstring's "MRR-MTH-004 ceiling-gate
#: extension" section.
_RULED_BY_EDGE_TYPE = "ruled_by"

#: The two ``EvidenceAnchor`` fields ``_field_reference_hops`` follows — see
#: the module docstring's "Provenance map" section.
_ANCHOR_FIELD_REFERENCES: tuple[str, ...] = ("source_record_id", "run_id")


class _EventJournal(Protocol):
    """The one read operation this service needs from an event log — see
    ``mrr.services.correction.service._EventJournal`` for the identical
    rationale (deliberately narrower than the generic
    ``mrr.provenance.log.EventLog[TTx]`` Protocol).
    """

    def read_all(self) -> list[AppendedEvent]: ...


class ProjectionService:
    """docs/spec/01_SYSTEM_SPEC.md section 7.9 ("Projection Service"),
    implemented per task-packets/E3-T07.yaml. See the module docstring for
    the full design rationale — above all, that this class writes no
    object/revision/event/edge anywhere; it is a pure read-and-shape layer
    over ``mrr.domain.projection``.
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

    # ------------------------------------------------------------------
    # The claim table (MRR-FR-095/100).
    # ------------------------------------------------------------------

    def build_claim_table(self) -> list[ClaimTableRow]:
        """One ``ClaimTableRow`` per claim ever created (see the module
        docstring's "Claim/correction discovery" section), sorted by
        ``claim_id`` for a deterministic, order-independent result.

        Never writes anything. Calling this twice against an unchanged graph
        returns an equal list both times — every input it reads
        (``_EventJournal.read_all()``, ``ObjectRepository.get_latest``) is
        itself a pure read of already-persisted, unchanging data.
        """
        claim_ids = self._discover_ids_by_event_type(_CLAIM_CREATED_EVENT_TYPE)
        corrections = self._read_correction_bodies()

        rows: list[ClaimTableRow] = []
        for claim_id in sorted(claim_ids):
            claim_body = self._latest_body_or_none(claim_id)
            if claim_body is None or claim_body.get("kind") != _CLAIM_KIND:
                # A dangling genesis event with no resolvable Claim behind it
                # (should not happen in practice — every "claim.created"
                # event is written atomically with its revision-1 object —
                # but the projection fails soft here rather than raising,
                # matching CorrectionImpactService._require_review_if_needed's
                # identical "no stored object / wrong kind -> skip" stance
                # for an edge endpoint no service can guarantee still exists).
                continue
            ceiling_chain = self._resolve_latest_ruling_ceiling_chain(claim_id)
            if ceiling_chain is None:
                rows.append(build_claim_table_row(claim_body, corrections))
            else:
                ruled_ceiling, profile_max_ceiling = ceiling_chain
                rows.append(
                    build_claim_table_row(
                        claim_body,
                        corrections,
                        ruled_ceiling=ruled_ceiling,
                        profile_max_ceiling=profile_max_ceiling,
                    )
                )
        return rows

    # ------------------------------------------------------------------
    # The provenance map (MRR-FR-100).
    # ------------------------------------------------------------------

    def build_provenance_map(self, claim_id: Urn) -> ProvenanceMap:
        """The typed-edge/field paths from ``claim_id`` to the evidence
        anchors, source records, and runs it actually traces back to. See
        the module docstring's "Provenance map" section for the full
        traversal rationale.

        Raises:
            ClaimNotFoundError: ``claim_id`` resolves to no stored object at
                all, or resolves to an object that is not a ``Claim``.
        """
        root = self._get_object_or_none(claim_id)
        if root is None or root.kind != _CLAIM_KIND:
            raise ClaimNotFoundError(claim_id)

        edges = self._trace_provenance(claim_id)
        return ProvenanceMap(claim_id=claim_id, edges=tuple(edges))

    # ------------------------------------------------------------------
    # Internal helpers.
    # ------------------------------------------------------------------

    def _discover_ids_by_event_type(self, event_type: str) -> set[str]:
        return {
            appended.event.object_id
            for appended in self._event_log.read_all()
            if appended.event.event_type == event_type
        }

    def _read_correction_bodies(self) -> list[dict[str, object]]:
        correction_ids = self._discover_ids_by_event_type(_CORRECTION_RECORDED_EVENT_TYPE)
        bodies: list[dict[str, object]] = []
        for correction_id in sorted(correction_ids):
            body = self._latest_body_or_none(correction_id)
            if body is not None and body.get("kind") == _CORRECTION_KIND:
                bodies.append(body)
        return bodies

    def _get_object_or_none(self, object_id: str) -> StoredObject | None:
        try:
            return self._object_repository.get_latest(object_id)
        except ObjectNotFoundError:
            return None

    def _latest_body_or_none(self, object_id: str) -> dict[str, object] | None:
        obj = self._get_object_or_none(object_id)
        return None if obj is None else obj.body

    def _resolve_latest_ruling_ceiling_chain(self, claim_id: str) -> tuple[str, str] | None:
        """Resolve ``claim_id``'s MOST RECENT ``ruled_by`` edge (if any) to a
        ``(ruled_ceiling, profile_max_ceiling)`` pair — see the module
        docstring's "MRR-MTH-004 ceiling-gate extension" section for why the
        most recent edge, not every attached one. Returns ``None`` whenever
        no ``ruled_by`` edge exists, or the chain fails to resolve at any
        hop (dangling reference at any point — a ``MethodRuling``/
        ``MethodProtocol``/``MethodProfile`` no longer or never resolving),
        fully in line with this service's own established "fails soft, never
        raises, on an edge endpoint nothing can guarantee still exists"
        stance (see ``build_claim_table``'s own identical stance for a
        dangling claim genesis event).
        """
        ruled_by_edges = self._edge_repository.edges_from(claim_id, _RULED_BY_EDGE_TYPE)
        if not ruled_by_edges:
            return None
        latest_edge = ruled_by_edges[-1]

        ruling = self._get_object_or_none(latest_edge.target_id)
        if ruling is None:
            return None
        protocol_id = ruling.body.get("protocol_id")
        protocol = self._get_object_or_none(str(protocol_id)) if protocol_id else None
        if protocol is None:
            return None
        profile_id = protocol.body.get("profile_id")
        profile = self._get_object_or_none(str(profile_id)) if profile_id else None
        if profile is None:
            return None

        ruled_ceiling = ruling.body.get("ruled_ceiling")
        profile_max_ceiling = profile.body.get("max_claim_ceiling")
        if ruled_ceiling is None or profile_max_ceiling is None:
            return None
        return str(ruled_ceiling), str(profile_max_ceiling)

    def _trace_provenance(self, claim_id: str) -> list[ProvenanceEdge]:
        """Breadth-first, cycle-safe traversal of every outgoing typed edge
        (any edge type) reachable from ``claim_id``, plus every
        ``EvidenceAnchor`` field reference discovered along the way — see
        the module docstring's "Provenance map" section. A ``visited`` set
        keyed on object id makes this terminate on any finite graph,
        including cycles, and ensures each id is expanded at most once.
        """
        visited: set[str] = {claim_id}
        frontier: list[str] = [claim_id]
        collected: list[ProvenanceEdge] = []

        while frontier:
            next_frontier: list[str] = []
            for node_id in frontier:
                for hop in self._outgoing_provenance_hops(node_id):
                    collected.append(hop)
                    if hop.target_id not in visited:
                        visited.add(hop.target_id)
                        next_frontier.append(hop.target_id)
            frontier = next_frontier

        # Sorted for a deterministic, order-independent result regardless of
        # the repositories' own row order (task-packets/E3-T07.yaml
        # invariant: "byte-identical on repeated builds").
        return sorted(
            collected, key=lambda hop: (hop.source_id, hop.relation, hop.target_id, hop.via)
        )

    def _outgoing_provenance_hops(self, node_id: str) -> list[ProvenanceEdge]:
        hops: list[ProvenanceEdge] = []
        for edge in self._edge_repository.edges_from(node_id):
            target = self._get_object_or_none(edge.target_id)
            if target is None:
                continue
            hops.append(
                ProvenanceEdge(
                    source_id=edge.source_id,
                    target_id=edge.target_id,
                    target_kind=target.kind,
                    relation=edge.edge_type,
                    via="edge",
                    edge_id=edge.id,
                )
            )

        node_obj = self._get_object_or_none(node_id)
        if node_obj is not None and node_obj.kind == _EVIDENCE_ANCHOR_KIND:
            hops.extend(self._field_reference_hops(node_obj))
        return hops

    def _field_reference_hops(self, anchor: StoredObject) -> list[ProvenanceEdge]:
        hops: list[ProvenanceEdge] = []
        for field_name in _ANCHOR_FIELD_REFERENCES:
            referenced_id = anchor.body.get(field_name)
            if not referenced_id:
                continue
            target = self._get_object_or_none(referenced_id)
            if target is None:
                continue
            hops.append(
                ProvenanceEdge(
                    source_id=anchor.id,
                    target_id=referenced_id,
                    target_kind=target.kind,
                    relation=field_name,
                    via="field",
                    edge_id=None,
                )
            )
        return hops
