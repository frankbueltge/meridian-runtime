"""``TaskBundleService`` and ``NodeTaskDecisionService`` (task-packets/
E2-T03.yaml): creation, offer, and the target node's authoritative decision
on signed ``TaskBundle`` objects, up to but not including execution
(QUEUED/RUNNING/COMPLETED — E2-T04) or evidence-crate sealing (E2-T06).

Two roles, two classes, deliberately separated (docs/spec/01_SYSTEM_SPEC.md
MRR-FR-022: "The target node MUST make the authoritative accept, modify,
defer, or reject decision"):

- ``TaskBundleService`` — the ORIGIN's operations: ``create`` (gated on the
  E2-T01 research-score approval and the E2-T02 capability-declaration
  check), ``offer`` (CREATED -> OFFERED), and ``accept_modification`` (the
  origin's acknowledgement of a node-proposed revision — see its own
  docstring for why this is not a state transition). There is **no**
  accept-to-ACCEPTED method here, by construction, not by convention — see
  ``test_origin_service_has_no_accept_method`` in the unit tests, which
  asserts this against the class's own public API surface.
- ``NodeTaskDecisionService`` — the TARGET NODE's operations: ``accept``,
  ``propose_modification``, ``defer``, ``reject``. Every one of these takes
  a caller-supplied ``deciding_node_id`` and raises ``NodeAuthorityError``
  before doing anything else (including before the signature check) unless
  it equals the bundle's own ``target_node_id`` — MRR-FR-022 enforced
  structurally, in-process, for this single-node slice: there is no HTTP
  layer yet to authenticate a remote caller's identity, so the identity
  arrives as an explicit parameter and is checked against the stored
  bundle's declared target on every call. A future HTTP-facing task (outside
  this packet's ``forbidden_changes``: "HTTP/FastAPI endpoints") is
  responsible for deriving ``deciding_node_id`` from an authenticated
  transport identity rather than a bare parameter; that does not change
  this structural check, only who is trusted to populate it.

--- status is now a native TaskBundle field (ADR-0005) -----------------------

``schemas/task-bundle.schema.json`` originally had no ``status``/lifecycle
field at all (unlike ``ResearchScore``), which the first version of this
service worked around by stashing workflow status as an out-of-band key on
the persisted ``StoredObject.body``, alongside the real TaskBundle schema
fields. Review caught that this made ``stored.body`` fail to round-trip
through ``TaskBundle.model_validate`` (an unrecognized key, rejected by
``MRRModel``'s ``extra="forbid"`` / the schema's own
``unevaluatedProperties: false``) — a real defect, not just a style
preference. ADR-0005 (accepted) closed the gap the right way, upstream of
this service: ``status`` (the fourteen ``TASK_BUNDLE_LIFECYCLE`` states,
``TaskBundleStatus``) is now a required, schema-validated ``TaskBundle``
field, exactly like ``ResearchScore``'s. This module was rewritten on top of
that: ``StoredObject.body`` is now a plain
``bundle.model_dump_json(exclude_none=True)`` with **no** added key, and
``TaskBundle.model_validate(stored.body)`` succeeds unconditionally (asserted
directly by ``test_persisted_body_round_trips_through_task_bundle_model_validate``
in the unit tests).

--- TaskBundle.revision and the store revision now stay in lockstep ----------

Because ``status`` is a real top-level field, every workflow transition
— even a pure status flip such as ``offer`` — is now genuinely "a new
revision" in exactly the sense ``ResearchScoreService._transition`` already
uses: the whole object (including the new ``status``) is re-serialized and
its ``content_hash`` recomputed (``mrr.domain.hashing_policy.
compute_content_hash``), and ``TaskBundle.revision`` is incremented to match.
``mrr.persistence.repositories.PostgresObjectRepository.insert_revision``
requires a strictly-incrementing ``StoredObject.revision`` on every insert
regardless, so there is no longer any reason (and, given the round-trip
requirement above, no longer any room) for the store's row-revision counter
to diverge from ``TaskBundle.revision`` the way the pre-ADR-0005 version of
this module did — they are one and the same number now, exactly as they
already are for ``ResearchScore``/``NodeManifest``.

--- Verifying a signature that is NOT re-minted on every status flip ---------

``TaskBundle.signature`` is **not** re-signed on a pure status transition —
nobody holds a private key inside this service, and re-signing on every
workflow step (just to keep pace with a status flip) would defeat the point
of MRR-FR-034 tying "a new content hash and signature" specifically to a
genuine content revision (``propose_modification``), not to negotiation
bookkeeping. ``signature.value`` is therefore carried forward unchanged
across ``offer``/``accept``/``defer``/``reject``/``accept_modification``,
while ``content_hash``/``revision`` advance underneath it (see above) —
which means the *current* stored revision's own ``content_hash`` does not,
in general, match what a fresh ``verify_object_signature`` call against its
*current* fields would need (the signed payload's ``status`` differs from
whatever ``status`` was in effect when ``signature.value`` was actually
produced). Verifying "the origin signature" (MRR-FR-031: "verified on the
node side before any decision") therefore does not target ``latest`` at
all — it targets the **oldest stored revision that already carries the same
``signature.value`` as `latest``** (``_find_signed_revision``, a linear scan
over ``ObjectRepository.list_revisions`` comparing ``signature.value``,
oldest-first): that revision's own fields (its own ``status`` — ``CREATED``
for the origin's first signature, or ``OFFERED`` for a modifier's, since
``propose_modification`` always signs the bundle it is about to land in
``OFFERED`` with) are exactly the bytes that were actually signed, and
verifying against them is what actually succeeds for an honestly-signed
bundle and actually fails for a tampered one. This is the load-bearing
adaptation this rewrite makes to keep MRR-FR-031 meaningful now that
``status`` participates in the hashed/signed payload; flagged for reviewer
scrutiny alongside the FR-022 authority split.

--- propose_modification is one write, not two -------------------------------

``TASK_BUNDLE_LIFECYCLE`` draws ``OFFERED -> MODIFICATION_PROPOSED ->
OFFERED`` as two edges. The first version of this module persisted a
separate ``StoredObject`` row for the transient ``MODIFICATION_PROPOSED``
state before the modifier's genuinely new content. With ``TaskBundle.
revision`` and the store revision now required to stay in lockstep, doing
that would force the modifying node to sign its counter-proposal with
``latest.revision + 2`` (one consumed by the transient marker row, one for
its own content) instead of the natural, ``ResearchScoreService.revise``-
style ``latest.revision + 1`` — an implementation detail of *this* service
leaking into what a remote node must predict before it can even construct
the bytes it signs. Instead, ``propose_modification`` here validates **both**
edges are lifecycle-legal (two ``TASK_BUNDLE_LIFECYCLE.assert_transition``
calls — the second, ``MODIFICATION_PROPOSED -> OFFERED``, is unconditionally
legal, so in practice this reduces to requiring ``latest``'s status be
``OFFERED``) but persists exactly **one** new revision — the modifier's own
signed content, landing directly in ``OFFERED`` at ``latest.revision + 1`` —
plus one ``task_bundle.modification_offered`` event whose payload documents
that it passed through the (unmaterialized) ``MODIFICATION_PROPOSED`` state.
The prior revision(s) are, as before, left completely untouched.

--- Refusal reason vocabulary -------------------------------------------------

``RefusalReason`` below is **not** defined anywhere in ``schemas/`` (only
``correction-event.schema.json`` has a "reason" property, and it is a free
string, not this concept) — docs/spec/04_SECURITY_AND_POLICY.md section 8.3
("Refusal safety") only says a refusal "may return a coarse reason code",
without naming the set. The five values below are this task's own minimal,
coarse proposal (task-packets/E2-T03.yaml derived_decisions), not a
specification-derived vocabulary, and are flagged in the PR as an open
specification question needing ratification.

--- Cancel (not implemented here) ---------------------------------------------

``TASK_BUNDLE_LIFECYCLE`` (``mrr.domain.lifecycles``) draws exactly one edge
into ``CANCELLED``: ``QUEUED -> CANCELLED``. ``QUEUED`` only exists on the
execution side (E2-T04, this packet's own ``forbidden_changes``). No edge
into ``CANCELLED`` is drawn from any pre-execution state (``CREATED``,
``OFFERED``, ``ACCEPTED``, ``MODIFICATION_PROPOSED``, ``DEFERRED``,
``REJECTED``), so there is no lifecycle-legal cancel operation this task can
implement without inventing an undrawn edge (task-packets/E1-T04.yaml's own
rule, which this packet's own instructions reaffirm: "only lifecycle-legal
pre-execution edges ... If a needed cancel edge isn't drawn pre-execution,
don't invent it"). Neither service class below has a ``cancel`` method.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, get_args

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from mrr.contracts import TaskBundle, Urn
from mrr.domain.exceptions import (
    CapabilityNotDeclaredError,
    InvalidTransitionError,
    NodeAuthorityError,
    ObjectNotFoundError,
    TaskBundleNotFoundError,
)
from mrr.domain.hashing_policy import compute_content_hash, verify_object_signature
from mrr.domain.identity import new_urn
from mrr.domain.lifecycles import TASK_BUNDLE_LIFECYCLE
from mrr.domain.repositories import ObjectRepository, StoredObject
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.persistence.unit_of_work import record_object_revision_with_event
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.capability_registry.service import CapabilityRegistry
from mrr.services.research_score.service import ResearchScoreService
from sqlalchemy import Engine

#: Sentinel "from" state used only when reporting ``InvalidTransitionError``
#: for ``create()`` with a non-CREATED initial status — the exact pattern
#: ``mrr.services.research_score.service.ResearchScoreService.create`` uses
#: (there is no real "from" state for a brand-new object). Never a member of
#: ``TASK_BUNDLE_LIFECYCLE.states``, so it can never appear as a legal
#: transition source.
_NEW_BUNDLE_SENTINEL_STATE = "<new>"

#: docs/spec/01_SYSTEM_SPEC.md MRR-FR-024's refusal event type, and the other
#: five event types this module writes. Dot-separated, matching
#: ``node_manifest.registered``/``research_score.*``'s existing convention.
_EVENT_CREATED = "task_bundle.created"
_EVENT_OFFERED = "task_bundle.offered"
_EVENT_MODIFICATION_ACKNOWLEDGED = "task_bundle.modification_acknowledged"
_EVENT_ACCEPTED = "task_bundle.accepted"
_EVENT_DEFERRED = "task_bundle.deferred"
_EVENT_REJECTED = "task_bundle.rejected"
_EVENT_MODIFICATION_OFFERED = "task_bundle.modification_offered"

#: See the module docstring's "Refusal reason vocabulary" section: a minimal,
#: coarse, NOT spec-defined set (docs/spec/04_SECURITY_AND_POLICY.md section
#: 8.3, "a coarse reason code"), flagged as an open specification question.
RefusalReason = Literal[
    "capability_unavailable",
    "policy_declined",
    "resource_unavailable",
    "data_access_denied",
    "other",
]

_REFUSAL_REASONS: frozenset[str] = frozenset(get_args(RefusalReason))

#: The callable shape ``mrr.persistence.unit_of_work.record_object_revision_with_event``
#: takes once its ``engine``/``object_repository``/``event_log`` arguments
#: are bound. Identical in shape to
#: ``mrr.services.research_score.service.RecordRevisionWithEvent`` /
#: ``mrr.services.capability_registry.service.RecordRevisionWithEvent`` —
#: see those modules' docstrings for why this is a local copy, not a shared
#: import, across *separate service modules*. Within *this* module, however,
#: both ``TaskBundleService`` and ``NodeTaskDecisionService`` share the one
#: copy below via module-level helper functions (``_advance`` etc.) rather
#: than each duplicating it again — they operate on the very same kind of
#: object in the very same file, so the "local copy per service module"
#: convention's purpose (not coupling two independently evolving services)
#: does not apply between them the way it does across E2-T01/T02/T03.
RecordRevisionWithEvent = Callable[
    [StoredObject, int | None, DomainEvent], tuple[StoredObject, AppendedEvent]
]


class _EventJournal(Protocol):
    """The one read operation this module needs from an event log. Same
    shape and rationale as ``mrr.services.research_score.service._EventJournal``
    / ``mrr.services.capability_registry.service._EventJournal``.
    """

    def read_all(self) -> list[AppendedEvent]: ...


def bind_unit_of_work(
    engine: Engine,
    object_repository: PostgresObjectRepository,
    event_log: PostgresEventLog,
) -> RecordRevisionWithEvent:
    """Bind ``record_object_revision_with_event`` to a concrete
    ``sqlalchemy.Engine``/``PostgresObjectRepository``/``PostgresEventLog``
    triple. Production wiring and integration tests call this once each for
    ``TaskBundleService`` and ``NodeTaskDecisionService`` (they may safely
    share the same bound callable, since both ultimately write the same
    ``objects``/``domain_events`` tables); DB-free unit tests pass their own
    trivial callable of the same shape, backed by in-memory fakes, instead.
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


