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

Every one of ``NodeTaskDecisionService``'s four methods also verifies the
CURRENT stored revision's own signature (``mrr.domain.hashing_policy.
verify_object_signature``, the same ``model_dump(mode="json")`` convention
E2-T02 uses for ``NodeManifest`` — see
``docs/spec/adr/ADR-0004-CANONICAL-OBJECT-SERIALIZATION.md`` (status:
proposed) for the cross-service alignment planned before E5) before any
decision is recorded, fail closed (MRR-FR-031: "A cross-practice TaskBundle MUST be
signed by the origin practice"; docs/spec/04_SECURITY_AND_POLICY.md section
8.2: "Task ... envelopes include ... signatures"). For the first negotiation
round this is exactly the origin's own signature over its initial CREATED
bundle, as MRR-FR-031 names it. After a ``propose_modification`` round, the
"current" stored revision is the one the deciding node itself just signed as
the modifier (MRR-FR-023/034) — re-verifying that signature on a later
``accept``/``defer``/``reject`` call for the same bundle is a harmless,
uniform application of the same fail-closed gate rather than a second,
differently-shaped check; the specification does not separately name a
"verify the modifier's own signature" step, and this generalizes MRR-FR-031's
"verify before any decision" gate to whichever revision is actually current,
which is the only self-consistent reading available without inventing a
parallel, unnamed verification path. Flagged for reviewer scrutiny in the PR.

The verifying key, like ``CapabilityRegistry.register``'s ``verifying_key``
(E2-T02), is caller-supplied on every ``NodeTaskDecisionService`` method —
key management, trust anchoring, and revocation stay E5
(docs/spec/04_SECURITY_AND_POLICY.md section 8.4), out of scope here.

--- The TaskBundle-has-no-status-field problem -------------------------------

