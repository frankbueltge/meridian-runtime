"""``ObligationService`` (task-packets/E6-T02.yaml): materialize the
persisted ``Obligation`` aggregate (docs/spec/02_DOMAIN_MODEL.md section
2.15) from an accepted/adapted ``TransferContract``'s structural obligation
stubs and non-empty ``caveats`` field (MRR-FR-083), bind it to the
transferred object(s) — and every object later found to be built on
them — via ``subject_to_obligation`` edges, and record the two verified
response actions, ``resolve``/``defer``.

Closest templates, explicitly named by the packet: ``mrr.services.
correction.service.CorrectionImpactService`` (the record()/propagate()
split, the query-driven BFS feeding a pure closure function, the
provenance/idempotency shape) and ``mrr.services.transfer.service.
TransferService`` (the event-derived-decision read, and
``bind_edge_unit_of_work``'s edges-plus-event atomic composition,
generalized here one step further to an object-revision-plus-edges-plus-
event composition — see "Atomic creation/propagation writes" below).

--- Reads TransferContract; never modifies TransferService ------------------

This service reads an already-accepted/adapted ``TransferContract`` (its
``transferred_objects``, ``caveats``, ``obligations`` stubs, and its LIVE,
event-derived decision) directly via the generic ``ObjectRepository`` and
event log — never through ``mrr.services.transfer.service.TransferService``,
which this task's forbidden_changes explicitly keeps untouched. The
event-derived-decision read (``_latest_transfer_decision`` below) is a
LOCAL structural copy of that service's own ``_current_status`` helper's
reasoning (task-packets/E6-T01.yaml's own ADR-0007 argument: a
TransferContract's stored ``status`` field is a permanent creation-time
snapshot, "created", because ``offer``/``respond`` are event-only
transitions that never mint a new content revision) — narrowed to exactly
what this task needs: the latest ``transfer.responded`` event's ``decision``
payload value, per E6-T01's own acceptance_tests naming that literal key.

--- Materialization gating (MRR-FR-083, derived_decisions (d)) --------------

``materialize_from_transfer`` proceeds ONLY when that latest decision is
``"accepted"``/``"adapted"``. ``"rejected"``/``"deferred"``/``"unresolved"``,
and a transfer that was never responded to at all (no ``transfer.responded``
event exists yet — still ``"created"``/``"offered"``), all raise
``TransferNotAcceptedError`` and persist nothing: nothing was actually
received by the practice, so there is nothing yet to bind an obligation to.
This last case (never responded) is not literally named by the packet's own
acceptance_tests list (which names the three terminal non-accepting
decisions), but follows directly from the same invariant text
("materialized ... ONLY when ... accepted or adapted") read fail-closed
rather than fail-open for the "no decision recorded yet" case — flagged in
the PR body as this task's own extension, not a literal enumeration.

--- One Obligation per stub, one more for non-empty caveats (derived
    decisions (c), (e)) --------------------------------------------------

Every entry in ``TransferContract.obligations`` (kind + optional deadline)
materializes its own independent ``Obligation`` object, each bound
identically to that SAME transfer's entire ``transferred_objects`` id set —
E6-T01's stub shape carries no per-stub object scope, so finer binding is not
possible without a TransferContract schema change (out of this task's
allowed_paths). A non-empty ``TransferContract.caveats`` field additionally
materializes exactly one further ``Obligation`` of kind ``"retain_caveat"``,
carrying the caveat text(s) verbatim as its own ``caveat_text`` field — this
is independent of any explicit ``retain_caveat``-kind stub already present in
``obligations``; the two mechanisms may each produce their own Obligation on
the same transfer.

--- Atomic creation/propagation writes: object revision + edges + event -----

``materialize_from_transfer`` writes, atomically, in ONE transaction: a
brand-new Obligation's revision-1 content record, ONE ``subject_to_
obligation`` edge per bound object (source=the bound object, target=this
Obligation's id — mirroring ``mrr.services.claim.service.ClaimService.
add_dependency_edge``'s own source-is-the-dependent/target-is-the-depended-
upon convention, generalized here to "source is the object subject to the
duty, target is the duty itself"), and ONE ``obligation.created`` event.
``propagate`` writes the identical THREE-WAY composition (a new revision
updating ``propagated_objects``, any newly-discovered binding edges, and ONE
``obligation.propagated`` event) — but only when there is something new to
bind (see "Idempotent propagate" below); otherwise it is a pure no-op, no
write of any kind. Both operations therefore share ONE local unit-of-work
function, ``bind_revision_with_edges_unit_of_work`` — a further, explicitly
documented generalization of ``mrr.services.transfer.service.
bind_edge_unit_of_work``'s own "ONE event, any number of edges" composition
to also cover the accompanying object-revision write, exactly the same kind
of deliberate, minimal-invention duplication that module's own docstring
already flags for reviewer scrutiny (composing a Postgres object-revision
insert with a list of edge inserts and one event append, because
``mrr.persistence.unit_of_work`` offers only the revision-plus-event path
and the event-only path, neither of which touches ``edges``, and this task's
``allowed_paths`` does not include ``packages/persistence/**`` any more than
E3-T02's or E6-T01's did).

``resolve``/``defer`` need no edges at all (MRR-NFR-002's append-only
binding-history invariant: neither ever rewrites or deletes a
``subject_to_obligation`` edge) — they reuse the plain, already-established
revision-plus-event composition (``mrr.persistence.unit_of_work.
record_object_revision_with_event``, via a local ``bind_unit_of_work``,
identical in shape to every other service module's own).

--- Idempotent propagate: a single "anything new?" gate ---------------------

``propagate`` recomputes ``mrr.domain.obligation_propagation.
compute_obligation_binding`` over the current real edge graph (seeded from
the Obligation's own stored ``bound_objects`` — never re-derived from the
source transfer), and compares the result against which ids ALREADY carry a
``subject_to_obligation`` edge to this Obligation
(``EdgeRepository.edges_to(obligation_id, "subject_to_obligation")``, read
as the single authoritative "already bound" set — not the stored
``propagated_objects`` field, which this call is about to rewrite). If every
computed id already has such an edge, ``propagate`` is a complete no-op: no
new revision, no new edge, no new event — satisfying the packet's own
idempotency invariant literally ("calling propagate twice ... writes no new
Obligation revision and adds no duplicate subject_to_obligation edge") and
its "callable-anytime recomputation" invariant (a later call, after a new
derivative edge is added elsewhere, discovers exactly the newly-missing ids
and binds only those, without disturbing any previously-recorded edge).

--- Gathering edges: a query-driven BFS feeding the pure closure function ---

Exactly ``CorrectionImpactService._gather_impact_edges``'s own shape and
rationale, duplicated here rather than imported (that method is private to a
different service class, and this task's forbidden_changes keeps
``mrr.services.correction.service`` untouched): ``EdgeRepository`` has no
"list every edge" method, so ``_gather_binding_edges`` drives its own
breadth-first expansion via ``edges_to``, keeping only
``IMPACT_EDGE_TYPES``-typed edges, and hands the collected edges to
``compute_obligation_binding`` once, at the end, for the actual authoritative
closure — this method's own bookkeeping decides only which edges to fetch
next, never what the final bound set is.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

import sqlalchemy as sa
from mrr.contracts import Obligation, TransferContract, Urn
from mrr.domain.correction_impact import IMPACT_EDGE_TYPES
from mrr.domain.exceptions import (
    ObjectNotFoundError,
    ObligationNotFoundError,
    ObligationSourceTransferNotFoundError,
    TransferNotAcceptedError,
    UnknownEdgeTypeError,
)
from mrr.domain.hashing_policy import compute_content_hash
from mrr.domain.identity import new_urn
from mrr.domain.lifecycles import OBLIGATION_LIFECYCLE
from mrr.domain.obligation_propagation import compute_obligation_binding
from mrr.domain.repositories import (
    EDGE_VOCABULARY,
    EdgeRepository,
    ObjectRepository,
    StoredObject,
    TypedEdge,
)
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.persistence.tables import edges_table
from mrr.persistence.unit_of_work import record_object_revision_with_event
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from sqlalchemy import Engine

#: docs/spec/03_API_AND_EVENTS.md section 5.2's required events for this
#: entity, plus ``obligation.propagated`` and ``obligation.deferred``
#: (task-packets/E6-T02.yaml derived_decisions (h): "Required events" read as
#: a floor, not an exhaustive ceiling — the same reading E6-T01 already makes
#: for ``transfer.created``, and E3-T06 for ``correction.impact_computed``).
_EVENT_CREATED = "obligation.created"
_EVENT_PROPAGATED = "obligation.propagated"
_EVENT_RESOLVED = "obligation.resolved"
_EVENT_DEFERRED = "obligation.deferred"

#: The event this module reads (never writes) to determine a TransferContract's
#: LIVE, event-derived decision — ``mrr.services.transfer.service``'s own
#: ``_EVENT_RESPONDED`` constant, duplicated here rather than imported since
#: that module's constants are private (task-packets/E6-T02.yaml
#: forbidden_changes: read, never modify, that service).
_TRANSFER_RESPONDED_EVENT_TYPE = "transfer.responded"

#: The two ``TransferContract`` decisions MRR-FR-083 obligations may
#: materialize from (docs/spec/01_SYSTEM_SPEC.md MRR-FR-081/083;
#: task-packets/E6-T02.yaml derived_decisions (d)).
_ACCEPTING_DECISIONS: frozenset[str] = frozenset({"accepted", "adapted"})

#: The edge type this module binds Obligations with — already a declared
#: ``EDGE_VOCABULARY`` member (added by an earlier task, unused in code until
#: this one; task-packets/E6-T02.yaml forbidden_changes confirms no DDL
#: change is needed to use it).
_SUBJECT_TO_OBLIGATION_EDGE_TYPE = "subject_to_obligation"

#: The one additional Obligation kind derived_decisions (c) materializes from
#: a TransferContract's non-empty ``caveats`` field.
_RETAIN_CAVEAT_KIND = "retain_caveat"

#: A syntactically valid ``$defs.sha256``-shaped placeholder, overwritten by
#: the real ``compute_content_hash`` result before persisting — the value
#: itself is inert: ``compute_content_hash`` excludes the ``content_hash``
#: key from what it hashes regardless of what is stored there (``mrr.domain.
#: hashing_policy``'s own "hashed payload" field-selection policy), so this
#: placeholder never leaks into any persisted hash.
_PLACEHOLDER_CONTENT_HASH = "sha256:" + "0" * 64

#: The callable shape ``mrr.persistence.unit_of_work.
#: record_object_revision_with_event`` takes once its ``engine``/
#: ``object_repository``/``event_log`` arguments are bound — the plain
#: revision-plus-event path, used by ``resolve``/``defer`` (neither ever
#: touches ``subject_to_obligation`` edges). A local copy, not a shared
#: import — see ``mrr.services.claim.service``'s own module docstring for
#: why each service module keeps its own.
RecordRevisionWithEvent = Callable[
    [StoredObject, int | None, DomainEvent], tuple[StoredObject, AppendedEvent]
]

#: The callable shape ``bind_revision_with_edges_unit_of_work`` below
#: produces: insert an object revision, ANY NUMBER of typed edges, and
#: append exactly ONE domain event, all atomically. See the module
#: docstring's "Atomic creation/propagation writes" section.
RecordRevisionWithEdgesAndEvent = Callable[
    [StoredObject, int | None, list[TypedEdge], DomainEvent],
    tuple[StoredObject, list[TypedEdge], AppendedEvent],
]


class _EventJournal(Protocol):
    """The one read operation this module needs from an event log — see
    ``mrr.services.correction.service._EventJournal`` for the identical
    rationale.
    """

    def read_all(self) -> list[AppendedEvent]: ...


def bind_unit_of_work(
    engine: Engine,
    object_repository: PostgresObjectRepository,
    event_log: PostgresEventLog,
) -> RecordRevisionWithEvent:
    """Bind ``record_object_revision_with_event`` to a concrete
    ``sqlalchemy.Engine``/``PostgresObjectRepository``/``PostgresEventLog``
    triple — identical in shape and purpose to every other service module's
    own ``bind_unit_of_work``. Used by ``resolve``/``defer`` only.
    """

    def _record(
        obj: StoredObject,
        expected_current_revision: int | None,
        event: DomainEvent,
    ) -> tuple[StoredObject, AppendedEvent]:
        return record_object_revision_with_event(
            engine, object_repository, event_log, obj, expected_current_revision, event
        )

    return _record


def bind_revision_with_edges_unit_of_work(
    engine: Engine,
    object_repository: PostgresObjectRepository,
    event_log: PostgresEventLog,
) -> RecordRevisionWithEdgesAndEvent:
    """Compose an object-revision insert, ANY NUMBER of ``edges`` table
    inserts, and ONE domain-event append into a SINGLE database transaction.
    See the module docstring's "Atomic creation/propagation writes" section
    for why this exists (rather than an addition to ``mrr.persistence``,
    which this task's ``allowed_paths`` does not include) and why it is not a
    second, divergent implementation of "how an edge is inserted": same
    columns, same values, same ``EDGE_VOCABULARY``/``UnknownEdgeTypeError``
    check as ``mrr.persistence.repositories.PostgresEdgeRepository.add_edge``
    and every other service module's own ``bind_edge_unit_of_work``, further
    generalized to also cover the accompanying object-revision write.

    Every edge's ``edge_type`` is validated against ``EDGE_VOCABULARY``
    BEFORE the transaction opens — an unknown type in ANY entry aborts the
    whole call with no partial insert, and no object revision write either.

    Used by ``materialize_from_transfer`` (``expected_current_revision=None``,
    a brand-new Obligation) and ``propagate`` (``expected_current_revision=
    latest.revision``, an existing Obligation gaining a new revision).

    Production wiring and integration tests call this once; DB-free unit
    tests pass their own trivial callable of the same
    ``RecordRevisionWithEdgesAndEvent`` shape, backed by in-memory fakes,
    instead.
    """

    def _record(
        obj: StoredObject,
        expected_current_revision: int | None,
        edges: list[TypedEdge],
        event: DomainEvent,
    ) -> tuple[StoredObject, list[TypedEdge], AppendedEvent]:
        for edge in edges:
            if edge.edge_type not in EDGE_VOCABULARY:
                raise UnknownEdgeTypeError(edge.edge_type)
        with engine.begin() as conn:
            stored = object_repository.insert_revision_with_connection(
                conn, obj, expected_current_revision
            )
            for edge in edges:
                conn.execute(
                    sa.insert(edges_table).values(
                        id=edge.id,
                        source_id=edge.source_id,
                        target_id=edge.target_id,
                        edge_type=edge.edge_type,
                        created_at=edge.created_at,
                        created_by=edge.created_by,
                        practice_id=edge.practice_id,
                        scope=edge.scope,
                        status=edge.status,
                    )
                )
            appended = event_log.append(conn, event)
        return stored, edges, appended

    return _record


# ---------------------------------------------------------------------------
# Internal helpers.
# ---------------------------------------------------------------------------


def _latest_transfer_decision(
    event_log: _EventJournal, transfer_id: str
) -> tuple[str | None, str | None]:
    """The TransferContract's LIVE decision, per E6-T01's own ADR-0007
    event-derived-status convention: the ``decision`` payload of the most
    recently appended ``transfer.responded`` event for ``transfer_id``, and
    that event's own id (used as this Obligation's ``trigger`` content). Both
    are ``None`` if no such event has been recorded yet.
    """
    responded_events = [
        appended.event
        for appended in event_log.read_all()
        if appended.event.object_id == transfer_id
        and appended.event.event_type == _TRANSFER_RESPONDED_EVENT_TYPE
    ]
    if not responded_events:
        return None, None
    last = responded_events[-1]
    return str(last.payload["decision"]), last.id


def _make_subject_to_obligation_edge(
    object_id: str, obligation_id: str, *, actor: str, practice_id: str, now: datetime
) -> TypedEdge:
    """One ``subject_to_obligation`` edge: source=the object bound by the
    duty, target=the Obligation's own id — mirroring
    ``mrr.services.claim.service.ClaimService.add_dependency_edge``'s
    source-is-the-dependent/target-is-the-depended-upon convention.
    """
    return TypedEdge(
        id=new_urn("edge"),
        source_id=object_id,
        target_id=obligation_id,
        edge_type=_SUBJECT_TO_OBLIGATION_EDGE_TYPE,
        created_at=now,
        created_by=actor,
        scope=None,
        status="active",
        practice_id=practice_id,
    )


def _build_obligation_body(
    *,
    obligation_id: str,
    contract: TransferContract,
    obligation_kind: str,
    deadline: datetime | None,
    bound_object_ids: list[str],
    trigger: str,
    caveat_text: list[str] | None,
    actor: str,
    now: datetime,
) -> dict[str, Any]:
    """Synthesize a brand-new Obligation's revision-1 body as a plain,
    JSON-safe dict — mirroring ``CorrectionImpactService._write_impact_
    objects``'s identical "build the dict, hash it, re-validate" shape,
    since (unlike every other service's own ``create()``) this task's
    ``materialize_from_transfer`` synthesizes the Obligation itself rather
    than accepting an already-valid caller-supplied model (there is no
    caller-supplied Obligation to accept in the first place — the only input
    is ``transfer_id``).
    """
    body: dict[str, Any] = {
        "id": obligation_id,
        "api_version": "mrr/v1alpha1",
        "kind": "Obligation",
        "practice_id": contract.receiver_practice_id,
        "revision": 1,
        "created_at": now.isoformat(),
        "created_by": actor,
        "content_hash": _PLACEHOLDER_CONTENT_HASH,
        "source_transfer_id": contract.id,
        "obligation_kind": obligation_kind,
        "responsible_practice_id": contract.receiver_practice_id,
        "trigger": trigger,
        "status": OBLIGATION_LIFECYCLE.initial_state,
        "bound_objects": list(bound_object_ids),
        "propagated_objects": [],
    }
    if deadline is not None:
        body["deadline"] = deadline.isoformat()
    if caveat_text is not None:
        body["caveat_text"] = list(caveat_text)

    content_hash = compute_content_hash(body)
    body["content_hash"] = content_hash

    # Re-run the Obligation contract's own validation against the EXACT
    # revision body about to be persisted — matches ClaimService._transition
    # / CorrectionImpactService._write_impact_objects's identical
    # "re-check before persisting" stance.
    Obligation.model_validate(body)
    return body


def _stored_object_from_body(body: dict[str, Any]) -> StoredObject:
    return StoredObject(
        id=body["id"],
        api_version=body["api_version"],
        kind=body["kind"],
        practice_id=body["practice_id"],
        revision=body["revision"],
        created_at=datetime.fromisoformat(body["created_at"]),
        created_by=body["created_by"],
        content_hash=body["content_hash"],
        supersedes=None,
        labels=None,
        body=body,
    )


class ObligationService:
    """docs/spec/02_DOMAIN_MODEL.md section 2.15 ("Obligation"), implemented
    per task-packets/E6-T02.yaml. See the module docstring for the full
    design rationale.
    """

    def __init__(
        self,
        object_repository: ObjectRepository,
        edge_repository: EdgeRepository,
        event_log: _EventJournal,
        record: RecordRevisionWithEvent,
        record_revision_with_edges: RecordRevisionWithEdgesAndEvent,
    ) -> None:
        self._object_repository = object_repository
        self._edge_repository = edge_repository
        self._event_log = event_log
        self._record = record
        self._record_revision_with_edges = record_revision_with_edges

    # ------------------------------------------------------------------
    # Materialization (MRR-FR-083): one-time creation, gated on the
    # transfer's event-derived decision.
    # ------------------------------------------------------------------

    def materialize_from_transfer(
        self,
        transfer_id: Urn,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> list[StoredObject]:
        """Materialize one ``Obligation`` per entry in the referenced
        ``TransferContract``'s ``obligations`` stub list, plus one more of
        kind ``"retain_caveat"`` if its ``caveats`` field is non-empty — each
        bound, via one ``subject_to_obligation`` edge per bound object,
        atomically with its own ``obligation.created`` event.

        Args:
            transfer_id: the ``TransferContract`` to materialize obligations
                from.

        Raises:
            ObligationSourceTransferNotFoundError: ``transfer_id`` resolves
                to no stored object at all.
            TransferNotAcceptedError: the transfer's latest event-derived
                decision (its latest ``transfer.responded`` event's
                ``decision`` payload, or no decision at all if never
                responded to) is not ``"accepted"``/``"adapted"``. Nothing
                is persisted.

        Returns:
            the list of newly created Obligation ``StoredObject``s, in
            ``obligations`` stub order, with the ``retain_caveat``-from-
            ``caveats`` Obligation (if any) last.
        """
        latest_transfer = self._get_transfer_or_raise(transfer_id)
        contract = TransferContract.model_validate(latest_transfer.body)

        decision, responded_event_id = _latest_transfer_decision(self._event_log, transfer_id)
        if decision not in _ACCEPTING_DECISIONS:
            raise TransferNotAcceptedError(transfer_id, decision)

        bound_object_ids = [ref.id for ref in contract.transferred_objects]
        trigger = f"transfer.responded:{responded_event_id}"
        now = datetime.now(UTC)

        stored: list[StoredObject] = []
        for stub in contract.obligations:
            stored.append(
                self._materialize_one(
                    contract=contract,
                    obligation_kind=stub.kind,
                    deadline=stub.deadline,
                    bound_object_ids=bound_object_ids,
                    trigger=trigger,
                    caveat_text=None,
                    actor=actor,
                    policy_version=policy_version,
                    correlation_id=correlation_id,
                    now=now,
                )
            )

        if contract.caveats:
            stored.append(
                self._materialize_one(
                    contract=contract,
                    obligation_kind=_RETAIN_CAVEAT_KIND,
                    deadline=None,
                    bound_object_ids=bound_object_ids,
                    trigger=trigger,
                    caveat_text=list(contract.caveats),
                    actor=actor,
                    policy_version=policy_version,
                    correlation_id=correlation_id,
                    now=now,
                )
            )

        return stored

    def _materialize_one(
        self,
        *,
        contract: TransferContract,
        obligation_kind: str,
        deadline: datetime | None,
        bound_object_ids: list[str],
        trigger: str,
        caveat_text: list[str] | None,
        actor: str,
        policy_version: str,
        correlation_id: str,
        now: datetime,
    ) -> StoredObject:
        obligation_id = new_urn("obligation")
        body = _build_obligation_body(
            obligation_id=obligation_id,
            contract=contract,
            obligation_kind=obligation_kind,
            deadline=deadline,
            bound_object_ids=bound_object_ids,
            trigger=trigger,
            caveat_text=caveat_text,
            actor=actor,
            now=now,
        )
        obj = _stored_object_from_body(body)
        edges = [
            _make_subject_to_obligation_edge(
                object_id,
                obligation_id,
                actor=actor,
                practice_id=contract.receiver_practice_id,
                now=now,
            )
            for object_id in bound_object_ids
        ]
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=_EVENT_CREATED,
            occurred_at=now,
            actor=actor,
            policy_version=policy_version,
            causation_id=None,
            correlation_id=correlation_id,
            object_id=obligation_id,
            object_revision=1,
            payload={
                "source_transfer_id": contract.id,
                "obligation_kind": obligation_kind,
                "bound_object_ids": list(bound_object_ids),
            },
        )
        stored, _edges, _appended = self._record_revision_with_edges(obj, None, edges, event)
        return stored

    # ------------------------------------------------------------------
    # Propagation: repeatable, idempotent, callable anytime.
    # ------------------------------------------------------------------

    def propagate(
        self,
        obligation_id: Urn,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """Recompute ``mrr.domain.obligation_propagation.
        compute_obligation_binding`` over the current real edge graph
        (seeded from the Obligation's own stored ``bound_objects``), bind any
        newly-discovered downstream object via a new ``subject_to_obligation``
        edge, and — only if that computed set actually changed — write a new
        Obligation revision (``propagated_objects``, sorted) plus an
        ``obligation.propagated`` event, atomically with the new edges.

        A true no-op (no new revision, no new edge, no new event) when every
        currently-computed id already carries a ``subject_to_obligation``
        edge to this Obligation — see the module docstring's "Idempotent
        propagate" section.

        Raises:
            ObligationNotFoundError: ``obligation_id`` resolves to no stored
                object at all.
        """
        latest = self._get_obligation_or_raise(obligation_id)
        seed_ids: set[str] = set(latest.body["bound_objects"])

        edges = self._gather_binding_edges(seed_ids)
        computed = compute_obligation_binding(seed_ids, edges)

        existing_bound_ids = {
            edge.source_id
            for edge in self._edge_repository.edges_to(
                obligation_id, _SUBJECT_TO_OBLIGATION_EDGE_TYPE
            )
        }
        missing_ids = sorted(computed - existing_bound_ids)

        if not missing_ids:
            return latest

        now = datetime.now(UTC)
        new_edges = [
            _make_subject_to_obligation_edge(
                object_id,
                obligation_id,
                actor=actor,
                practice_id=latest.practice_id,
                now=now,
            )
            for object_id in missing_ids
        ]

        new_body = dict(latest.body)
        new_body["propagated_objects"] = sorted(computed)
        new_revision = latest.revision + 1
        new_body["revision"] = new_revision
        new_body["created_at"] = now.isoformat()
        new_body["created_by"] = actor
        new_content_hash = compute_content_hash(new_body)
        new_body["content_hash"] = new_content_hash
        Obligation.model_validate(new_body)

        obj = StoredObject(
            id=latest.id,
            api_version=latest.api_version,
            kind=latest.kind,
            practice_id=latest.practice_id,
            revision=new_revision,
            created_at=now,
            created_by=actor,
            content_hash=new_content_hash,
            supersedes=latest.supersedes,
            labels=latest.labels,
            body=new_body,
        )
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=_EVENT_PROPAGATED,
            occurred_at=now,
            actor=actor,
            policy_version=policy_version,
            causation_id=self._last_event_id_for(obligation_id),
            correlation_id=correlation_id,
            object_id=obligation_id,
            object_revision=new_revision,
            payload={
                "propagated_objects": sorted(computed),
                "newly_bound_object_ids": missing_ids,
            },
        )
        stored, _edges, _appended = self._record_revision_with_edges(
            obj, latest.revision, new_edges, event
        )
        return stored

    def _gather_binding_edges(self, seed_ids: set[str]) -> list[TypedEdge]:
        """Breadth-first-expand the Obligation's downstream closure via
        ``EdgeRepository.edges_to``, collecting only impact-typed edges. See
        the module docstring's "Gathering edges" section.
        """
        visited: set[str] = set()
        frontier: set[str] = set(seed_ids)
        collected: list[TypedEdge] = []
        while frontier:
            next_frontier: set[str] = set()
            for node_id in frontier:
                if node_id in visited:
                    continue
                visited.add(node_id)
                for edge in self._edge_repository.edges_to(node_id):
                    if edge.edge_type not in IMPACT_EDGE_TYPES:
                        continue
                    collected.append(edge)
                    if edge.source_id not in visited:
                        next_frontier.add(edge.source_id)
            frontier = next_frontier
        return collected

    # ------------------------------------------------------------------
    # resolve()/defer(): each mints a NEW REVISION (Obligation is unsigned).
    # ------------------------------------------------------------------

    def resolve(
        self,
        obligation_id: Urn,
        resolution_evidence: str,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """``open -> resolved``, setting ``resolution_evidence``.

        Raises:
            ObligationNotFoundError: ``obligation_id`` resolves to no stored
                object at all.
            InvalidTransitionError: the Obligation is not currently
                ``"open"``. Nothing is persisted.
        """
        return self._transition(
            obligation_id,
            "resolved",
            event_type=_EVENT_RESOLVED,
            extra_fields={"resolution_evidence": resolution_evidence},
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
        )

    def defer(
        self,
        obligation_id: Urn,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """``open -> deferred``.

        Raises:
            ObligationNotFoundError: ``obligation_id`` resolves to no stored
                object at all.
            InvalidTransitionError: the Obligation is not currently
                ``"open"``. Nothing is persisted.
        """
        return self._transition(
            obligation_id,
            "deferred",
            event_type=_EVENT_DEFERRED,
            extra_fields={},
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
        )

    def _transition(
        self,
        obligation_id: str,
        to_status: str,
        *,
        event_type: str,
        extra_fields: dict[str, Any],
        actor: str,
        policy_version: str,
        correlation_id: str,
    ) -> StoredObject:
        latest = self._get_obligation_or_raise(obligation_id)
        from_status = latest.body["status"]
        OBLIGATION_LIFECYCLE.assert_transition(from_status, to_status)

        new_body = dict(latest.body)
        new_body["status"] = to_status
        new_body.update(extra_fields)
        new_revision = latest.revision + 1
        now = datetime.now(UTC)
        new_body["revision"] = new_revision
        new_body["created_at"] = now.isoformat()
        new_body["created_by"] = actor
        new_content_hash = compute_content_hash(new_body)
        new_body["content_hash"] = new_content_hash
        Obligation.model_validate(new_body)

        obj = StoredObject(
            id=latest.id,
            api_version=latest.api_version,
            kind=latest.kind,
            practice_id=latest.practice_id,
            revision=new_revision,
            created_at=now,
            created_by=actor,
            content_hash=new_content_hash,
            supersedes=latest.supersedes,
            labels=latest.labels,
            body=new_body,
        )
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=event_type,
            occurred_at=now,
            actor=actor,
            policy_version=policy_version,
            causation_id=self._last_event_id_for(obligation_id),
            correlation_id=correlation_id,
            object_id=obligation_id,
            object_revision=new_revision,
            payload={"from_status": from_status, "to_status": to_status},
        )
        stored, _appended = self._record(obj, latest.revision, event)
        return stored

    # ------------------------------------------------------------------
    # Internal lookups.
    # ------------------------------------------------------------------

    def _get_transfer_or_raise(self, transfer_id: str) -> StoredObject:
        try:
            return self._object_repository.get_latest(transfer_id)
        except ObjectNotFoundError:
            raise ObligationSourceTransferNotFoundError(transfer_id) from None

    def _get_obligation_or_raise(self, obligation_id: str) -> StoredObject:
        try:
            return self._object_repository.get_latest(obligation_id)
        except ObjectNotFoundError:
            raise ObligationNotFoundError(obligation_id) from None

    def _last_event_id_for(self, object_id: str) -> str | None:
        """The id of the most recently appended event for ``object_id``, or
        ``None`` if there is none yet — identical rationale to every other
        service module's own ``_last_event_id_for``.
        """
        matching_ids = [
            appended.event.id
            for appended in self._event_log.read_all()
            if appended.event.object_id == object_id
        ]
        return matching_ids[-1] if matching_ids else None