# ---------------------------------------------------------------------------
# Shared internals — see the module docstring for why these are shared
# module-level functions rather than duplicated per class or lifted onto a
# common base class (which would blur the deliberate origin/node API split).
# ---------------------------------------------------------------------------


def _bundle_to_stored_object(bundle: TaskBundle) -> StoredObject:
    """Convert ``bundle`` (already schema-valid, including its native
    ``status`` field — ADR-0005) into the generic ``StoredObject``
    ``ObjectRepository`` persists. ``body`` is a plain
    ``model_dump_json(exclude_none=True))`` — no added keys — so
    ``TaskBundle.model_validate(stored.body)`` always succeeds, matching
    ``mrr.services.research_score.service._score_to_stored_object`` and
    ``mrr.services.capability_registry.service._manifest_to_stored_object``'s
    own pattern exactly.
    """
    body: dict[str, Any] = json.loads(bundle.model_dump_json(exclude_none=True))
    return StoredObject(
        id=bundle.id,
        api_version=bundle.api_version,
        kind=bundle.kind,
        practice_id=bundle.practice_id,
        revision=bundle.revision,
        created_at=bundle.created_at,
        created_by=bundle.created_by,
        content_hash=bundle.content_hash,
        supersedes=bundle.supersedes,
        labels=bundle.labels,
        body=body,
    )


