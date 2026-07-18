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

--- ADR-0007: lifecycle transitions are events, not new signed revisions ----

docs/spec/adr/ADR-0007-TASK-BUNDLE-TRANSITIONS-ARE-EVENTS.md is the
authorizing decision for this module's shape and supersedes both the first
E2-T03 implementation's own workaround and the withdrawn ADR-0006. The
problem it fixes: a first-class object's ``revision``/``content_hash`` are
part of what a signature covers (docs/spec/02_DOMAIN_MODEL.md section 1.3),
and TaskBundle is the only lifecycle-bearing object that is ALSO
origin-signed (MRR-FR-031). Modeling every workflow step (offer, accept,
defer, reject) as "a new revision" — the natural pattern for
ResearchScore/Claim/CorrectionEvent, none of which are signed — would mean
``revision``/``content_hash`` moving out from under a signature that is
produced exactly once. The very first version of this module reconciled
that by scanning stored revisions for the one the current ``signature.value``
was actually produced against (``_find_signed_revision``) — which proved
"some ancestor was validly signed", not "the current content matches what
was signed": a real verification-semantics weakness that ADR-0007 removes at
the root, not by patching around it further.

The model this module now implements, exactly per ADR-0007:

- The origin-signed TaskBundle **content record** is written ONCE, as
  revision 1, by ``TaskBundleService.create``, and is never touched again by
  a pure lifecycle transition. Its signature therefore always verifies
  directly against whatever ``ObjectRepository.get_latest`` returns — no
  historical scan, ever (see ``NodeTaskDecisionService._authorize_and_verify``,
  and the unit/integration tests literally named for this: signature
  verification after an arbitrary sequence of transitions still passes, and
  a content-tampered record is still caught).
- ``offer``/``accept``/``defer``/``reject`` (the drawn ``TASK_BUNDLE_LIFECYCLE``
  edges out of CREATED/OFFERED) append an append-only **domain event** via
  the new event-only unit-of-work primitive
  (``mrr.persistence.unit_of_work.record_event``, E1-T06's ADR-0007
  addition) and mint NO new object content revision. The authoritative
  CURRENT status is derived from the event log, not read off any stored
  ``body["status"]`` (``_current_status`` below) — ``body["status"]`` on a
  content record is that record's own creation-time snapshot, a historical
  fact about when it was written, not a live field.
