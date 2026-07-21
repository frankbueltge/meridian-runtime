"""``TransferService`` (task-packets/E6-T01.yaml): create, offer, and respond
to a signed, cross-practice ``TransferContract`` — docs/spec/01_SYSTEM_SPEC.md
section 4.9 ("Stage 9 — Transfer and obligations", MRR-FR-080..083) and
docs/spec/02_DOMAIN_MODEL.md section 2.14. One class owns all three
operations — unlike ``mrr.services.task_bundle.service``'s deliberate
origin/node split (MRR-FR-022's separate authority requirement, which has no
analog here), matching the section 3.8 API surface's single
``POST /v1/transfers/{id}/respond`` endpoint (one verb, a ``decision``
parameter — not five separate accept/adapt/reject/defer/unresolved methods).

--- ADR-0007 applies: offer/respond are event-only, no new revision ---------

``TransferContract`` is, like ``TaskBundle`` and UNLIKE
``ResearchScore``/``Claim``/``CorrectionEvent``, BOTH cross-practice,
origin-signed (MRR-NFR-007) AND lifecycle-bearing (MRR-FR-081) — exactly the
structural situation docs/spec/adr/ADR-0007-TASK-BUNDLE-TRANSITIONS-ARE-
EVENTS.md documents for ``TaskBundle``. The same asymmetry therefore applies
here: ``create`` mints and sender-signs the ONE content record, at revision
1, over the ADR-0004 ``exclude_none=True`` canonical form — it is never
mutated again in this task's scope (no ``propose_modification`` analog
exists for a transfer, unlike ``TaskBundle``). ``offer`` and ``respond`` are
EVENT-ONLY transitions (``mrr.persistence.unit_of_work.record_event``) that
append a domain event without writing a new object content revision, so the
sender's origin signature always verifies directly against the CURRENT
(only) content record — no historical-revision scan, ever. The schema's own
``status`` field is, per ADR-0007's language for ``TaskBundle``, "that
record's creation-time snapshot" — it reads ``"created"`` for every stored
``TransferContract`` in this task's scope; the LIVE status is event-derived
(the latest ``transfer.offered``/``transfer.responded`` event, falling back
to the content record's own ``status`` when no transition event exists
yet — see ``_current_status``, a direct structural copy of
``mrr.services.task_bundle.service``'s own helper of the same name and
purpose).

Because a lifecycle transition writes no revision, ``offer``/``respond``
return ``TransferTransition`` rather than a bare ``StoredObject`` — the
unchanged content record paired with the newly current, event-derived
status — mirroring ``mrr.services.task_bundle.service.TaskBundleTransition``
exactly.

--- The TRANSFER_LIFECYCLE matrix is enforced ENTIRELY by the state machine -

``created -> offered -> exactly one of {accepted, adapted, rejected,
deferred, unresolved}`` (``mrr.domain.lifecycles.TRANSFER_LIFECYCLE`` —
task-packets/E6-T01.yaml, no section-6 diagram exists for this entity; see
that module's own comment block). None of the five terminal outcomes has
any drawn outgoing edge, and ``offered -> offered`` is never a legal
self-transition (``StateMachine.__post_init__`` forbids declaring one at
all) — so the entire illegal-transition acceptance matrix (respond before
offer; a second respond after a terminal outcome already recorded; offer
called twice) falls out of ``_current_status`` + ``TRANSFER_LIFECYCLE.
assert_transition`` alone, with no special-cased bookkeeping needed anywhere
in this module — exactly the same mechanism
``mrr.services.task_bundle.service``'s own double-accept/not-yet-offered
tests already rely on for ``TASK_BUNDLE_LIFECYCLE``.

--- respond: signature verified BEFORE recording ANY of the five outcomes ---

task-packets/E6-T01.yaml derived_decisions (c): "a forged or tampered offer
must not become recordable as 'rejected' any more than as 'accepted'."
``respond`` therefore resolves the sender's trusted signing key
(``mrr.domain.transfer_trust.resolve_trusted_transfer_key``) UNCONDITIONALLY,
before even checking whether ``decision`` names a lifecycle-legal
transition — signature resolution does not depend on ``decision`` at all
(it authenticates the CONTRACT's own signature field, not the response), so
checking it first costs nothing and closes the gap for every one of the five
outcomes uniformly, not just acceptance. On any resolution failure
(``TransferSignerMismatchError``/``UnknownKeyIdError``/
``TransferKeyNotValidError``/``SignatureVerificationError``/
``UnsupportedAlgorithmError``), nothing is read a second time and no event
is appended — the lifecycle-transition check never runs.

--- adapted: an atomic adapted_from edge alongside transfer.responded ------

MRR-FR-082 ("Adaptation MUST create a new local object and preserve the
relation to the source object"). This service does NOT genericize
construction of the adapted object's own content (task-packets/E6-T01.yaml
derived_decisions (d)) — the caller supplies an ALREADY-EXISTING local
object id (verified via ``ObjectRepository.get_latest``, raising
``ObjectNotFoundError`` if absent — propagated unchanged, before anything is
recorded). For every entry in ``contract.transferred_objects`` (ordinarily
exactly one; MRR-FR-082's "the source object," generalized to "each source
object" when a contract genuinely bundles more than one — task-packets/
E6-T01.yaml's own acceptance tests exercise the single-object case), an
``adapted_from`` edge is recorded — source=the adapted object, target=that
transferred object — mirroring the source-verb-target edge-direction
convention already established for ``derived_from`` in
``tests/integration/persistence/test_postgres_repositories.py`` ("a
derived_from hub" == edge(source=a, target=hub)). Every edge this produces,
plus the single ``transfer.responded`` event, is recorded atomically in ONE
database transaction (``bind_edge_unit_of_work`` below) — either all of it
lands, or none of it does; a nonexistent adapted object id aborts both the
edges and the event before any database write is attempted.

--- bind_edge_unit_of_work here is a LOCAL, GENERALIZED copy ---------------

``mrr.services.claim.service.bind_edge_unit_of_work`` composes exactly ONE
edge insert with ONE event append into one transaction (its own module
docstring: "Edge writes need their own atomic composition," since
``mrr.persistence.unit_of_work`` offers only the content-revision path and
the event-only path, neither of which touches ``edges``, and this task's
``allowed_paths`` does not include ``packages/persistence/**`` any more than
E3-T02's did). This module's own copy generalizes that shape to a LIST of
edges sharing ONE event — because ``respond("adapted", ...)`` may need to
record more than one ``adapted_from`` edge (one per ``transferred_objects``
entry) atomically with the single ``transfer.responded`` event, not exactly
one edge as ``ClaimService``'s own edge-writing methods always do. Same
columns, same values, same ``EDGE_VOCABULARY``/``UnknownEdgeTypeError``
fail-closed check as ``PostgresEdgeRepository.add_edge`` — nothing about
"how an edge is inserted" is reimplemented or diverges; only the "how many
edges share this one transaction and event" cardinality is generalized.

--- What this module deliberately does NOT do -------------------------------

No HTTP/FastAPI wiring (task-packets/E6-T01.yaml forbidden_changes — every
prior E1-E5 task packet drew this same line). No receiver-authority check
analogous to ``NodeAuthorityError`` (MRR-FR-022 has no stated analog for
``TransferContract`` in this task's requirements list; inventing one would
be exactly the kind of undrawn behavior AGENTS.md rule 3 forbids — flagged
in the PR body, not guessed here). No coarse ``RefusalReason``-style
category on ``respond`` (MRR-FR-081 asks only for the five-way decision
itself, unlike MRR-FR-024's task-bundle refusal, which does carry one). No
persisted ``Obligation`` aggregate, its own lifecycle, or any
``subject_to_obligation`` edge (E6-T02's scope — obligation/caveat data
travels as structural fields on the ONE signed content record only, never
touched again by ``offer``/``respond``). No cross-practice correction
notification, and no reject/defer-a-CORRECTION response recording (E6-T03/
T04 — MRR-FR-084 concerns a different object, a received correction
notification, not this transfer's own MRR-FR-081 decision; not claimed
here). No public unresolved-correction projection (E6-T05) or offline
recipient delivery tracking (E6-T06). No refactor of
``resolve_trusted_transfer_key`` into one shared resolver with
``resolve_trusted_task_key``/``resolve_trusted_crate_key`` even though the
three are now near-identical (task-packets/E6-T01.yaml forbidden_changes —
a future, separate, reviewed cleanup).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

import sqlalchemy as sa
from mrr.contracts import Practice, TransferContract, Urn
from mrr.domain.exceptions import (
    InvalidTransitionError,
    ObjectNotFoundError,
    ParticipantIdentifiableTransferError,
    TransferContractNotFoundError,
    UnknownEdgeTypeError,
)
from mrr.domain.identity import new_urn
from mrr.domain.lifecycles import TRANSFER_LIFECYCLE
from mrr.domain.manifest_trust import practice_key_ring
from mrr.domain.repositories import (
    EDGE_VOCABULARY,
    ObjectRepository,
    StoredObject,
    TypedEdge,
)
from mrr.domain.transfer_trust import resolve_trusted_transfer_key
from mrr.persistence.repositories import PostgresEventLog
from mrr.persistence.tables import edges_table
from mrr.persistence.unit_of_work import (
    RecordRevisionWithEvent as RecordRevisionWithEvent,
)
from mrr.persistence.unit_of_work import (
    bind_unit_of_work as bind_unit_of_work,
)
from mrr.persistence.unit_of_work import (
    record_event,
)
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from sqlalchemy import Engine

#: Sentinel "from" state used only when reporting ``InvalidTransitionError``
#: for ``create()`` with a non-``created`` initial status — the exact
#: pattern ``mrr.services.task_bundle.service``'s own
#: ``_NEW_BUNDLE_SENTINEL_STATE`` uses (there is no real "from" state for a
#: brand-new object). Never a member of ``TRANSFER_LIFECYCLE.states``.
_NEW_TRANSFER_SENTINEL_STATE = "<new>"

#: docs/spec/03_API_AND_EVENTS.md section 5.2's required events for this
#: entity, plus ``transfer.created`` (task-packets/E6-T01.yaml
#: specification_gaps item 4: "Required events" is read as a floor, not an
#: exhaustive ceiling, for MRR-NFR-001 provenance parity with every sibling
#: first-class object — flagged in the PR as a judgment call, not a literal
#: requirement).
_EVENT_CREATED = "transfer.created"
_EVENT_OFFERED = "transfer.offered"
_EVENT_RESPONDED = "transfer.responded"

#: Event types whose payload carries a lifecycle ``to_status`` — the drawn
#: ``TRANSFER_LIFECYCLE`` edges this module drives (ADR-0007's "current
#: status is derived from the latest lifecycle event"). Deliberately
#: excludes ``_EVENT_CREATED`` — creation is not a transition (there is no
#: "from" state), and the content record's own ``body["status"]`` already IS
#: the ``_current_status`` fallback.
_LIFECYCLE_TRANSITION_EVENT_TYPES: frozenset[str] = frozenset({_EVENT_OFFERED, _EVENT_RESPONDED})

#: The edge type ``respond("adapted", ...)`` records — already declared in
#: ``EDGE_VOCABULARY`` (added by an earlier task, unused in code until this
#: one; task-packets/E6-T01.yaml forbidden_changes confirms no DDL change is
#: needed to use it).
_ADAPTED_FROM_EDGE_TYPE = "adapted_from"

#: Mirrors the top-level `status` enum's five terminal decisions — the
#: values ``respond`` accepts (MRR-FR-081). A plain ``Literal``, matching
#: ``mrr.contracts.transfer_contract.TransferStatus``'s own vocabulary minus
#: ``"created"``/``"offered"`` (the two non-terminal states).
TransferDecision = Literal["accepted", "adapted", "rejected", "deferred", "unresolved"]

#: The callable shape ``mrr.persistence.unit_of_work.record_event`` takes
#: once its ``engine``/``event_log`` arguments are bound — the EVENT-ONLY
#: path (ADR-0007: ``offer`` and every non-``adapted`` ``respond`` outcome).
RecordEvent = Callable[[DomainEvent], AppendedEvent]

#: The callable shape ``bind_edge_unit_of_work`` below produces: insert ANY
#: NUMBER of typed edges and append exactly ONE domain event, atomically.
#: See the module docstring's "bind_edge_unit_of_work here is a LOCAL,
#: GENERALIZED copy" section for why this generalizes
#: ``mrr.services.claim.service.RecordEdgeWithEvent``'s single-edge shape to
#: a list.
RecordEdgesWithEvent = Callable[
    [list[TypedEdge], DomainEvent], tuple[list[TypedEdge], AppendedEvent]
]


class _EventJournal(Protocol):
    """The one read operation this module needs from an event log. Same
    shape and rationale as ``mrr.services.task_bundle.service._EventJournal``.
    """

    def read_all(self) -> list[AppendedEvent]: ...


@dataclass(frozen=True, slots=True)
class TransferTransition:
    """The result of an ADR-0007 event-only lifecycle transition — ``offer``
    or ``respond``. Mirrors
    ``mrr.services.task_bundle.service.TaskBundleTransition`` exactly: no
    new content revision is ever written by either operation, so there is no
    "new ``StoredObject``" to return.

    ``content`` is the unchanged content record ``ObjectRepository.get_latest``
    already returned before this transition ran (always revision 1 in this
    task's scope — there is no content-mutating operation after ``create``)
    — its own ``content.body["status"]`` is that record's creation-time
    snapshot and, after ``offer``/``respond``, will generally NOT equal
    ``status`` below; that divergence is ADR-0007's whole point, not a bug.
    ``appended_event`` is the domain event this transition just recorded —
    for ``respond("adapted", ...)`` this is the ``transfer.responded`` event
    only (the ``adapted_from`` edges it was recorded atomically with are not
    separately exposed here; a caller that needs them queries
    ``EdgeRepository.edges_from(adapted_object_id, "adapted_from")``, per
    the packet's own acceptance test).
    """

    content: StoredObject
    status: str
    appended_event: AppendedEvent


def bind_event_unit_of_work(engine: Engine, event_log: PostgresEventLog) -> RecordEvent:
    """Bind ``mrr.persistence.unit_of_work.record_event`` (ADR-0007's
    EVENT-ONLY path — no content revision) to a concrete
    ``sqlalchemy.Engine``/``PostgresEventLog`` pair. Production wiring and
    integration tests call this once; DB-free unit tests pass their own
    trivial callable of the same ``RecordEvent`` shape, backed by an
    in-memory fake event log, instead.
    """

    def _record_event(event: DomainEvent) -> AppendedEvent:
        return record_event(engine, event_log, event)

    return _record_event


def bind_edge_unit_of_work(engine: Engine, event_log: PostgresEventLog) -> RecordEdgesWithEvent:
    """Compose ANY NUMBER of ``edges`` table inserts with ONE domain-event
    append into a SINGLE database transaction. See the module docstring's
    "bind_edge_unit_of_work here is a LOCAL, GENERALIZED copy" section for
    why this exists (rather than an addition to ``mrr.persistence``, which
    this task's ``allowed_paths`` does not include) and why it is not a
    second, divergent implementation of "how an edge is inserted": same
    columns, same values, same ``EDGE_VOCABULARY``/``UnknownEdgeTypeError``
    check as ``mrr.persistence.repositories.PostgresEdgeRepository.add_edge``
    and ``mrr.services.claim.service.bind_edge_unit_of_work``, just sharing
    ``event_log.append``'s connection instead of opening its own, generalized
    from one edge to a list.

    Every edge's ``edge_type`` is validated against ``EDGE_VOCABULARY``
    BEFORE the transaction opens — an unknown type in ANY entry aborts the
    whole call with no partial insert.

    Production wiring and integration tests call this once; DB-free unit
    tests pass their own trivial callable of the same
    ``RecordEdgesWithEvent`` shape, backed by an in-memory fake, instead.
    """

    def _record_edges(
        edges: list[TypedEdge], event: DomainEvent
    ) -> tuple[list[TypedEdge], AppendedEvent]:
        for edge in edges:
            if edge.edge_type not in EDGE_VOCABULARY:
                raise UnknownEdgeTypeError(edge.edge_type)
        with engine.begin() as conn:
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
        return edges, appended

    return _record_edges


# ---------------------------------------------------------------------------
# Internal helpers.
# ---------------------------------------------------------------------------


def _transfer_to_stored_object(contract: TransferContract) -> StoredObject:
    """Convert ``contract`` (already schema-valid, including its native
    ``status`` field) into the generic ``StoredObject``
    ``ObjectRepository`` persists. ``body`` is a plain
    ``model_dump_json(exclude_none=True)`` — no added keys — so
    ``TransferContract.model_validate(stored.body)`` always succeeds,
    matching every other service's own ``_*_to_stored_object`` helper.
    """
    body: dict[str, Any] = json.loads(contract.model_dump_json(exclude_none=True))
    return StoredObject(
        id=contract.id,
        api_version=contract.api_version,
        kind=contract.kind,
        practice_id=contract.practice_id,
        revision=contract.revision,
        created_at=contract.created_at,
        created_by=contract.created_by,
        content_hash=contract.content_hash,
        supersedes=contract.supersedes,
        labels=contract.labels,
        body=body,
    )


def _reconstruct_contract(stored: StoredObject) -> TransferContract:
    """The inverse of ``_transfer_to_stored_object``: ``stored.body`` is
    already a plain, schema-valid ``TransferContract`` serialization, so
    this is a direct ``model_validate`` — no stripping needed.
    """
    return TransferContract.model_validate(stored.body)


def _get_latest_or_raise(object_repository: ObjectRepository, transfer_id: str) -> StoredObject:
    try:
        return object_repository.get_latest(transfer_id)
    except ObjectNotFoundError:
        raise TransferContractNotFoundError(transfer_id) from None


def _current_status(event_log: _EventJournal, latest: StoredObject) -> str:
    """The authoritative CURRENT ``TRANSFER_LIFECYCLE`` status (ADR-0007):
    the ``to_status`` of the most recently appended lifecycle-transition
    event for this transfer (``_LIFECYCLE_TRANSITION_EVENT_TYPES`` — offer,
    respond), or ``latest.body["status"]`` — the immutable content record's
    own creation-time status snapshot — when no transition event has been
    recorded yet (a freshly created contract, still ``"created"``).

    Direct structural copy of
    ``mrr.services.task_bundle.service._current_status`` — identical
    reasoning, applied to ``TransferContract``.
    """
    transition_events = [
        appended.event
        for appended in event_log.read_all()
        if appended.event.object_id == latest.id
        and appended.event.event_type in _LIFECYCLE_TRANSITION_EVENT_TYPES
    ]
    if not transition_events:
        return str(latest.body["status"])
    return str(transition_events[-1].payload["to_status"])


def _last_event_id_for(event_log: _EventJournal, transfer_id: str) -> str | None:
    """The id of the most recently appended event for ``transfer_id``, or
    ``None`` if there is none yet — the ``causation_id`` for the next event
    in that transfer's own causal chain (MRR-NFR-001). Identical logic to
    every other service's own ``_last_event_id_for``: ``read_all()`` returns
    events oldest-first, so the last match is the most recent.
    """
    matching_ids = [
        appended.event.id
        for appended in event_log.read_all()
        if appended.event.object_id == transfer_id
    ]
    return matching_ids[-1] if matching_ids else None


class TransferService:
    """docs/spec/01_SYSTEM_SPEC.md section 2.1 ("Transfer and Obligation
    Service") and section 4.9 (Stage 9), implemented per task-packets/
    E6-T01.yaml. See the module docstring for the full design rationale.
    """

    def __init__(
        self,
        object_repository: ObjectRepository,
        event_log: _EventJournal,
        record: RecordRevisionWithEvent,
        record_event: RecordEvent,
        record_edges: RecordEdgesWithEvent,
    ) -> None:
        self._object_repository = object_repository
        self._event_log = event_log
        self._record = record
        self._record_event = record_event
        self._record_edges = record_edges

    # ------------------------------------------------------------------
    # Creation (MRR-FR-080/083) — the one-time, sender-signed CONTENT write.
    # ------------------------------------------------------------------

    def create(
        self,
        contract: TransferContract,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """Persist ``contract`` as revision 1 in status ``"created"``, plus a
        ``transfer.created`` event, atomically — after rejecting any
        referenced transferred object classified PARTICIPANT_IDENTIFIABLE
        (MRR-NFR-006; see ``_ensure_no_participant_identifiable_object``).

        ``contract`` must already be a fully valid, sender-signed
        ``TransferContract`` — its own ``id``/``content_hash``/``signature``
        are minted by the caller (this service does not sign or hash on the
        caller's behalf, matching every other ``create()``/``record()`` in
        this codebase). ``contract.revision`` must be ``1`` and
        ``contract.status`` must be ``TRANSFER_LIFECYCLE.initial_state``
        (``"created"``).

        Per ADR-0007, this revision-1 content record is written ONCE and
        never touched again by ``offer``/``respond`` — see the module
        docstring. Its sender signature therefore always verifies directly
        against whatever ``get_latest`` returns.

        Raises:
            InvalidTransitionError: ``contract.status`` is not
                ``"created"``.
            ValueError: ``contract.revision`` is not ``1``.
            ParticipantIdentifiableTransferError: one of
                ``contract.transferred_objects`` resolves to a stored object
                classified ``PARTICIPANT_IDENTIFIABLE``. Nothing is
                persisted.
        """
        if contract.status != TRANSFER_LIFECYCLE.initial_state:
            raise InvalidTransitionError(
                TRANSFER_LIFECYCLE.name, _NEW_TRANSFER_SENTINEL_STATE, contract.status
            )
        if contract.revision != 1:
            raise ValueError(
                f"TransferContract.revision must be 1 for create(), got {contract.revision!r}"
            )

        self._ensure_no_participant_identifiable_object(contract)

        obj = _transfer_to_stored_object(contract)
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=_EVENT_CREATED,
            occurred_at=datetime.now(UTC),
            actor=actor,
            policy_version=policy_version,
            causation_id=None,
            correlation_id=correlation_id,
            object_id=contract.id,
            object_revision=1,
            payload={
                "status": contract.status,
                "sender_practice_id": contract.sender_practice_id,
                "receiver_practice_id": contract.receiver_practice_id,
                "transferred_object_ids": [ref.id for ref in contract.transferred_objects],
            },
        )
        stored, _ = self._record(obj, None, event)
        return stored

    def _ensure_no_participant_identifiable_object(self, contract: TransferContract) -> None:
        """MRR-NFR-006 / docs/spec/02_DOMAIN_MODEL.md section 4: reject a
        transfer that references any object classified
        ``PARTICIPANT_IDENTIFIABLE``, fail-closed, before anything is
        persisted.

        Only object KINDS that carry a top-level ``classification`` field on
        their own stored body (today: ``TaskBundle`` — see
        ``mrr.domain.exceptions.ParticipantIdentifiableTransferError``'s own
        docstring) can ever be flagged by this check; a referenced object
        that resolves to no stored object at all, or whose kind carries no
        ``classification`` field, is silently NOT rejected here — this
        mirrors ``mrr.services.correction.service.CorrectionImpactService.
        _require_review_if_needed``'s identical "no existence constraint on
        a referenced id" precedent (there is no foreign key from
        ``transferred_objects`` entries into ``objects``, any more than
        there is one from an edge's endpoints). This is a deliberate,
        narrower-than-maximal scope, flagged in the PR body: verifying that
        every referenced object actually EXISTS (beyond what is needed to
        read its classification) is not asked for by this task's acceptance
        tests or invariants.
        """
        for ref in contract.transferred_objects:
            try:
                referenced = self._object_repository.get_latest(ref.id)
            except ObjectNotFoundError:
                continue
            if referenced.body.get("classification") == "PARTICIPANT_IDENTIFIABLE":
                raise ParticipantIdentifiableTransferError(ref.id)

    # ------------------------------------------------------------------
    # Offer (MRR-FR-080: the sender's own act of proposing the transfer).
    # ADR-0007: EVENT-ONLY — no new content revision.
    # ------------------------------------------------------------------

    def offer(
        self, transfer_id: Urn, *, actor: Urn, policy_version: str, correlation_id: Urn
    ) -> TransferTransition:
        """``created -> offered``, recorded as a ``transfer.offered`` domain
        event. The content record (revision 1) is untouched.

        Raises:
            TransferContractNotFoundError: no stored contract for
                ``transfer_id``.
            InvalidTransitionError: the contract's current (event-derived)
                status is not ``"created"`` — in particular, calling
                ``offer`` a second time fails closed here (``offered ->
                offered`` is not a legal self-transition).
        """
        latest = _get_latest_or_raise(self._object_repository, transfer_id)
        current_status = _current_status(self._event_log, latest)
        TRANSFER_LIFECYCLE.assert_transition(current_status, "offered")

        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=_EVENT_OFFERED,
            occurred_at=datetime.now(UTC),
            actor=actor,
            policy_version=policy_version,
            causation_id=_last_event_id_for(self._event_log, transfer_id),
            correlation_id=correlation_id,
            object_id=transfer_id,
            object_revision=latest.revision,
            payload={"from_status": current_status, "to_status": "offered"},
        )
        appended = self._record_event(event)
        return TransferTransition(content=latest, status="offered", appended_event=appended)

    # ------------------------------------------------------------------
    # Respond (MRR-FR-081/082): the receiver's five-way decision. ADR-0007:
    # EVENT-ONLY for every outcome; "adapted" additionally records
    # adapted_from edge(s) atomically with the same event.
    # ------------------------------------------------------------------

    def respond(
        self,
        transfer_id: Urn,
        decision: TransferDecision,
        trusted_sender: Practice,
        *,
        adapted_object_id: Urn | None = None,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
        at: datetime | None = None,
    ) -> TransferTransition:
        """Record exactly one of ``{accepted, adapted, rejected, deferred,
        unresolved}`` as a ``transfer.responded`` domain event — after
        resolving the sender's trusted signing key
        (``mrr.domain.transfer_trust.resolve_trusted_transfer_key``,
        anchored to ``trusted_sender``) UNCONDITIONALLY, for every value of
        ``decision`` alike. See the module docstring's "respond: signature
        verified BEFORE recording ANY of the five outcomes" section.

        For ``decision == "adapted"``, ``adapted_object_id`` (an
        ALREADY-EXISTING local object, verified via
        ``ObjectRepository.get_latest``) is required; one ``adapted_from``
        edge (source=``adapted_object_id``, target=each transferred
        object's own id) is recorded atomically with the
        ``transfer.responded`` event. See the module docstring's "adapted:
        an atomic adapted_from edge" section.

        Args:
            transfer_id: the ``TransferContract`` being responded to.
            decision: one of ``TransferDecision``'s five values
                (MRR-FR-081). An unrecognized value fails closed via
                ``TRANSFER_LIFECYCLE.assert_transition`` (there is no
                declared ``offered -> <anything else>`` edge), after
                signature resolution has already run.
            trusted_sender: the practice the caller actually trusts as this
                contract's SENDER — caller-supplied, exactly as every prior
                trust resolver's own ``trusted_*_practice_id``/practice is.
                Its ``KeyRing`` is built internally via
                ``mrr.domain.manifest_trust.practice_key_ring``.
            adapted_object_id: required, and used, only when
                ``decision == "adapted"``; ignored otherwise.
            at: the evaluation instant for the resolver's
                validity-window check. Defaults to ``datetime.now(UTC)``,
                caller-overridable for deterministic testing.

        Raises:
            TransferContractNotFoundError: no stored contract for
                ``transfer_id`` — raised before any trust resolution is
                attempted.
            mrr.domain.exceptions.TransferSignerMismatchError,
            mrr.domain.exceptions.UnknownKeyIdError,
            mrr.domain.exceptions.TransferKeyNotValidError,
            mrr.crypto.exceptions.SignatureVerificationError,
            mrr.crypto.exceptions.UnsupportedAlgorithmError: trust
                resolution failed for the corresponding reason; nothing is
                recorded for ANY value of ``decision``.
            InvalidTransitionError: the contract's current (event-derived)
                status is not ``"offered"``, or ``decision`` names no
                declared edge from it (e.g. a second response after a
                terminal outcome already recorded).
            ValueError: ``decision == "adapted"`` and ``adapted_object_id``
                is ``None``.
            ObjectNotFoundError: ``decision == "adapted"`` and
                ``adapted_object_id`` resolves to no stored object at all —
                raised BEFORE any edge or event is recorded.
        """
        latest = _get_latest_or_raise(self._object_repository, transfer_id)
        contract = _reconstruct_contract(latest)
        ring = practice_key_ring(trusted_sender)
        resolve_trusted_transfer_key(contract, trusted_sender.id, ring, at=at)

        current_status = _current_status(self._event_log, latest)
        TRANSFER_LIFECYCLE.assert_transition(current_status, decision)

        if decision == "adapted":
            return self._respond_adapted(
                latest,
                contract,
                current_status,
                adapted_object_id,
                actor=actor,
                policy_version=policy_version,
                correlation_id=correlation_id,
            )

        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=_EVENT_RESPONDED,
            occurred_at=datetime.now(UTC),
            actor=actor,
            policy_version=policy_version,
            causation_id=_last_event_id_for(self._event_log, transfer_id),
            correlation_id=correlation_id,
            object_id=transfer_id,
            object_revision=latest.revision,
            payload={"from_status": current_status, "to_status": decision, "decision": decision},
        )
        appended = self._record_event(event)
        return TransferTransition(content=latest, status=decision, appended_event=appended)

    def _respond_adapted(
        self,
        latest: StoredObject,
        contract: TransferContract,
        current_status: str,
        adapted_object_id: Urn | None,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> TransferTransition:
        """The ``decision == "adapted"`` branch of ``respond`` — see that
        method's own docstring and the module docstring's "adapted: an
        atomic adapted_from edge" section. Raises ``ObjectNotFoundError``
        (propagated, unwrapped) if ``adapted_object_id`` does not resolve to
        a stored object — checked BEFORE any edge or event is built.
        """
        if adapted_object_id is None:
            raise ValueError('adapted_object_id is required when decision="adapted"')

        adapted_object = self._object_repository.get_latest(adapted_object_id)

        now = datetime.now(UTC)
        edges = [
            TypedEdge(
                id=new_urn("edge"),
                source_id=adapted_object_id,
                target_id=ref.id,
                edge_type=_ADAPTED_FROM_EDGE_TYPE,
                created_at=now,
                created_by=actor,
                scope=None,
                status="active",
                practice_id=adapted_object.practice_id,
            )
            for ref in contract.transferred_objects
        ]
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=_EVENT_RESPONDED,
            occurred_at=now,
            actor=actor,
            policy_version=policy_version,
            causation_id=_last_event_id_for(self._event_log, latest.id),
            correlation_id=correlation_id,
            object_id=latest.id,
            object_revision=latest.revision,
            payload={
                "from_status": current_status,
                "to_status": "adapted",
                "decision": "adapted",
                "adapted_object_id": adapted_object_id,
                "adapted_from_edge_ids": [edge.id for edge in edges],
                "transferred_object_ids": [ref.id for ref in contract.transferred_objects],
            },
        )
        _, appended_event = self._record_edges(edges, event)
        return TransferTransition(content=latest, status="adapted", appended_event=appended_event)