def _reconstruct_bundle(stored: StoredObject) -> TaskBundle:
    """The inverse of ``_bundle_to_stored_object``: ``stored.body`` is
    already a plain, schema-valid ``TaskBundle`` serialization (ADR-0005), so
    this is a direct ``model_validate`` — no stripping needed anymore.
    """
    return TaskBundle.model_validate(stored.body)


def _bundle_with_new_status(bundle: TaskBundle, *, to_status: str, new_revision: int) -> TaskBundle:
    """Build the next pure-status-transition revision of ``bundle``: same
    substantive content, ``status`` and ``revision`` advanced, and
    ``content_hash`` recomputed to match (mirrors ``ResearchScoreService.
    _transition``'s "only status changes, everything else carried over,
    content_hash recomputed" pattern). ``signature`` is carried forward
    UNCHANGED — see the module docstring's "Verifying a signature that is
    NOT re-minted on every status flip" section for why, and for how
    verification is adapted to that.
    """
    updated = bundle.model_copy(update={"revision": new_revision, "status": to_status})
    body = json.loads(updated.model_dump_json(exclude_none=True))
    new_content_hash = compute_content_hash(body)
    return updated.model_copy(update={"content_hash": new_content_hash})


def _get_latest_or_raise(object_repository: ObjectRepository, bundle_id: str) -> StoredObject:
    try:
        return object_repository.get_latest(bundle_id)
    except ObjectNotFoundError:
        raise TaskBundleNotFoundError(bundle_id) from None