- ``propose_modification`` is the one exception, and it is not really an
  exception at all: it is a genuine CONTENT change (new purpose/resources/
  whatever the modifying node wants to counter-propose), so it still goes
  through ``record_object_revision_with_event`` exactly like
  ``create`` — a new content hash, a new signature by the modifying node,
  a new revision (MRR-FR-023/034). The "returns to OFFERED" lifecycle
  language is represented purely in the event stream (the
  ``task_bundle.modification_offered`` event's ``to_status``), not by a
  transient ``MODIFICATION_PROPOSED`` object row (see "propose_modification
  is one write, not two" below, unchanged from the first implementation).
- ``accept_modification`` (the origin's acknowledgement that a node's
  counter-proposal exists and stands) never changed the lifecycle *status*
  (bundle stays OFFERED throughout) and, per ADR-0007's "only a genuine
  content change creates a revision" rule, no longer writes any object
  revision either — it is now a plain event-only write via
  ``record_event``, same as ``offer``/``accept``/``defer``/``reject``. Under
  the pre-ADR-0007 implementation this method DID write a revision, but only
  because ADR-0005 had (temporarily) made every workflow step "a revision by
  construction" — a premise ADR-0007 replaces.

What this rework removes entirely, and why:

- ``_find_signed_revision`` — the historical-revision scan described above.
  Gone; verification now targets ``get_latest`` directly.
- ``_bundle_with_new_status`` — built a same-content-different-status/
  revision/content_hash copy of a bundle for a pure lifecycle transition.
  Gone; a lifecycle transition no longer touches the content record at all.
- The "``TaskBundle.revision`` and the store revision stay in lockstep"
  section from the prior revision of this docstring — no longer true, or
  needed: the store's row-revision counter and ``TaskBundle.revision`` both
  still only ever advance on a genuine content write (``create``,
  ``propose_modification``), which is exactly the same event, not two
  independent counters that happen to agree.

Because a lifecycle transition writes no revision, ``offer``/``accept``/
``defer``/``reject``/``accept_modification`` can no longer return "the new
``StoredObject``" the way the old revision-per-transition design did — there
is no new one. They return ``TaskBundleTransition`` instead: the unchanged
content record (``content``, still whatever ``get_latest`` already returned)
paired with the newly current, event-derived ``status`` — which will, after
any transition beyond ``create``, differ from ``content.body["status"]`` by
design; that divergence *is* the point of ADR-0007, not a bug, and the test
suite asserts it explicitly.

--- propose_modification is one write, not two -------------------------------

``TASK_BUNDLE_LIFECYCLE`` draws ``OFFERED -> MODIFICATION_PROPOSED ->
OFFERED`` as two edges. This module never materializes the transient
``MODIFICATION_PROPOSED`` state as its own stored row (before OR after
ADR-0007): ``propose_modification`` validates both edges are lifecycle-legal
(two ``TASK_BUNDLE_LIFECYCLE.assert_transition`` calls — the second,
``MODIFICATION_PROPOSED -> OFFERED``, is unconditionally legal, so in
practice this reduces to requiring the CURRENT event-derived status be
``OFFERED``) but persists exactly **one** new content revision — the
modifier's own signed content, landing directly at ``latest.revision + 1`` —
plus one ``task_bundle.modification_offered`` event whose payload documents
that it passed through the (unmaterialized) ``MODIFICATION_PROPOSED`` state.
The prior revision(s) are, as always, left completely untouched.

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
from dataclasses import dataclass
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
from mrr.persistence.unit_of_work import record_event, record_object_revision_with_event
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

#: Event types whose payload carries a lifecycle ``to_status`` — exactly the
#: drawn ``TASK_BUNDLE_LIFECYCLE`` edges this module drives (ADR-0007's
#: "current status is derived from the latest lifecycle event"). Deliberately
#: excludes ``_EVENT_CREATED`` (creation is not a transition — there is no
#: "from" state, and the content record's own ``body["status"]`` already IS
#: the fallback ``_current_status`` uses) and
#: ``_EVENT_MODIFICATION_ACKNOWLEDGED`` (the origin's acknowledgement changes
#: no lifecycle status at all — the bundle stays OFFERED throughout, per
#: ``TaskBundleService.accept_modification``'s own docstring).
_LIFECYCLE_TRANSITION_EVENT_TYPES: frozenset[str] = frozenset(
    {
        _EVENT_OFFERED,
        _EVENT_ACCEPTED,
        _EVENT_DEFERRED,
        _EVENT_REJECTED,
        _EVENT_MODIFICATION_OFFERED,
    }
)

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
#: are bound — the CONTENT-revision path (``create``, ``propose_modification``).
#: Identical in shape to
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

#: The callable shape ``mrr.persistence.unit_of_work.record_event`` takes
#: once its ``engine``/``event_log`` arguments are bound — the EVENT-ONLY
#: path (ADR-0007: ``offer``/``accept``/``defer``/``reject``/
#: ``accept_modification``, none of which write a content revision).
RecordEvent = Callable[[DomainEvent], AppendedEvent]


class _EventJournal(Protocol):
    """The one read operation this module needs from an event log. Same
    shape and rationale as ``mrr.services.research_score.service._EventJournal``
    / ``mrr.services.capability_registry.service._EventJournal``.
    """

    def read_all(self) -> list[AppendedEvent]: ...


@dataclass(frozen=True, slots=True)
class TaskBundleTransition:
    """The result of an ADR-0007 event-only lifecycle transition — ``offer``,
    ``accept``, ``defer``, ``reject``, ``accept_modification``. None of these
    write a new content revision, so there is no "new ``StoredObject``" to
    return the way ``create``/``propose_modification`` do.

    ``content`` is the unchanged content record ``ObjectRepository.get_latest``
    already returned before this transition ran (still revision 1, unless an
    earlier ``propose_modification`` advanced it) — its own
    ``content.body["status"]`` is that record's creation-time snapshot and,
    after this transition, will generally NOT equal ``status`` below. That
    divergence is the whole point of ADR-0007, not a bug: ``status`` is the
    live, event-derived current lifecycle status; ``content`` is the
    immutable, always-signature-verifiable content. ``appended_event`` is the
    domain event this transition just recorded.
    """

    content: StoredObject
    status: str
    appended_event: AppendedEvent


def bind_unit_of_work(
    engine: Engine,
    object_repository: PostgresObjectRepository,
    event_log: PostgresEventLog,
) -> RecordRevisionWithEvent:
    """Bind ``record_object_revision_with_event`` (the CONTENT-revision path)
    to a concrete ``sqlalchemy.Engine``/``PostgresObjectRepository``/
    ``PostgresEventLog`` triple. Production wiring and integration tests call
    this once each for ``TaskBundleService`` and ``NodeTaskDecisionService``
    (they may safely share the same bound callable, since both ultimately
    write the same ``objects``/``domain_events`` tables); DB-free unit tests
    pass their own trivial callable of the same shape, backed by in-memory
    fakes, instead.
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


def bind_event_unit_of_work(engine: Engine, event_log: PostgresEventLog) -> RecordEvent:
    """Bind ``mrr.persistence.unit_of_work.record_event`` (ADR-0007's
    EVENT-ONLY path — no content revision) to a concrete
    ``sqlalchemy.Engine``/``PostgresEventLog`` pair. Production wiring and
    integration tests call this once each for ``TaskBundleService`` and
    ``NodeTaskDecisionService``; DB-free unit tests pass their own trivial
    callable of the same ``RecordEvent`` shape, backed by an in-memory fake
    event log, instead.
    """

    def _record_event(event: DomainEvent) -> AppendedEvent:
        return record_event(engine, event_log, event)

    return _record_event


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
    own pattern exactly. Used only for genuine content writes (``create``,
    ``propose_modification`` — ADR-0007); a pure lifecycle transition never
    calls this, since it never builds a new content record at all.
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
    this is a direct ``model_validate`` — no stripping needed.
    """
    return TaskBundle.model_validate(stored.body)


def _get_latest_or_raise(object_repository: ObjectRepository, bundle_id: str) -> StoredObject:
    try:
        return object_repository.get_latest(bundle_id)
    except ObjectNotFoundError:
        raise TaskBundleNotFoundError(bundle_id) from None


def _current_status(event_log: _EventJournal, latest: StoredObject) -> str:
    """The authoritative CURRENT ``TASK_BUNDLE_LIFECYCLE`` status (ADR-0007):
    the ``to_status`` of the most recently appended lifecycle-transition
    event for this bundle (``_LIFECYCLE_TRANSITION_EVENT_TYPES`` — offer,
    accept, defer, reject, propose_modification), or ``latest.body["status"]``
    — the immutable content record's own creation-time status snapshot —
    when no transition event has been recorded yet (a freshly created
    bundle, still ``CREATED``).

    ``event_log.read_all()`` returns events oldest-first (both the
    ``EventLog`` protocol's contract and ``PostgresEventLog``'s
    implementation), so the LAST matching entry is the most recent —
    "newest wins" over the filtered list.
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
    record_event_fn: RecordEvent,
    latest: StoredObject,
    current_status: str,
    *,
    to_status: str,
    event_type: str,
    actor: Urn,
    policy_version: str,
    correlation_id: Urn,
    payload: dict[str, Any] | None = None,
) -> TaskBundleTransition:
    """Shared implementation for every EVENT-ONLY ``TASK_BUNDLE_LIFECYCLE``
    edge this module drives (``offer``/``accept``/``defer``/``reject`` —
    ADR-0007): assert the transition is legal (fails closed with
    ``InvalidTransitionError`` and appends nothing — checked before any
    ``DomainEvent`` is even constructed), then append the transition's event
    via ``record_event_fn``. Writes NO object content revision — ``latest``
    is returned unchanged inside the result.

    Not used by ``accept_modification`` (its "transition" is ``OFFERED ->
    OFFERED``, not a legal edge — see that method) or by
    ``propose_modification`` (a genuine content write, going through
    ``record_object_revision_with_event`` instead — see that method's
    docstring); both call ``TASK_BUNDLE_LIFECYCLE.assert_transition``
    themselves as needed.
    """
    TASK_BUNDLE_LIFECYCLE.assert_transition(current_status, to_status)

    event = DomainEvent(
        id=new_urn("domain-event"),
        event_type=event_type,
        occurred_at=datetime.now(UTC),
        actor=actor,
        policy_version=policy_version,
        causation_id=_last_event_id_for(event_log, latest.id),
        correlation_id=correlation_id,
        object_id=latest.id,
        object_revision=latest.revision,
        payload=payload
        if payload is not None
        else {"from_status": current_status, "to_status": to_status},
    )
    appended = record_event_fn(event)
    return TaskBundleTransition(content=latest, status=to_status, appended_event=appended)


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
        record_event: RecordEvent,
        research_score_service: ResearchScoreService,
        capability_registry: CapabilityRegistry,
    ) -> None:
        self._object_repository = object_repository
        self._event_log = event_log
        self._record = record
        self._record_event = record_event
        self._research_score_service = research_score_service
        self._capability_registry = capability_registry

    # ------------------------------------------------------------------
    # Creation (MRR-FR-030/031/032) — the one-time CONTENT write.
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
        (``"CREATED"``).

        Per ADR-0007, this revision-1 content record is written ONCE and
        never touched again by a pure lifecycle transition — see the module
        docstring. Its origin signature therefore always verifies directly
        against whatever ``get_latest`` returns, for as long as no
        ``propose_modification`` has happened.

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
    # explicitly offer before the node can decide). ADR-0007: EVENT-ONLY —
    # no new content revision.
    # ------------------------------------------------------------------

    def offer(
        self, bundle_id: Urn, *, actor: Urn, policy_version: str, correlation_id: Urn
    ) -> TaskBundleTransition:
        """CREATED -> OFFERED, recorded as a ``task_bundle.offered`` domain
        event. The content record (revision 1) is untouched.
        """
        latest = _get_latest_or_raise(self._object_repository, bundle_id)
        current_status = _current_status(self._event_log, latest)
        return _advance(
            self._event_log,
            self._record_event,
            latest,
            current_status,
            to_status="OFFERED",
            event_type=_EVENT_OFFERED,
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
        )

    # ------------------------------------------------------------------
    # The origin's acknowledgement of a node-proposed modification
    # (MRR-FR-023: "... explicitly accepted by the origin before
    # execution"). ADR-0007: EVENT-ONLY.
    # ------------------------------------------------------------------

    def accept_modification(
        self, bundle_id: Urn, *, actor: Urn, policy_version: str, correlation_id: Urn
    ) -> TaskBundleTransition:
        """Record the origin's explicit acknowledgement that the current
        content revision (following a node's ``propose_modification``)
        exists and stands.

        This is deliberately **not** a ``TASK_BUNDLE_LIFECYCLE`` state
        transition: the bundle is already ``OFFERED`` (``propose_modification``
        lands there), and ``OFFERED -> OFFERED`` is not a drawn edge
        (self-transitions are never legal, per
        ``mrr.domain.lifecycles.StateMachine``). The node still holds the
        sole authority to move the bundle to ``ACCEPTED``
        (``NodeTaskDecisionService.accept``, MRR-FR-022) — this method
        cannot and does not grant the origin any such path; it only records
        a ``task_bundle.modification_acknowledged`` event. Per ADR-0007 this
        writes NO content revision (unlike the pre-ADR-0007 implementation,
        which minted one purely because every workflow step was then, by
        ADR-0005 construction, "a revision" — a premise ADR-0007 replaces:
        only a genuine content change is a revision now).

        Raises:
            InvalidTransitionError: if the bundle's current (event-derived)
                status is not ``OFFERED`` — reused here (rather than a new
                error type) to report "you cannot acknowledge a modification
                of a bundle that is not currently offered", carrying the
                actual status as ``from_state`` and ``"OFFERED"`` as
                ``to_state``.
        """
        latest = _get_latest_or_raise(self._object_repository, bundle_id)
        current_status = _current_status(self._event_log, latest)
        if current_status != "OFFERED":
            raise InvalidTransitionError(TASK_BUNDLE_LIFECYCLE.name, current_status, "OFFERED")

        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=_EVENT_MODIFICATION_ACKNOWLEDGED,
            occurred_at=datetime.now(UTC),
            actor=actor,
            policy_version=policy_version,
            causation_id=_last_event_id_for(self._event_log, bundle_id),
            correlation_id=correlation_id,
            object_id=bundle_id,
            object_revision=latest.revision,
            payload={"status": "OFFERED", "acknowledged_content_revision": latest.revision},
        )
        appended = self._record_event(event)
        return TaskBundleTransition(content=latest, status="OFFERED", appended_event=appended)


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
        record_event: RecordEvent,
    ) -> None:
        self._object_repository = object_repository
        self._event_log = event_log
        self._record = record
        self._record_event = record_event

    # ------------------------------------------------------------------
    # OFFERED -> ACCEPTED. ADR-0007: EVENT-ONLY.
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
    ) -> TaskBundleTransition:
        """OFFERED -> ACCEPTED, after verifying ``deciding_node_id`` is the
        bundle's own ``target_node_id`` and the signature verifies
        (MRR-FR-022, MRR-FR-031) — both fail closed, before any decision.
        See ``_authorize_and_verify``. Records a ``task_bundle.accepted``
        event; writes NO content revision.
        """
        latest, _bundle, current_status = self._authorize_and_verify(
            bundle_id, deciding_node_id, verifying_key
        )
        return _advance(
            self._event_log,
            self._record_event,
            latest,
            current_status,
            to_status="ACCEPTED",
            event_type=_EVENT_ACCEPTED,
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
        )

    # ------------------------------------------------------------------
    # OFFERED -> DEFERRED. ADR-0007: EVENT-ONLY.
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
    ) -> TaskBundleTransition:
        """OFFERED -> DEFERRED."""
        latest, _bundle, current_status = self._authorize_and_verify(
            bundle_id, deciding_node_id, verifying_key
        )
        return _advance(
            self._event_log,
            self._record_event,
            latest,
            current_status,
            to_status="DEFERRED",
            event_type=_EVENT_DEFERRED,
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
        )

    # ------------------------------------------------------------------
    # OFFERED -> REJECTED (MRR-FR-024: a refusal event with a reason
    # category). ADR-0007: EVENT-ONLY.
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
    ) -> TaskBundleTransition:
        """OFFERED -> REJECTED, recording a ``task_bundle.rejected`` event
        carrying ``reason_category`` (docs/spec/04_SECURITY_AND_POLICY.md
        section 8.3: "a coarse reason code") and the optional human-readable
        ``explanation`` (MRR-FR-024). See the module docstring's "Refusal
        reason vocabulary" section — ``RefusalReason`` is this task's own
        minimal proposal, not schema- or spec-defined. Writes NO content
        revision.

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

        latest, _bundle, current_status = self._authorize_and_verify(
            bundle_id, deciding_node_id, verifying_key
        )
        payload: dict[str, Any] = {
            "from_status": current_status,
            "to_status": "REJECTED",
            "reason_category": reason_category,
        }
        if explanation is not None:
            payload["explanation"] = explanation
        return _advance(
            self._event_log,
            self._record_event,
            latest,
            current_status,
            to_status="REJECTED",
            event_type=_EVENT_REJECTED,
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            payload=payload,
        )

    # ------------------------------------------------------------------
    # OFFERED -> MODIFICATION_PROPOSED -> OFFERED (MRR-FR-023/034: a new
    # signed CONTENT revision, returned to OFFERED for the origin). This is
    # the one node decision ADR-0007 keeps as a content write — see the
    # module docstring's "propose_modification is one write, not two"
    # section.
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
        and ``MODIFICATION_PROPOSED -> OFFERED``) are lifecycle-legal against
        the CURRENT event-derived status, then persist ``modified_bundle`` —
        the modifier's own new signed content, already carrying its own new
        ``content_hash``/``signature`` (minted by ``deciding_node_id``, not by
        this service — same "caller mints hash/signature" convention as
        everywhere else in this codebase) and its own ``status="OFFERED"`` —
        as exactly one new content revision, plus one
        ``task_bundle.modification_offered`` event. The prior revision(s) —
        including the original revision the origin signed — are left
        completely untouched; only one new row is ever inserted.

        After this call, ``get_latest`` returns this new, modifier-signed
        content record, and its signature verifies directly against it — the
        next node decision on this bundle (e.g. ``accept``) must be given the
        MODIFIER's public key, not the origin's (see
        ``test_full_negotiation_loop_modification_then_accept``).

        Raises:
            NodeAuthorityError / mrr.crypto.exceptions.SignatureVerificationError:
                see ``_authorize_and_verify`` — checked before anything else.
            InvalidTransitionError: the bundle's current (event-derived)
                status is not ``OFFERED`` (the only status from which
                ``MODIFICATION_PROPOSED`` is reachable).
            ValueError: ``modified_bundle.id`` does not match ``bundle_id``,
                ``modified_bundle.status`` is not ``"OFFERED"``,
                ``modified_bundle.revision`` is not exactly the current
                content revision + 1, or ``modified_bundle.content_hash``
                equals the current revision's hash unchanged (MRR-FR-034
                requires a genuinely new content hash for "a task revision").
                Checked after authorization/signature but before the write,
                so a malformed proposed revision fails closed with nothing
                persisted.
        """
        latest, bundle, current_status = self._authorize_and_verify(
            bundle_id, deciding_node_id, verifying_key
        )

        TASK_BUNDLE_LIFECYCLE.assert_transition(current_status, "MODIFICATION_PROPOSED")
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
        expected_revision = latest.revision + 1
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
                "from_status": current_status,
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
    ) -> tuple[StoredObject, TaskBundle, str]:
        """Load the latest stored content revision, enforce MRR-FR-022
        (structural node-authority check: ``deciding_node_id`` must equal the
        stored bundle's ``target_node_id``), verify the signature that
        actually covers it (MRR-FR-031), and resolve the current
        event-derived status — all fail closed, all before any decision or
        persistence. The authority check runs first (cheap, no cryptography)
        so an unauthorized caller learns nothing about whether the bundle's
        signature is even valid.

        Per ADR-0007, verification targets ``latest`` DIRECTLY — no
        historical-revision scan. This is possible, and sound, precisely
        because a content record is never mutated by a pure lifecycle
        transition: ``latest`` is either the origin's original revision-1
        record (no ``propose_modification`` has happened yet) or the most
        recent modifier-signed revision, and in both cases its own embedded
        ``signature`` is exactly what was produced over exactly its own
        current fields. ``verifying_key`` must therefore be the CURRENT
        signer's public key — the origin's, or the modifier's after a
        ``propose_modification`` — not necessarily the bundle's original
        creator.

        Returns ``(latest, bundle, current_status)`` — ``latest`` the
        current stored content revision, ``bundle`` its reconstructed
        ``TaskBundle``, ``current_status`` the event-derived live lifecycle
        status (``_current_status``) a caller validates the next transition
        against.

        Raises:
            TaskBundleNotFoundError: no stored bundle for ``bundle_id``.
            NodeAuthorityError: ``deciding_node_id`` is not the bundle's
                ``target_node_id``. Nothing has been read from the bundle's
                content or verified at this point beyond ``target_node_id``
                itself.
            mrr.crypto.exceptions.SignatureVerificationError /
            UnsupportedAlgorithmError: the signature does not verify under
                ``verifying_key`` — including when the CONTENT itself has
                been tampered with since it was signed (the content record's
                own fields no longer match what ``signature.value`` covers).
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

        current_status = _current_status(self._event_log, latest)
        return latest, bundle, current_status