``schemas/task-bundle.schema.json`` has no ``status``/lifecycle field at all
(unlike ``ResearchScore``, whose ``status`` is a real schema property that
directly participates in its own signed/hashed content) —
``mrr.domain.lifecycles``'s own module docstring already flags this as an
open specification question ("TaskBundle has no schema status enum to
anchor its state names against ... the states below are the section-6
diagram names verbatim"). This service is the first to actually have to
drive a ``TaskBundle`` through ``TASK_BUNDLE_LIFECYCLE``, so it is the first
to have to resolve, not just note, that gap. The resolution here:

1. ``StoredObject.body`` gets one extra top-level key beyond the TaskBundle
   schema's own fields, ``_STATUS_KEY`` ("status"), holding the current
   ``TASK_BUNDLE_LIFECYCLE`` state name. ``body`` is a plain, never-schema-
   revalidated JSONB blob (``mrr.persistence.tables.objects_table.body``) —
   nothing in this codebase re-validates a *stored* body against
   ``schemas/task-bundle.schema.json`` (only ``examples/*.example.json`` are
   schema-checked, by ``scripts/check_contracts.py``), so this addition
   never collides with schema validation. ``_reconstruct_bundle`` strips it
   back out before treating a stored body as a real ``TaskBundle`` again
   (in particular, before signature verification — the signed payload never
   included this key, so it must not be present when re-deriving the bytes
   that were actually signed).
2. Pure workflow-status transitions (``offer``, ``accept``, ``defer``,
   ``reject``, ``accept_modification``) do **not** touch the bundle's own
   ``revision``/``content_hash``/``signature`` fields — those stay exactly
   as originally signed. Recomputing a content hash or asking for a new
   signature on every status flip would be wrong: MRR-FR-034 ties "a new
   content hash and signature" specifically to "a task revision"
   (a genuine content change), and nothing about advancing negotiation
   status is a content change the origin or node should have to re-sign.
3. ``mrr.persistence.repositories.PostgresObjectRepository.insert_revision``
   nonetheless requires a strictly-incrementing ``StoredObject.revision`` on
   every insert (task-packets/E1-T06.yaml's ``record_object_revision_with_
   event`` always pairs exactly one object insert with exactly one event —
   there is no bare "append an event with no new object row" primitive, by
   E1-T06's own deliberate design; see ``mrr.persistence.repositories``'s
   module docstring). So every transition here, even a pure status flip,
   does insert a new ``StoredObject`` row — its ``revision`` is a store-level
   append-only counter, independent of (and, after the first transition,
   numerically ahead of) the ``TaskBundle.revision`` field embedded in
   ``body``, which only advances when ``propose_modification`` actually
   mints new signed content. Multiple stored revisions can and do share the
   same ``content_hash`` while only ``body["status"]`` differs between them
   — that is the intended, documented shape of "the same signed object,
   observed at successive workflow states," not a bug.

This is a genuine, load-bearing design choice made in the absence of a
schema-level status field, not an invented shortcut around one that exists —
flagged in the PR as an open specification question (should
``schemas/task-bundle.schema.json`` gain a status property, matching
``ResearchScore``'s and mirrored into ``mrr.domain.lifecycles``' own
documented gap?).

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
from mrr.domain.hashing_policy import verify_object_signature
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

#: See the module docstring's "TaskBundle-has-no-status-field problem"
#: section: the one key ``StoredObject.body`` carries beyond the TaskBundle
#: schema's own fields, holding the current ``TASK_BUNDLE_LIFECYCLE`` state
#: name. Never present in a real signed TaskBundle payload — stripped by
#: ``_reconstruct_bundle`` before treating a stored body as a ``TaskBundle``
#: again.
_STATUS_KEY = "status"

#: docs/spec/01_SYSTEM_SPEC.md MRR-FR-024's refusal event type, and the other
#: seven event types this module writes. Dot-separated, matching
#: ``node_manifest.registered``/``research_score.*``'s existing convention.
_EVENT_CREATED = "task_bundle.created"
_EVENT_OFFERED = "task_bundle.offered"
_EVENT_MODIFICATION_ACKNOWLEDGED = "task_bundle.modification_acknowledged"
_EVENT_ACCEPTED = "task_bundle.accepted"
_EVENT_DEFERRED = "task_bundle.deferred"
_EVENT_REJECTED = "task_bundle.rejected"
_EVENT_MODIFICATION_PROPOSED = "task_bundle.modification_proposed"
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


def _bundle_to_stored_object(
    bundle: TaskBundle, *, status: str, store_revision: int
) -> StoredObject:
    """Convert ``bundle`` (already schema-valid, already signed — this
    module never signs on a caller's behalf) into the generic
    ``StoredObject`` ``ObjectRepository`` persists, tagging it with the
    current workflow ``status`` (see the module docstring's "TaskBundle-has-
    no-status-field problem"). ``store_revision`` is the append-only store
    row counter, set by the caller — see that same section for why it is
    not simply ``bundle.revision``.

    ``body`` is the full schema-shaped JSON object
    (``model_dump_json(exclude_none=True)``, matching
    ``mrr.services.research_score.service._score_to_stored_object`` and
    ``mrr.services.capability_registry.service._manifest_to_stored_object``'s
    own pattern) plus the one added ``_STATUS_KEY``.
    """
    body: dict[str, Any] = json.loads(bundle.model_dump_json(exclude_none=True))
    body[_STATUS_KEY] = status
    return StoredObject(
        id=bundle.id,
        api_version=bundle.api_version,
        kind=bundle.kind,
        practice_id=bundle.practice_id,
        revision=store_revision,
        created_at=bundle.created_at,
        created_by=bundle.created_by,
        content_hash=bundle.content_hash,
        supersedes=bundle.supersedes,
        labels=bundle.labels,
        body=body,
    )


def _reconstruct_bundle(stored: StoredObject) -> TaskBundle:
    """The inverse of ``_bundle_to_stored_object``: strip ``_STATUS_KEY`` and
    re-validate the rest as a ``TaskBundle``. Used both to recover the
    unchanged bundle content across a pure status transition, and — crucially
    — to rebuild the exact dict a signer would have produced
    (``bundle.model_dump(mode="json")``) before signature verification: the
    signed payload never included ``_STATUS_KEY``, so it must not be present
    here either.
    """
    body_without_status = {key: value for key, value in stored.body.items() if key != _STATUS_KEY}
    return TaskBundle.model_validate(body_without_status)


def _get_latest_or_raise(object_repository: ObjectRepository, bundle_id: str) -> StoredObject:
    try:
        return object_repository.get_latest(bundle_id)
    except ObjectNotFoundError:
        raise TaskBundleNotFoundError(bundle_id) from None


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
    bundle: TaskBundle,
    *,
    to_status: str,
    event_type: str,
    actor: Urn,
    policy_version: str,
    correlation_id: Urn,
    payload: dict[str, Any] | None = None,
) -> StoredObject:
    """Shared implementation for every ``TASK_BUNDLE_LIFECYCLE`` edge this
    module drives: assert the transition is legal (fails closed with
    ``InvalidTransitionError`` and writes nothing — checked before any
    ``StoredObject``/``DomainEvent`` is even constructed), then persist the
    next store revision (carrying ``bundle``'s content, tagged with
    ``to_status``) plus its event atomically.

    ``bundle`` is supplied by the caller rather than reconstructed here —
    for a pure status transition it is ``_reconstruct_bundle(latest)``
    (content unchanged); for ``propose_modification``'s second edge it is
    the node's genuinely new signed revision. Either way this function does
    not need to know which case it is.
    """
    from_status = latest.body[_STATUS_KEY]
    TASK_BUNDLE_LIFECYCLE.assert_transition(from_status, to_status)

    new_store_revision = latest.revision + 1
    obj = _bundle_to_stored_object(bundle, status=to_status, store_revision=new_store_revision)
    event = DomainEvent(
        id=new_urn("domain-event"),
        event_type=event_type,
        occurred_at=datetime.now(UTC),
        actor=actor,
        policy_version=policy_version,
        causation_id=_last_event_id_for(event_log, latest.id),
        correlation_id=correlation_id,
        object_id=latest.id,
        object_revision=new_store_revision,
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
        signature" convention). ``bundle.revision`` must be ``1``.

        Unlike ``ResearchScoreService.create``, there is no caller-suppliable
        "initial status" field to defensively reject here — the TaskBundle
        schema carries no status property at all (see the module docstring),
        so the only analogous guard available is the ``revision == 1`` check
        below; an attempt to ``create()`` on top of an ``id`` that already
        has a stored revision fails structurally via
        ``mrr.domain.exceptions.RevisionConflictError`` from
        ``insert_revision`` (``expected_current_revision=None`` here), the
        same way ``ResearchScoreService.create`` relies on it too.
        """
        if bundle.revision != 1:
            raise ValueError(f"TaskBundle.revision must be 1 for create(), got {bundle.revision!r}")

        self._research_score_service.ensure_can_start_work(bundle.research_score_id)
        self._ensure_capability_declared(bundle)

        status = TASK_BUNDLE_LIFECYCLE.initial_state
        obj = _bundle_to_stored_object(bundle, status=status, store_revision=1)
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
                "status": status,
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
        return _advance(
            self._event_log,
            self._record,
            latest,
            bundle,
            to_status="OFFERED",
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
        transition: the bundle is already ``OFFERED`` (``propose_modification``'s
        second edge lands there), and ``OFFERED -> OFFERED`` is not a drawn
        edge (self-transitions are never legal, per
        ``mrr.domain.lifecycles.StateMachine``). The node still holds the
        sole authority to move the bundle to ``ACCEPTED``
        (``NodeTaskDecisionService.accept``, MRR-FR-022) — this method
        cannot and does not grant the origin any such path; it only records
        a ``task_bundle.modification_acknowledged`` event, still requiring a
        new store revision (see the module docstring for why every write
        here pairs with one) whose content and status are otherwise
        unchanged from ``latest``.

        Raises:
            InvalidTransitionError: if the bundle's current status is not
                ``OFFERED`` — reused here (rather than a new error type) to
                report "you cannot acknowledge a modification of a bundle
                that is not currently offered", carrying the actual status
                as ``from_state`` and ``"OFFERED"`` as ``to_state``.
        """
        latest = _get_latest_or_raise(self._object_repository, bundle_id)
        current_status = latest.body[_STATUS_KEY]
        if current_status != "OFFERED":
            raise InvalidTransitionError(TASK_BUNDLE_LIFECYCLE.name, current_status, "OFFERED")

        bundle = _reconstruct_bundle(latest)
        new_store_revision = latest.revision + 1
        obj = _bundle_to_stored_object(
            bundle, status=current_status, store_revision=new_store_revision
        )
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=_EVENT_MODIFICATION_ACKNOWLEDGED,
            occurred_at=datetime.now(UTC),
            actor=actor,
            policy_version=policy_version,
            causation_id=_last_event_id_for(self._event_log, bundle_id),
            correlation_id=correlation_id,
            object_id=bundle_id,
            object_revision=new_store_revision,
            payload={"status": current_status, "acknowledged_content_revision": bundle.revision},
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
        bundle's own ``target_node_id`` and the current revision's signature
        verifies under ``verifying_key`` — both fail closed, before any
        decision (MRR-FR-022, MRR-FR-031). See ``_authorize_and_verify``.
        """
        latest, bundle = self._authorize_and_verify(bundle_id, deciding_node_id, verifying_key)
        return _advance(
            self._event_log,
            self._record,
            latest,
            bundle,
            to_status="ACCEPTED",
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
        return _advance(
            self._event_log,
            self._record,
            latest,
            bundle,
            to_status="DEFERRED",
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
        payload: dict[str, Any] = {
            "from_status": latest.body[_STATUS_KEY],
            "to_status": "REJECTED",
            "reason_category": reason_category,
        }
        if explanation is not None:
            payload["explanation"] = explanation
        return _advance(
            self._event_log,
            self._record,
            latest,
            bundle,
            to_status="REJECTED",
            event_type=_EVENT_REJECTED,
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            payload=payload,
        )

    # ------------------------------------------------------------------
    # OFFERED -> MODIFICATION_PROPOSED -> OFFERED (MRR-FR-023/034: a new
    # signed revision, returned to OFFERED for the origin).
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
        """Traverse both edges ``TASK_BUNDLE_LIFECYCLE`` draws for a proposed
        modification — ``OFFERED -> MODIFICATION_PROPOSED`` then
        ``MODIFICATION_PROPOSED -> OFFERED`` — as two atomic store writes
        (see the module docstring for why one ``DomainEvent`` always pairs
        with one new ``StoredObject`` row, so two drawn edges mean two
        writes here): first a status-only revision recording that a
        modification is being proposed (bundle content unchanged), then the
        genuinely new revision carrying ``modified_bundle``'s own new
        ``content_hash``/``signature`` (minted by ``deciding_node_id`` as
        the modifier, not by this service — same "caller mints hash/
        signature" convention as everywhere else in this codebase), landing
        back in ``OFFERED`` for the origin (``TaskBundleService.
        accept_modification``) to acknowledge. The prior revision(s) —
        including the original revision 1 the origin signed — are left
        completely untouched; only new rows are ever inserted.

        Raises:
            NodeAuthorityError / mrr.crypto.exceptions.SignatureVerificationError:
                see ``_authorize_and_verify`` — checked before either write.
            ValueError: ``modified_bundle.id`` does not match ``bundle_id``,
                ``modified_bundle.revision`` is not exactly the current
                content revision + 1, or ``modified_bundle.content_hash``
                equals the current revision's hash unchanged (MRR-FR-034
                requires a genuinely new content hash for "a task
                revision"). Checked after authorization/signature but before
                either write, so a malformed proposed revision fails closed
                with nothing persisted.
        """
        latest, bundle = self._authorize_and_verify(bundle_id, deciding_node_id, verifying_key)

        if modified_bundle.id != bundle.id:
            raise ValueError(
                f"modified_bundle.id ({modified_bundle.id!r}) must match the bundle being "
                f"modified ({bundle.id!r})"
            )
        expected_revision = bundle.revision + 1
        if modified_bundle.revision != expected_revision:
            raise ValueError(
                f"modified_bundle.revision must be {expected_revision!r} "
                f"(current content revision + 1), got {modified_bundle.revision!r}"
            )
        if modified_bundle.content_hash == bundle.content_hash:
            raise ValueError(
                "modified_bundle must carry a new content_hash (MRR-FR-034) — got the "
                "unchanged prior hash"
            )

        proposed = _advance(
            self._event_log,
            self._record,
            latest,
            bundle,
            to_status="MODIFICATION_PROPOSED",
            event_type=_EVENT_MODIFICATION_PROPOSED,
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
        )
        return _advance(
            self._event_log,
            self._record,
            proposed,
            modified_bundle,
            to_status="OFFERED",
            event_type=_EVENT_MODIFICATION_OFFERED,
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            payload={
                "from_status": "MODIFICATION_PROPOSED",
                "to_status": "OFFERED",
                "content_revision": modified_bundle.revision,
            },
        )

    # ------------------------------------------------------------------
    # Internal helpers.
    # ------------------------------------------------------------------

    def _authorize_and_verify(
        self, bundle_id: Urn, deciding_node_id: Urn, verifying_key: Ed25519PublicKey
    ) -> tuple[StoredObject, TaskBundle]:
        """Load the latest stored revision, enforce MRR-FR-022 (structural
        node-authority check: ``deciding_node_id`` must equal the stored
        bundle's ``target_node_id``), then verify the current revision's
        signature under ``verifying_key`` (MRR-FR-031) — both fail closed,
        both before any decision or persistence. The authority check runs
        first (cheap, no cryptography) so an unauthorized caller learns
        nothing about whether the bundle's signature is even valid.

        Raises:
            TaskBundleNotFoundError: no stored bundle for ``bundle_id``.
            NodeAuthorityError: ``deciding_node_id`` is not the bundle's
                ``target_node_id``. Nothing has been read from the bundle's
                content or verified at this point beyond ``target_node_id``
                itself.
            mrr.crypto.exceptions.SignatureVerificationError /
            UnsupportedAlgorithmError: the current revision's signature does
                not verify under ``verifying_key``.
        """
        latest = _get_latest_or_raise(self._object_repository, bundle_id)
        target_node_id = latest.body["target_node_id"]
        if deciding_node_id != target_node_id:
            raise NodeAuthorityError(bundle_id, target_node_id, deciding_node_id)

        bundle = _reconstruct_bundle(latest)
        verify_object_signature(
            verifying_key,
            bundle.model_dump(mode="json"),
            bundle.signature.value,
            algorithm=bundle.signature.algorithm,
        )
        return latest, bundle