def _find_signed_revision(
    object_repository: ObjectRepository, bundle_id: str, latest: StoredObject
) -> StoredObject:
    """Return the OLDEST stored revision whose ``signature.value`` equals
    ``latest``'s — i.e. the revision whose fields are the exact bytes that
    were actually signed, since pure status transitions carry ``signature``
    forward unchanged while ``content_hash``/``revision``/``status`` advance
    underneath it (module docstring). Verification must target THIS
    revision's own fields (its own ``status`` — ``CREATED`` for the origin's
    first signature, ``OFFERED`` for a modifier's), not ``latest``'s current
    ones. ``list_revisions`` returns oldest-first, so the first match is the
    revision where this exact signature was first introduced.
    """
    current_signature_value = latest.body["signature"]["value"]
    for revision in object_repository.list_revisions(bundle_id):
        if revision.body["signature"]["value"] == current_signature_value:
            return revision
    return latest  # pragma: no cover - defensive only; latest always matches itself


def _last_event_id_for(event_log: _EventJournal, bundle_id: str) -> str | None:
    """The id of the most recently appended event for ``bundle_id``, or
    ``None`` if there is none yet — the ``causation_id`` for the next event
    in that bundle's own causal chain (MRR-NFR-001). Identical logic to
    ``ResearchScoreService``/``CapabilityRegistry``'s own
    ``_last_event_id_for``: ``read_all()`` returns events oldest-first, so
    the last match is the most recent.
    """
    matching_ids = [
        appended.event.id
        for appended in event_log.read_all()
        if appended.event.object_id == bundle_id
    ]
    return matching_ids[-1] if matching_ids else None


def _advance(
    event_log: _EventJournal,
    record: RecordRevisionWithEvent,
    latest: StoredObject,
    new_bundle: TaskBundle,
    *,
    event_type: str,
    actor: Urn,
    policy_version: str,
    correlation_id: Urn,
    payload: dict[str, Any] | None = None,
) -> StoredObject:
    """Shared implementation for every simple ``TASK_BUNDLE_LIFECYCLE`` edge
    this module drives (``offer``/``accept``/``defer``/``reject`` — every
    case where ``from_status != to_status``): assert the transition is legal
    (fails closed with ``InvalidTransitionError`` and writes nothing —
    checked before any ``StoredObject``/``DomainEvent`` is even
    constructed), then persist ``new_bundle`` (already carrying the new
    ``status``/``revision``/``content_hash`` — see
    ``_bundle_with_new_status``) plus its event atomically.

    Not used by ``accept_modification`` (its "transition" is ``OFFERED ->
    OFFERED``, not a legal edge — see that method) or by
    ``propose_modification`` (whose overall move is also ``OFFERED ->
    OFFERED`` at the storage level, passing logically but not physically
    through ``MODIFICATION_PROPOSED`` — see the module docstring's
    "propose_modification is one write, not two" section); both call
    ``TASK_BUNDLE_LIFECYCLE.assert_transition`` themselves as needed and
    build their ``DomainEvent`` directly.
    """
    from_status = latest.body["status"]
    to_status = new_bundle.status
    TASK_BUNDLE_LIFECYCLE.assert_transition(from_status, to_status)

    obj = _bundle_to_stored_object(new_bundle)
    event = DomainEvent(
        id=new_urn("domain-event"),
        event_type=event_type,
        occurred_at=datetime.now(UTC),
        actor=actor,
        policy_version=policy_version,
        causation_id=_last_event_id_for(event_log, latest.id),
        correlation_id=correlation_id,
        object_id=latest.id,
        object_revision=new_bundle.revision,
        payload=payload
        if payload is not None
        else {"from_status": from_status, "to_status": to_status},
    )
    stored, _ = record(obj, latest.revision, event)
    return stored


# ---------------------------------------------------------------------------
# TaskBundleService — the ORIGIN's operations.
# ---------------------------------------------------------------------------


class TaskBundleService:
    """docs/spec/01_SYSTEM_SPEC.md section 7.2/7.5 (the orchestrator/origin
    side of task-bundle negotiation), implemented per task-packets/
    E2-T03.yaml. Owns exactly ``create``, ``offer``, and
    ``accept_modification`` — no method here can move a bundle to
    ``ACCEPTED``; that is ``NodeTaskDecisionService.accept``'s sole authority
    (MRR-FR-022).
    """

    def __init__(
        self,
        object_repository: ObjectRepository,
        event_log: _EventJournal,
        record: RecordRevisionWithEvent,
        research_score_service: ResearchScoreService,
        capability_registry: CapabilityRegistry,
    ) -> None:
        self._object_repository = object_repository
        self._event_log = event_log
        self._record = record
        self._research_score_service = research_score_service
        self._capability_registry = capability_registry

    # ------------------------------------------------------------------
    # Creation (MRR-FR-030/031/032).
    # ------------------------------------------------------------------

    def create(
        self,
        bundle: TaskBundle,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """Persist ``bundle`` as revision 1 in status ``CREATED``, plus a
        ``task_bundle.created`` event, atomically — after two fail-closed
        gates, both reused as-is from prior epics, run in this order:

        1. ``ResearchScoreService.ensure_can_start_work(bundle.research_score_id)``
           (E2-T01, MRR-FR-004) — raises ``ScoreNotFoundError``/
           ``ScoreNotApprovedError`` if the referenced score is missing or
           not APPROVED/ACTIVE. Nothing is written if this raises.
        2. ``CapabilityRegistry.get_current_manifest(bundle.target_node_id)``
           (E2-T02) plus a capability-membership check —
           ``NodeManifestNotFoundError``/``NodeManifestValidityError``
           propagate unmodified if the target node has no current manifest
           at all; ``CapabilityNotDeclaredError`` (new, this task) if a
           current manifest exists but does not declare the exact
           ``{name, version}`` pair ``bundle.capability`` names. This is
           declaration, not permission (docs/spec/01_SYSTEM_SPEC.md section
           7.3) — no policy, approval-mode, or classification check happens
           here.

        ``bundle`` must already be a fully valid, origin-signed
        ``TaskBundle`` — its own ``id``/``content_hash``/``signature`` are
        minted by the caller (this service does not sign or hash on the
        caller's behalf, matching E2-T01/T02's own "caller mints id/hash/
        signature" convention). ``bundle.revision`` must be ``1`` and
        ``bundle.status`` must be ``TASK_BUNDLE_LIFECYCLE.initial_state``
        (``"CREATED"``) — now a real, schema-defined field (ADR-0005), this
        mirrors ``ResearchScoreService.create``'s own "reject non-DRAFT
        initial status" guard exactly, via the same sentinel-transition
        technique.

        Raises:
            InvalidTransitionError: ``bundle.status`` is not ``"CREATED"``.
            ValueError: ``bundle.revision`` is not ``1``.
        """
        if bundle.status != TASK_BUNDLE_LIFECYCLE.initial_state:
            raise InvalidTransitionError(
                TASK_BUNDLE_LIFECYCLE.name, _NEW_BUNDLE_SENTINEL_STATE, bundle.status
            )
        if bundle.revision != 1:
            raise ValueError(f"TaskBundle.revision must be 1 for create(), got {bundle.revision!r}")

        self._research_score_service.ensure_can_start_work(bundle.research_score_id)
        self._ensure_capability_declared(bundle)

        obj = _bundle_to_stored_object(bundle)
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=_EVENT_CREATED,
            occurred_at=datetime.now(UTC),
            actor=actor,
            policy_version=policy_version,
            causation_id=None,
            correlation_id=correlation_id,
            object_id=bundle.id,
            object_revision=1,
            payload={
                "status": bundle.status,
                "target_node_id": bundle.target_node_id,
                "capability_name": bundle.capability.name,
                "capability_version": bundle.capability.version,
            },
        )
        stored, _ = self._record(obj, None, event)
        return stored

    def _ensure_capability_declared(self, bundle: TaskBundle) -> None:
        manifest = self._capability_registry.get_current_manifest(bundle.target_node_id)
        declared = any(
            capability["name"] == bundle.capability.name
            and capability["version"] == bundle.capability.version
            for capability in manifest.body["capabilities"]
        )
        if not declared:
            raise CapabilityNotDeclaredError(
                bundle.target_node_id, bundle.capability.name, bundle.capability.version
            )

    # ------------------------------------------------------------------
    # Offer (MRR-FR-021: matching, not permission — the origin still has to
    # explicitly offer before the node can decide).
    # ------------------------------------------------------------------

    def offer(
        self, bundle_id: Urn, *, actor: Urn, policy_version: str, correlation_id: Urn
    ) -> StoredObject:
        """CREATED -> OFFERED."""
        latest = _get_latest_or_raise(self._object_repository, bundle_id)
        bundle = _reconstruct_bundle(latest)
        new_bundle = _bundle_with_new_status(
            bundle, to_status="OFFERED", new_revision=latest.revision + 1
        )
        return _advance(
            self._event_log,
            self._record,
            latest,
            new_bundle,
            event_type=_EVENT_OFFERED,
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
        )

    # ------------------------------------------------------------------
    # The origin's acknowledgement of a node-proposed modification
    # (MRR-FR-023: "... explicitly accepted by the origin before
    # execution").
    # ------------------------------------------------------------------

    def accept_modification(
        self, bundle_id: Urn, *, actor: Urn, policy_version: str, correlation_id: Urn
    ) -> StoredObject:
        """Record the origin's explicit acknowledgement that the current
        ``OFFERED`` revision (following a node's ``propose_modification``)
        exists and stands.

        This is deliberately **not** a ``TASK_BUNDLE_LIFECYCLE`` state
        transition: the bundle is already ``OFFERED`` (``propose_modification``
        lands there), and ``OFFERED -> OFFERED`` is not a drawn edge
        (self-transitions are never legal, per
        ``mrr.domain.lifecycles.StateMachine``). The node still holds the
        sole authority to move the bundle to ``ACCEPTED``
        (``NodeTaskDecisionService.accept``, MRR-FR-022) — this method
        cannot and does not grant the origin any such path; it only records
        a ``task_bundle.modification_acknowledged`` event, still requiring a
        new store revision (every write here does, in lockstep — see the
        module docstring) whose ``status`` is unchanged (``"OFFERED"``) and
        whose substantive content is otherwise identical to ``latest``.

        Raises:
            InvalidTransitionError: if the bundle's current status is not
                ``OFFERED`` — reused here (rather than a new error type) to
                report "you cannot acknowledge a modification of a bundle
                that is not currently offered", carrying the actual status
                as ``from_state`` and ``"OFFERED"`` as ``to_state``.
        """
        latest = _get_latest_or_raise(self._object_repository, bundle_id)
        bundle = _reconstruct_bundle(latest)
        if bundle.status != "OFFERED":
            raise InvalidTransitionError(TASK_BUNDLE_LIFECYCLE.name, bundle.status, "OFFERED")

        new_revision = latest.revision + 1
        new_bundle = _bundle_with_new_status(bundle, to_status="OFFERED", new_revision=new_revision)
        obj = _bundle_to_stored_object(new_bundle)
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=_EVENT_MODIFICATION_ACKNOWLEDGED,
            occurred_at=datetime.now(UTC),
            actor=actor,
            policy_version=policy_version,
            causation_id=_last_event_id_for(self._event_log, bundle_id),
            correlation_id=correlation_id,
            object_id=bundle_id,
            object_revision=new_revision,
            payload={"status": "OFFERED", "acknowledged_content_revision": bundle.revision},
        )
        stored, _ = self._record(obj, latest.revision, event)
        return stored


# ---------------------------------------------------------------------------
# NodeTaskDecisionService — the TARGET NODE's operations (MRR-FR-022).
# ---------------------------------------------------------------------------


class NodeTaskDecisionService:
    """docs/spec/01_SYSTEM_SPEC.md section 7.5 ("Node Runtime: Authenticates
    task bundles, performs local policy evaluation, ...") negotiation-side
    responsibilities, implemented per task-packets/E2-T03.yaml. The sole
    authority for ``accept``/``propose_modification``/``defer``/``reject`` —
    ``TaskBundleService`` has no equivalent method (MRR-FR-022).
    """

    def __init__(
        self,
        object_repository: ObjectRepository,
        event_log: _EventJournal,
        record: RecordRevisionWithEvent,
    ) -> None:
        self._object_repository = object_repository
        self._event_log = event_log
        self._record = record

    # ------------------------------------------------------------------
    # OFFERED -> ACCEPTED.
    # ------------------------------------------------------------------

    def accept(
        self,
        bundle_id: Urn,
        deciding_node_id: Urn,
        verifying_key: Ed25519PublicKey,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """OFFERED -> ACCEPTED, after verifying ``deciding_node_id`` is the
        bundle's own ``target_node_id`` and the signature verifies
        (MRR-FR-022, MRR-FR-031) — both fail closed, before any decision.
        See ``_authorize_and_verify``.
        """
        latest, bundle = self._authorize_and_verify(bundle_id, deciding_node_id, verifying_key)
        new_bundle = _bundle_with_new_status(
            bundle, to_status="ACCEPTED", new_revision=latest.revision + 1
        )
        return _advance(
            self._event_log,
            self._record,
            latest,
            new_bundle,
            event_type=_EVENT_ACCEPTED,
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
        )

    # ------------------------------------------------------------------
    # OFFERED -> DEFERRED.
    # ------------------------------------------------------------------

    def defer(
        self,
        bundle_id: Urn,
        deciding_node_id: Urn,
        verifying_key: Ed25519PublicKey,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """OFFERED -> DEFERRED."""
        latest, bundle = self._authorize_and_verify(bundle_id, deciding_node_id, verifying_key)
        new_bundle = _bundle_with_new_status(
            bundle, to_status="DEFERRED", new_revision=latest.revision + 1
        )
        return _advance(
            self._event_log,
            self._record,
            latest,
            new_bundle,
            event_type=_EVENT_DEFERRED,
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
        )

    # ------------------------------------------------------------------
    # OFFERED -> REJECTED (MRR-FR-024: a refusal event with a reason
    # category).
    # ------------------------------------------------------------------

    def reject(
        self,
        bundle_id: Urn,
        deciding_node_id: Urn,
        verifying_key: Ed25519PublicKey,
        *,
        reason_category: RefusalReason,
        explanation: str | None = None,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """OFFERED -> REJECTED, recording a ``task_bundle.rejected`` event
        carrying ``reason_category`` (docs/spec/04_SECURITY_AND_POLICY.md
        section 8.3: "a coarse reason code") and the optional human-readable
        ``explanation`` (MRR-FR-024). See the module docstring's "Refusal
        reason vocabulary" section — ``RefusalReason`` is this task's own
        minimal proposal, not schema- or spec-defined.

        Raises:
            ValueError: ``reason_category`` is not one of ``RefusalReason``'s
                members. Checked before the authority/signature checks below,
                so a malformed reason fails closed without even touching the
                object repository.
        """
        if reason_category not in _REFUSAL_REASONS:
            raise ValueError(
                f"reason_category must be one of {sorted(_REFUSAL_REASONS)!r}, "
                f"got {reason_category!r}"
            )

        latest, bundle = self._authorize_and_verify(bundle_id, deciding_node_id, verifying_key)
        new_bundle = _bundle_with_new_status(
            bundle, to_status="REJECTED", new_revision=latest.revision + 1
        )
        payload: dict[str, Any] = {
            "from_status": bundle.status,
            "to_status": "REJECTED",
            "reason_category": reason_category,
        }
        if explanation is not None:
            payload["explanation"] = explanation
        return _advance(
            self._event_log,
            self._record,
            latest,
            new_bundle,
            event_type=_EVENT_REJECTED,
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            payload=payload,
        )

    # ------------------------------------------------------------------
    # OFFERED -> MODIFICATION_PROPOSED -> OFFERED (MRR-FR-023/034: a new
    # signed revision, returned to OFFERED for the origin). See the module
    # docstring's "propose_modification is one write, not two" section.
    # ------------------------------------------------------------------

    def propose_modification(
        self,
        bundle_id: Urn,
        modified_bundle: TaskBundle,
        deciding_node_id: Urn,
        verifying_key: Ed25519PublicKey,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """Validate that both drawn edges (``OFFERED -> MODIFICATION_PROPOSED``
        and ``MODIFICATION_PROPOSED -> OFFERED``) are lifecycle-legal, then
        persist ``modified_bundle`` — the modifier's own new signed content,
        already carrying its own new ``content_hash``/``signature`` (minted
        by ``deciding_node_id``, not by this service — same "caller mints
        hash/signature" convention as everywhere else in this codebase) and
        its own ``status="OFFERED"`` — as exactly one new revision, plus one
        ``task_bundle.modification_offered`` event. The prior revision(s) —
        including the original revision the origin signed — are left
        completely untouched; only one new row is ever inserted.

        Raises:
            NodeAuthorityError / mrr.crypto.exceptions.SignatureVerificationError:
                see ``_authorize_and_verify`` — checked before anything else.
            InvalidTransitionError: the bundle's current status is not
                ``OFFERED`` (the only status from which
                ``MODIFICATION_PROPOSED`` is reachable).
            ValueError: ``modified_bundle.id`` does not match ``bundle_id``,
                ``modified_bundle.status`` is not ``"OFFERED"``,
                ``modified_bundle.revision`` is not exactly the current
                revision + 1, or ``modified_bundle.content_hash`` equals the
                current revision's hash unchanged (MRR-FR-034 requires a
                genuinely new content hash for "a task revision"). Checked
                after authorization/signature but before the write, so a
                malformed proposed revision fails closed with nothing
                persisted.
        """
        latest, bundle = self._authorize_and_verify(bundle_id, deciding_node_id, verifying_key)

        TASK_BUNDLE_LIFECYCLE.assert_transition(bundle.status, "MODIFICATION_PROPOSED")
        TASK_BUNDLE_LIFECYCLE.assert_transition("MODIFICATION_PROPOSED", "OFFERED")

        if modified_bundle.id != bundle.id:
            raise ValueError(
                f"modified_bundle.id ({modified_bundle.id!r}) must match the bundle being "
                f"modified ({bundle.id!r})"
            )
        if modified_bundle.status != "OFFERED":
            raise ValueError(
                f"modified_bundle.status must be 'OFFERED' (the state a proposed "
                f"modification lands in), got {modified_bundle.status!r}"
            )
        expected_revision = bundle.revision + 1
        if modified_bundle.revision != expected_revision:
            raise ValueError(
                f"modified_bundle.revision must be {expected_revision!r} "
                f"(current revision + 1), got {modified_bundle.revision!r}"
            )
        if modified_bundle.content_hash == bundle.content_hash:
            raise ValueError(
                "modified_bundle must carry a new content_hash (MRR-FR-034) — got the "
                "unchanged prior hash"
            )

        obj = _bundle_to_stored_object(modified_bundle)
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=_EVENT_MODIFICATION_OFFERED,
            occurred_at=datetime.now(UTC),
            actor=actor,
            policy_version=policy_version,
            causation_id=_last_event_id_for(self._event_log, bundle_id),
            correlation_id=correlation_id,
            object_id=bundle_id,
            object_revision=modified_bundle.revision,
            payload={
                "from_status": bundle.status,
                "via": "MODIFICATION_PROPOSED",
                "to_status": "OFFERED",
                "content_revision": modified_bundle.revision,
            },
        )
        stored, _ = self._record(obj, latest.revision, event)
        return stored

    # ------------------------------------------------------------------
    # Internal helpers.
    # ------------------------------------------------------------------

    def _authorize_and_verify(
        self, bundle_id: Urn, deciding_node_id: Urn, verifying_key: Ed25519PublicKey
    ) -> tuple[StoredObject, TaskBundle]:
        """Load the latest stored revision, enforce MRR-FR-022 (structural
        node-authority check: ``deciding_node_id`` must equal the stored
        bundle's ``target_node_id``), then verify the signature that is
        actually still valid for THIS bundle (MRR-FR-031) — both fail
        closed, both before any decision or persistence. The authority check
        runs first (cheap, no cryptography) so an unauthorized caller learns
        nothing about whether the bundle's signature is even valid.

        Verification does not target ``latest`` directly — see the module
        docstring's "Verifying a signature that is NOT re-minted on every
        status flip" section for why — it targets
        ``_find_signed_revision(latest)``, the oldest stored revision
        carrying the same ``signature.value`` as ``latest``, which is
        exactly the payload that was actually signed.

        Returns ``(latest, bundle)`` — ``latest`` the current stored
        revision, ``bundle`` its reconstructed ``TaskBundle`` (current
        ``status``, ready for a caller to build the next transition from).

        Raises:
            TaskBundleNotFoundError: no stored bundle for ``bundle_id``.
            NodeAuthorityError: ``deciding_node_id`` is not the bundle's
                ``target_node_id``. Nothing has been read from the bundle's
                content or verified at this point beyond ``target_node_id``
                itself.
            mrr.crypto.exceptions.SignatureVerificationError /
            UnsupportedAlgorithmError: the signature does not verify under
                ``verifying_key``.
        """
        latest = _get_latest_or_raise(self._object_repository, bundle_id)
        target_node_id = latest.body["target_node_id"]
        if deciding_node_id != target_node_id:
            raise NodeAuthorityError(bundle_id, target_node_id, deciding_node_id)

        signed_revision = _find_signed_revision(self._object_repository, bundle_id, latest)
        signed_bundle = _reconstruct_bundle(signed_revision)
        verify_object_signature(
            verifying_key,
            signed_bundle.model_dump(mode="json"),
            signed_bundle.signature.value,
            algorithm=signed_bundle.signature.algorithm,
        )

        bundle = _reconstruct_bundle(latest)
        return latest, bundle
