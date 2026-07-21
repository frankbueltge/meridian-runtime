"""``CorrectionImpactService`` (task-packets/E3-T06.yaml): the application-
layer service that records a ``CorrectionEvent``, drives the pure
``mrr.domain.correction_impact.compute_impact`` traversal over the real typed
edge graph (E1-T05 ``EdgeRepository``), writes the computed downstream
``impact_objects`` back onto the correction, and marks every impacted
``Claim`` ``review_required`` via the E3-T02 ``ClaimService`` — never
deleting a claim's prior decision, only appending a new revision. Sixth task
of Epic E3 (claim, evidence, correction kernel); the closest template is
``mrr.services.claim.service.ClaimService`` itself, named explicitly in the
task packet as such (record-then-transition shape, ``EdgeRepository`` reads,
``bind_unit_of_work``/local ``_EventJournal`` Protocol conventions).

--- Split record()/propagate_impact() rather than one combined method -------

task-packets/E3-T06.yaml's approved design offers a choice: one
``record_and_propagate`` method, or a split. This module splits: ``record``
persists the ``CorrectionEvent`` at revision 1 (a one-time creation, exactly
like every other service's own ``create``), and ``propagate_impact`` is the
separate, REPEATABLE, idempotent step that computes and (re-)applies impact.
Splitting them makes the packet's own idempotency invariant ("re-running does
not add a second review_required revision... and yields the same impact set")
directly expressible as "call ``propagate_impact`` more than once" rather
than needing a combined method to somehow distinguish "first call" (which
also records) from "re-run" (which must not re-record). A caller that wants
the combined one-shot behavior calls ``record`` then ``propagate_impact``
back to back, exactly as ``tests/integration/services/correction/
test_service.py`` does.

--- What counts as "affected" for the review_required transition ------------

task-packets/E3-T06.yaml's approved design: "for each impacted object that is
a CLAIM, call ClaimService.require_review". "Impacted object" is read
literally as ``mrr.domain.correction_impact.compute_impact``'s own output —
the computed DOWNSTREAM set written onto the correction's ``impact_objects``
field — not the correction's own ``affected_objects`` seeds. A seed object
that happens to be a ``Claim`` (the claim the correction is directly ABOUT)
is therefore NOT auto-transitioned to ``review_required`` by this service;
its own fate runs through the ``CorrectionEvent``'s own separate resolution
workflow (``replacement_object_id``, ``requested_action``, and eventually
``CORRECTION_LIFECYCLE``'s own NOTIFYING/AWAITING_RESPONSES states — E6,
out of this task's scope) rather than this task's downstream-propagation
concern. This mirrors ``mrr.domain.correction_impact``'s own documented
"seeds tracked separately" stance and is flagged here for the same reason:
a defensible, literal reading of the approved design text, not the only
possible one — worth reviewer scrutiny if a broader "also affects the named
seed claims directly" reading was actually intended.

--- Not driving CorrectionEvent's own CORRECTION_LIFECYCLE further -----------

``record`` checks the initial status against ``CORRECTION_LIFECYCLE.
initial_state`` ("OPEN"), exactly like ``ClaimService.create``/
``ResearchScoreService.create`` check their own machines' initial states —
but nothing in this service ever transitions a correction's own ``status``
field onward (e.g. OPEN -> IMPACT_ANALYSIS). task-packets/E3-T06.yaml's own
acceptance_tests and invariants name only the ``impact_objects`` field and
claim ``review_required`` transitions, never ``CorrectionEvent.status``
itself — and driving that lifecycle forward risks pre-empting the E6
notification task's own ownership of when a correction is considered past
impact analysis. Left untouched and flagged as an open item rather than
guessed.

--- Gathering edges: a query-driven BFS feeding the pure closure function ---

``mrr.domain.repositories.EdgeRepository`` has no "list every edge" method
(by design — E1-T05 is reuse-as-is, and this task's ``allowed_paths`` does
not include ``packages/persistence/**``), only ``edges_to``/``edges_from``
for one id at a time. ``_gather_impact_edges`` therefore drives its own
breadth-first expansion — visiting each id's incoming edges via
``edges_to(id)`` (no ``edge_type`` filter, since one round trip per id
covering every type is cheaper than one round trip per impact edge type),
keeping only edges whose type is in ``mrr.domain.correction_impact.
IMPACT_EDGE_TYPES``, and following each qualifying edge's ``source_id`` into
the next frontier — until nothing new is discovered. This necessarily
re-derives, at the query-driving level, the same "which id becomes impacted
next" logic ``compute_impact`` already implements purely; the alternative
(no query-driven expansion at all) is not available given the repository's
per-id query shape. To keep this from silently drifting into a SECOND,
divergent notion of "what impact means", the actual authoritative closure is
still always computed by handing the collected edges to ``compute_impact``
once, at the end — this method's own visited/frontier bookkeeping decides
only *which edges to fetch next*, never what the final impacted set is. This
is the same kind of deliberate, documented duplication
``mrr.services.claim.service.bind_edge_unit_of_work``'s own module docstring
flags for reviewer scrutiny ("Edge writes need their own atomic composition").

--- Idempotency: a claim already satisfying the review obligation ----------

``mrr.domain.lifecycles.CLAIM_LIFECYCLE`` declares no self-transition
(``review_required -> review_required`` is not a legal edge — see that
module's own ``StateMachine.__post_init__``), so calling
``ClaimService.require_review`` a second time on an already-
``review_required`` claim would raise ``InvalidTransitionError`` rather than
silently succeeding. The same is true of the two terminal states,
``withdrawn``/``superseded``, which have no drawn outgoing edge at all.
task-packets/E3-T06.yaml's own invariant ("a second run adds no new revision
to an already-review_required claim - check current status first") is
therefore not merely a nicety but a correctness requirement: ``_require_
review_if_needed`` reads the claim's current status and skips the transition
entirely whenever it is already ``review_required`` or one of the two
terminal states (read as "already at least as strict as review_required" per
MRR-FR-092's own "review_required or a stricter status" wording) — a plain
status check, not exception-driven control flow, so a genuinely unexpected
``InvalidTransitionError`` from some other cause still propagates instead of
being swallowed.

--- E6-T03 additions: cross-practice correction notification ----------------

``notify_affected_practices`` and ``receive_correction_notification`` are
ADDITIVE methods on this same service (task-packets/E6-T03.yaml) — every
E3-T06 method/helper above is reused verbatim, unmodified. They carry a
correction across the trust boundary using three already-shipped pieces,
reused unchanged: ``mrr.contracts.correction_event.CorrectionEvent`` (this
module's own ``record``/``propagate_impact``), the pure
``compute_impact``/``IMPACT_EDGE_TYPES`` traversal above, and the E5-T03/T06
online transport (``mrr.contracts.node_message_envelope.NodeMessageEnvelope``,
``mrr.domain.envelope_validation.validate_inbound_envelope``,
``mrr.domain.envelope_transport.EnvelopeTransport``).

Sending side (``notify_affected_practices``): mints+signs one
``mrr.contracts.correction_notification.CorrectionNotification`` per
CALLER-supplied :class:`NotificationRecipient` (mirroring
``mrr.services.node_runtime.evidence_crate.EvidenceCrateSealer.seal``'s own
draft-then-resign convention: build with a placeholder signature, compute
``content_hash`` over the ADR-0004 ``exclude_none=True`` form, sign, then
re-validate), wraps each in a ``NodeMessageEnvelope``
(``payload_kind="CorrectionNotification"``), attempts delivery via the
caller-supplied ``EnvelopeTransport``, and drives the ALREADY-DECLARED
``CORRECTION_LIFECYCLE`` forward along its already-drawn edges — OPEN ->
IMPACT_ANALYSIS -> NOTIFYING -> AWAITING_RESPONSES, continuing on to the
also-already-drawn AWAITING_RESPONSES -> DELIVERY_PENDING edge WITHIN THE
SAME CALL only if at least one recipient's synchronous delivery attempt
reports ``"failed"`` (task-packets/E6-T03.yaml derived_decisions (c); see
``_target_status_after_notification``). No new lifecycle edge is declared —
``DELIVERY_PENDING``'s already-documented "no drawn outgoing edge at all"
open question in ``mrr.domain.lifecycles`` is left exactly as-is.

One ``correction.notification_sent`` event is recorded per recipient
(section 5.2's own enumerated name), carrying the delivery outcome
(``"sent"``/``"pending"``) in its payload — the durable record MRR-FR-094
requires for a recipient who could not be reached, per AGENTS.md's own
source-of-truth discipline ("the append-only domain event log is
authoritative for audit history"). At most ONE new ``CorrectionEvent``
revision is written per call (covering the whole hop-chain, mirroring
``_write_impact_objects``'s "one revision, one event" economy) — the FIRST
recipient's event is recorded atomically with that revision via the
existing ``RecordRevisionWithEvent``; any additional recipients' events (a
second+ recipient in the same call, or ANY recipient when the correction's
status does not need to change) are appended via the new, OPTIONAL
``RecordEventOnly`` dependency (:func:`bind_event_unit_of_work`,
ADR-0007's event-only ``mrr.persistence.unit_of_work.record_event`` path,
reused unchanged) — kept optional, defaulting to ``None``, so this
service's existing constructor call sites outside this task's
``allowed_paths`` (the projection-service unit tests, E2E-003) do not break;
a caller invoking ``notify_affected_practices`` without configuring it gets
a clear ``ValueError`` rather than a silent no-op.

Outbound idempotency: recipients already recorded ``"sent"`` for this
correction (found by scanning the event log's own
``correction.notification_sent`` events — the same append-only log
AGENTS.md already names as authoritative) are skipped entirely — no
re-delivery attempt, no duplicate event, and if every caller-supplied
recipient is already ``"sent"``, no revision is written and no lifecycle
transition is attempted at all.

Receiving side (``receive_correction_notification``): validates the inbound
envelope (``validate_inbound_envelope``, UNCHANGED — the transport-layer
signature, replay, and validity-window checks), verifies the notification's
OWN signature (``resolve_trusted_correction_notification_key``, new — see
``mrr.domain.correction_notification``), checks the notification's OWN
validity window (``CorrectionNotificationNotWithinValidityWindowError`` — a
SECOND, independent window from the envelope's own), then a caller-supplied
replay predicate over the notification's OWN ``notification_id``
(``CorrectionNotificationAlreadyProcessedError`` — a SEPARATE namespace from
the envelope's own ``message_id``) — both replay checks, and the window
check, must pass before any local impact computation runs. Only THEN does
it treat ``notified_object_ids`` exactly as ``propagate_impact`` already
treats a LOCAL correction's own ``affected_objects``: gathers this
practice's OWN local edges via ``_gather_local_impact_edges`` (a small,
deliberate DUPLICATE of ``_gather_impact_edges`` — task-packets/E6-T03.yaml
derived_decisions (d): different seed provenance, same traversal shape, not
force-shared across the two call sites), runs the UNCHANGED
``compute_impact`` over them, and calls the EXISTING, unchanged
``_require_review_if_needed`` for every locally-impacted id — never
creating, storing, or mutating any copy of the remote ``CorrectionEvent``,
and never deciding accept/adapt/reject/defer (E6-T04). Returns a
:class:`LocalNotificationImpact` — the computed locally-impacted set,
explicit even when empty (MRR-NFR-012: an id with zero matching local edges
is a legitimate, explicit empty result, never a crash and never silently
dropped) — never a new event type of its own (section 5.2's required-events
enumeration has no name for the receiving side's own act of processing a
notification; the only observable trace remains the EXISTING
``claim.status_changed``-shaped events ``ClaimService.require_review``
already emits).

Deliberately deferred (task-packets/E6-T03.yaml forbidden_changes/
specification_gaps): resolving WHICH practices/nodes to notify (and which
``notified_object_ids`` matter to each) from
``TransferContract.correction_subscription``/``Obligation`` data is
E6-T01/E6-T02 wiring — ``NotificationRecipient`` is a plain caller-supplied
value, never resolved by a query here; the recipient's own accept/adapt/
reject/defer response is E6-T04; durable offline delivery tracking/retry
over a ``DELIVERY_PENDING`` correction is E6-T06 (this task performs one
synchronous online delivery ATTEMPT per recipient and records the outcome —
it does not call ``mrr.domain.offline_bundle.build_outbox_bundle`` or
enqueue anything); and the now-five near-identical trust resolvers
(manifest_trust/task_trust/crate_trust/transfer_trust/this task's own
correction_notification) are NOT refactored into one shared function.

--- E6-T04 addition: local accept/adapt/reject/defer response --------------

``record_response`` is a further ADDITIVE method on this same service
(task-packets/E6-T04.yaml) — every E3-T06/E6-T03 method/helper above is
reused verbatim, unmodified. It records this RECEIVING practice's own,
LOCAL-ONLY disposition (accept/adapt/reject/defer) toward one already
-received ``CorrectionNotification`` (E6-T03's ``receive_correction_
notification``, called separately, BEFORE ``record_response`` — no
signature is re-verified here; ``record_response`` trusts its caller to have
already run that method's own trust checks once per notification). It never
creates, stores, or mutates any copy of the sender's remote
``CorrectionEvent`` — E6-T03's own invariant that the receiving practice
never holds one — so the response is minted as a brand-new, standalone,
UNSIGNED, single-revision ``mrr.contracts.correction_response.
CorrectionResponse`` object instead (MRR-FR-084: "A receiving practice MAY
reject a correction, but MUST record that it was notified and why it
rejected or deferred it").

``record_response`` mints the ``CorrectionResponse`` at revision 1, verifies
every ``adaptations[].adapted_object_id`` already exists locally
(``ObjectRepository.get_latest``, ``ObjectNotFoundError`` if absent —
aborting the whole call with nothing persisted, mirroring E6-T01's own
identical adapted-decision verification), records one ``corrects`` edge per
adaptation entry (source=``adapted_object_id``, target=``notified_object_
id`` — the declared-but-unused-since-E3-T06 ``corrects`` edge type,
deliberately distinct from ``adapted_from``), and appends the
section-5.2-enumerated ``correction.response_recorded`` event — object
revision, edges, and event all written atomically in ONE new local
unit-of-work helper, :func:`bind_revision_with_edges_unit_of_work`,
mirroring ``mrr.services.obligation.service.
bind_revision_with_edges_unit_of_work``'s own identical "object revision +
N edges + one event" atomic composition (task-packets/E6-T02.yaml) — NOT
imported from that sibling service module (out of this task's
``allowed_paths``; the Obligation service is forbidden to modify), so it is
duplicated here rather than shared, exactly as ``_gather_impact_edges``'s
own shape is duplicated into ``ObligationService._gather_binding_edges``
for the identical reason. This local helper additionally exposes a private,
test-only ``_after_edges`` fault-injection seam (firing after every edge row
is inserted but before the event is appended, still inside the one open
transaction) — the same seam shape as ``mrr.persistence.unit_of_work.
record_object_revision_with_event``'s own ``_after_append``, which the
sibling Obligation helper does not itself expose.

A caller-supplied ``already_responded`` predicate (keyed by
``correction_notification_id``, mirroring E6-T03's own ``already_processed_
notification``) is checked FIRST, before anything else, and raises the new
``CorrectionResponseAlreadyRecordedError`` for a duplicate response — no
durable processed-notification-id store is built here, matching E6-T03's
own identical caller-supplied-predicate stance.

Deliberately NOT done here (task-packets/E6-T04.yaml forbidden_changes/
specification_gaps): no ``CORRECTION_LIFECYCLE`` transition is read or
written (the sending practice's own AWAITING_RESPONSES -> RESOLVED/
PARTIALLY_RESOLVED/REJECTED_BY_RECIPIENT aggregation remains entirely
undriven by this or any other task in the current six-task E6 epic); no
response is ever transported back to the notifying practice (a future task
or spec amendment must design that mechanism); and no further Claim
transition beyond what E6-T03's own receipt handling already applied.

--- E6-T06 additions: offline recipient delivery tracking -------------------

``open_pending_delivery``, ``retry_pending_delivery_online``,
``retry_pending_delivery_offline``, ``mark_pending_delivery_delivered``, and
``mark_pending_delivery_exhausted`` are further ADDITIVE methods on this same
service (task-packets/E6-T06.yaml) — every E3-T06/E6-T03/E6-T04 method/helper
above is reused verbatim, unmodified, including ``notify_affected_
practices``'s own one-synchronous-attempt-then-stop behavior (its own
forbidden_changes: "it does not build or call ``mrr.domain.offline_bundle.
build_outbox_bundle``, does not enqueue anything, and does not retry" — this
task is that deferred scope). They provide the durable, per-recipient
tracking layer MRR-FR-094's "pending-delivery record" half requires once a
recipient's synchronous online delivery attempt has already failed (the
moment ``notify_affected_practices`` chains ``CORRECTION_LIFECYCLE`` through
the already-drawn ``AWAITING_RESPONSES -> DELIVERY_PENDING`` edge for that
recipient), composed with the EXISTING, unmodified E5-T06 ``OfflineBundle``
export as one legitimate retry channel — never a new transport of its own.

The persistent tracking record itself is INTERNAL NODE STATE (task-packets/
E6-T06.yaml derived_decisions (a), mirroring task-packets/E5-T07.yaml's own
``processed_ids``/``PostgresProcessedIdStore`` precedent exactly) — a new
``mrr.persistence.repositories.PostgresDeliveryPendingStore`` keyed by
``(recipient_node_id, notification_id)``, injected here through the new,
OPTIONAL ``delivery_pending_store`` constructor parameter (default ``None``,
mirroring ``record_event``/``record_revision_with_edges``'s identical
"additive, does not break existing construction call sites" shape). A caller
invoking any of these five new methods without configuring it gets a clear
``ValueError`` rather than a silent no-op. It gets no ``schemas/*.
schema.json``, no ``mrr.contracts`` model, and no ``scripts/
check_contracts.py`` entry — see ``mrr.domain.delivery_retry``'s own module
docstring for the full "why internal state, not a cross-practice object"
reasoning.

``open_pending_delivery``: opens the tracking record the FIRST time a
recipient's synchronous attempt reports ``"failed"`` (idempotent — a second
call for the same ``(recipient_node_id, notification_id)`` is a no-op, no
duplicate event). ``retry_pending_delivery_online``: a FURTHER synchronous
``EnvelopeTransport.send`` attempt with the SAME already-signed
``NodeMessageEnvelope`` passed in by the caller (never re-signed, never
re-minted — task-packets/E6-T06.yaml derived_decisions (e)).
``retry_pending_delivery_offline``: wraps that SAME envelope into a FRESH
single-entry ``OfflineBundle`` via the UNCHANGED ``build_outbox_bundle`` —
proving the online and offline retry channels compose without modifying
either. Because the offline channel has no in-repo acknowledgement mechanism
(docs/spec/03_API_AND_EVENTS.md section 4.2's "optional acknowledgement
request" field was never added to ``NodeMessageEnvelope`` — flagged, not
built, in specification_gaps), an offline retry always records outcome
``"failed"`` (not yet confirmed) against the tracking store; a caller with an
out-of-band delivery signal calls ``mark_pending_delivery_delivered``
instead. ``mark_pending_delivery_exhausted`` is for a caller-decided EARLY
exhaustion (e.g. a permanently gone endpoint) rather than one discovered as a
side effect of a retry attempt.

Every one of these five methods appends its own ``correction.
notification_sent`` event (the only correction-delivery event name docs/spec/
03_API_AND_EVENTS.md section 5.2 actually enumerates) via the EXISTING,
unmodified ``record_event`` (:func:`bind_event_unit_of_work`) dependency —
never a new ``CorrectionEvent`` revision, since none of these methods ever
reads or writes ``CorrectionEvent.status``/``CORRECTION_LIFECYCLE`` (see
below). The event payload extends task-packets/E6-T03.yaml's own
``"sent"``/``"pending"`` ``delivery_status`` values with ``"delivered"``/
``"exhausted"`` and adds ``attempt_number``/``channel`` (task-packets/
E6-T06.yaml derived_decisions (g)) — a richer, SEPARATE event from whatever
``notify_affected_practices`` itself already recorded for the initial failed
attempt, not a replacement for it.

Deliberately NOT done here (task-packets/E6-T06.yaml forbidden_changes/
specification_gaps): ``CorrectionEvent.status``/``CORRECTION_LIFECYCLE`` is
NEVER transitioned by any of these five methods, even once every
per-recipient record for a correction resolves to ``delivered``/
``exhausted`` — no edge is drawn out of ``DELIVERY_PENDING`` anywhere in
docs/spec/01_SYSTEM_SPEC.md section 6.4 or ``mrr.domain.lifecycles``'s own
``_CORRECTION_TRANSITIONS`` (that module stays byte-for-byte unchanged), so a
correction whose every recipient is fully resolved remains permanently at
``DELIVERY_PENDING`` at the aggregate level under this implementation — an
open question for a future ADR or spec amendment, not resolved here. No
scheduler, cron job, or message queue is built to invoke the retry-due query
(``PostgresDeliveryPendingStore.list_due_for_retry``) on any cadence. No real
network, mTLS, or encryption/KMS is built — both retry channels operate on
already-in-memory values exactly like ``notify_affected_practices`` itself.
Whether an ``exhausted`` record should cause a fresh E6-T03 notification (a
new ``notification_id``/``expires_at``) is left an open workflow question,
not resolved unilaterally here.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import sqlalchemy as sa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.contracts import (
    CorrectionEvent,
    CorrectionNotification,
    CorrectionResponse,
    CorrectionResponseAdaptation,
    CorrectionResponseDecision,
    Signature,
    Urn,
)
from mrr.contracts.node_message_envelope import NodeMessageEnvelope
from mrr.contracts.offline_bundle import BundleEncryption, OfflineBundle
from mrr.domain.correction_impact import IMPACT_EDGE_TYPES, compute_impact
from mrr.domain.correction_notification import resolve_trusted_correction_notification_key
from mrr.domain.delivery_retry import DeliveryChannel, DeliveryPendingRecord, DeliveryPendingStore
from mrr.domain.envelope_transport import EnvelopeDeliveryRequest, EnvelopeTransport
from mrr.domain.envelope_validation import AlreadyProcessed, validate_inbound_envelope
from mrr.domain.exceptions import (
    CorrectionNotFoundError,
    CorrectionNotificationAlreadyProcessedError,
    CorrectionNotificationNotWithinValidityWindowError,
    CorrectionResponseAlreadyRecordedError,
    InvalidTransitionError,
    ObjectNotFoundError,
    UnknownEdgeTypeError,
)
from mrr.domain.hashing_policy import compute_content_hash, sign_object
from mrr.domain.identity import new_urn
from mrr.domain.key_management import KeyRing
from mrr.domain.lifecycles import CORRECTION_LIFECYCLE
from mrr.domain.offline_bundle import build_outbox_bundle
from mrr.domain.repositories import (
    EDGE_VOCABULARY,
    EdgeRepository,
    ObjectRepository,
    StoredObject,
    TypedEdge,
)
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.persistence.tables import edges_table
from mrr.persistence.unit_of_work import record_event, record_object_revision_with_event
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.claim.service import ClaimService
from sqlalchemy import Engine

#: Sentinel "from" state used only when reporting ``InvalidTransitionError``
#: for ``record()`` with a non-OPEN initial status — mirrors
#: ``ClaimService``'s/``ResearchScoreService``'s identical
#: ``_NEW_*_SENTINEL_STATE`` reasoning: creation is not itself a drawn
#: CORRECTION_LIFECYCLE edge, so this reuses the existing typed error against
#: a sentinel rather than inventing a new one.
_NEW_CORRECTION_SENTINEL_STATE = "<new>"

#: A claim status already at least as strict as ``review_required`` per
#: MRR-FR-092's "review_required or a stricter status" wording — see the
#: module docstring's "Idempotency" section. Skipping these avoids both a
#: redundant revision AND an ``InvalidTransitionError`` (neither
#: ``review_required`` nor the two terminal states have a legal
#: self-transition or, for the terminal ones, any outgoing edge at all).
_CLAIM_REVIEW_ALREADY_SATISFIED_STATUSES = frozenset({"review_required", "withdrawn", "superseded"})

_CLAIM_KIND = "Claim"

#: The callable shape ``mrr.persistence.unit_of_work.record_object_revision_with_event``
#: takes once its ``engine``/``object_repository``/``event_log`` arguments
#: are bound. A local copy, not a shared import — see
#: ``mrr.services.claim.service``'s own module docstring for why each
#: service module keeps its own.
RecordRevisionWithEvent = Callable[
    [StoredObject, int | None, DomainEvent], tuple[StoredObject, AppendedEvent]
]


class _EventJournal(Protocol):
    """The one read operation this service needs from an event log — see
    ``mrr.services.claim.service._EventJournal`` for the identical rationale.
    """

    def read_all(self) -> list[AppendedEvent]: ...


def bind_unit_of_work(
    engine: Engine,
    object_repository: PostgresObjectRepository,
    event_log: PostgresEventLog,
) -> RecordRevisionWithEvent:
    """Bind ``record_object_revision_with_event`` to a concrete
    ``sqlalchemy.Engine``/``PostgresObjectRepository``/``PostgresEventLog``
    triple — identical in shape and purpose to
    ``mrr.services.claim.service.bind_unit_of_work``. Production wiring and
    integration tests call this once; DB-free unit tests pass their own
    trivial callable of the same shape, backed by in-memory fakes.
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


#: The callable shape ``mrr.persistence.unit_of_work.record_event`` (ADR-0007's
#: event-only path) takes once its ``engine``/``event_log`` arguments are
#: bound — the E6-T03 counterpart to ``RecordRevisionWithEvent`` for
#: appending a ``correction.notification_sent`` event WITHOUT writing a new
#: ``CorrectionEvent`` revision. See :func:`bind_event_unit_of_work` and the
#: module docstring's "E6-T03 additions" section.
RecordEventOnly = Callable[[DomainEvent], AppendedEvent]


def bind_event_unit_of_work(engine: Engine, event_log: PostgresEventLog) -> RecordEventOnly:
    """Bind ``mrr.persistence.unit_of_work.record_event`` (ADR-0007,
    UNCHANGED) to a concrete ``sqlalchemy.Engine``/``PostgresEventLog`` pair.
    Production wiring and integration tests call this once; DB-free unit
    tests pass their own trivial callable of the same ``RecordEventOnly``
    shape, backed by an in-memory fake, instead.
    """

    def _record_event(event: DomainEvent) -> AppendedEvent:
        return record_event(engine, event_log, event)

    return _record_event


#: The callable shape :func:`bind_revision_with_edges_unit_of_work` below
#: produces: insert an object revision, ANY NUMBER of typed edges, and
#: append exactly ONE domain event, all atomically. See the module
#: docstring's "E6-T04 addition" section.
RecordRevisionWithEdgesAndEvent = Callable[
    [StoredObject, int | None, list[TypedEdge], DomainEvent],
    tuple[StoredObject, list[TypedEdge], AppendedEvent],
]


def record_response_revision_with_edges_and_event(
    engine: Engine,
    object_repository: PostgresObjectRepository,
    event_log: PostgresEventLog,
    obj: StoredObject,
    expected_current_revision: int | None,
    edges: list[TypedEdge],
    event: DomainEvent,
    *,
    _after_edges: Callable[[], None] | None = None,
) -> tuple[StoredObject, list[TypedEdge], AppendedEvent]:
    """Compose an object-revision insert, ANY NUMBER of ``edges`` table
    inserts, and ONE domain-event append into a SINGLE database
    transaction — the E6-T04 counterpart to ``mrr.services.obligation.
    service.bind_revision_with_edges_unit_of_work``'s identical composition
    (task-packets/E6-T02.yaml), duplicated here (NOT imported from that
    sibling service module — out of this task's ``allowed_paths``, and the
    Obligation service is a forbidden-to-modify path) rather than shared,
    exactly as ``CorrectionImpactService._gather_impact_edges``'s own shape
    is duplicated into ``ObligationService._gather_binding_edges`` for the
    identical reason. Same columns, same values, same ``EDGE_VOCABULARY``/
    ``UnknownEdgeTypeError`` check as ``mrr.persistence.repositories.
    PostgresEdgeRepository.add_edge`` and every other service module's own
    ``bind_edge_unit_of_work`` — not a second, divergent implementation of
    "how an edge is inserted".

    Every edge's ``edge_type`` is validated against ``EDGE_VOCABULARY``
    BEFORE the transaction opens — an unknown type in ANY entry aborts the
    whole call with no partial insert and no object revision write either.

    Directly callable (mirrors ``mrr.persistence.unit_of_work.
    record_object_revision_with_event``'s own directly-callable shape, rather
    than existing only behind a bound closure) so an integration test can
    inject ``_after_edges`` — firing after every edge row is inserted but
    BEFORE the event is appended, still inside the one open transaction — the
    same fault-injection seam shape as ``tests/integration/persistence/
    test_event_log_and_outbox.py``'s own ``_after_append``, which the
    sibling Obligation helper does not itself expose. A failure raised there
    leaves the object revision, every edge, AND the event unpersisted — the
    whole transaction rolls back together.
    """
    for edge in edges:
        if edge.edge_type not in EDGE_VOCABULARY:
            raise UnknownEdgeTypeError(edge.edge_type)
    hook = _after_edges or (lambda: None)
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
        hook()
        appended = event_log.append(conn, event)
        return stored, edges, appended


def bind_revision_with_edges_unit_of_work(
    engine: Engine,
    object_repository: PostgresObjectRepository,
    event_log: PostgresEventLog,
) -> RecordRevisionWithEdgesAndEvent:
    """Bind :func:`record_response_revision_with_edges_and_event` to a
    concrete ``sqlalchemy.Engine``/``PostgresObjectRepository``/
    ``PostgresEventLog`` triple — identical in shape and purpose to every
    other service module's own ``bind_unit_of_work``/``bind_edge_unit_of_
    work``. Production wiring and integration tests call this once; DB-free
    unit tests pass their own trivial callable of the same
    ``RecordRevisionWithEdgesAndEvent`` shape, backed by in-memory fakes,
    instead. The returned closure never exposes ``_after_edges`` — a test
    that needs the fault-injection seam calls
    :func:`record_response_revision_with_edges_and_event` directly instead,
    mirroring ``record_object_revision_with_event``'s identical precedent.
    """

    def _record(
        obj: StoredObject,
        expected_current_revision: int | None,
        edges: list[TypedEdge],
        event: DomainEvent,
    ) -> tuple[StoredObject, list[TypedEdge], AppendedEvent]:
        return record_response_revision_with_edges_and_event(
            engine, object_repository, event_log, obj, expected_current_revision, edges, event
        )

    return _record


#: ``NodeMessageEnvelope.payload_kind`` tag this service mints/expects for a
#: ``CorrectionNotification`` payload — a free-form string per that model's
#: own docstring; this is this task's own chosen spelling, checked by
#: ``receive_correction_notification`` before attempting to parse the
#: payload as one.
_CORRECTION_NOTIFICATION_PAYLOAD_KIND = "CorrectionNotification"

#: Placeholder ``signature.value`` used while building a draft object ahead
#: of computing its real content hash and signature — mirrors
#: ``mrr.services.node_runtime.evidence_crate.EvidenceCrateSealer``'s/
#: ``mrr.domain.offline_bundle``'s own identical
#: ``_PLACEHOLDER_SIGNATURE_VALUE`` (``min_length=40`` on
#: ``mrr.contracts.common.Signature.value``). ``prepare_for_signature``
#: strips the entire ``signature`` field before signing, so this placeholder
#: never leaks into what gets hashed or signed.
_PLACEHOLDER_SIGNATURE_VALUE = "0" * 44

#: A syntactically valid ``$defs.sha256``-shaped placeholder, overwritten by
#: the real ``compute_content_hash`` result before persisting — mirrors
#: ``mrr.services.obligation.service._PLACEHOLDER_CONTENT_HASH``'s identical
#: rationale: ``compute_content_hash`` excludes the ``content_hash`` key from
#: what it hashes regardless of what is stored there, so this placeholder
#: never leaks into any persisted hash.
_PLACEHOLDER_CORRECTION_RESPONSE_CONTENT_HASH = "sha256:" + "0" * 64

#: The section-3 edge vocabulary type this module records for an ``adapt``
#: ``CorrectionResponse`` (task-packets/E6-T04.yaml derived_decisions (d)) —
#: already declared in ``EDGE_VOCABULARY``, left entirely unused since
#: E3-T06, deliberately distinct from ``adapted_from`` (E6-T01's own
#: TRANSFER-adaptation edge type).
_CORRECTS_EDGE_TYPE = "corrects"

#: docs/spec/03_API_AND_EVENTS.md section 5.2's own enumerated event name for
#: this task — used verbatim, the one E6-T04-relevant event name that
#: section already names (unlike every other E6 task so far, which had to
#: add non-literal event names).
_EVENT_RESPONSE_RECORDED = "correction.response_recorded"

#: docs/spec/03_API_AND_EVENTS.md section 5.2's own enumerated event name,
#: reused verbatim for every E6-T06 delivery-tracking outcome (opened,
#: retried, delivered, exhausted) — the same name task-packets/E6-T03.yaml's
#: own ``notify_affected_practices`` already emits for its own initial
#: attempt, extended here with ``attempt_number``/``channel`` payload keys
#: (task-packets/E6-T06.yaml derived_decisions (g)) rather than inventing a
#: new event type string absent from that enumeration.
_EVENT_NOTIFICATION_SENT = "correction.notification_sent"


@dataclass(frozen=True, slots=True, kw_only=True)
class NotificationRecipient:
    """One recipient practice this correction must be signed-notified to,
    the specific object ids relevant to THAT recipient
    (``notified_object_ids`` — task-packets/E6-T03.yaml's own "the specific
    notified_object_ids relevant to ONE recipient practice"), and the opaque
    transport address ``EnvelopeTransport`` delivers to (mirroring
    ``EnvelopeDeliveryRequest.recipient_endpoint``'s own "no URL/host:port
    format is imposed or parsed here"). Entirely CALLER-supplied: resolving
    WHICH practices/nodes to notify, and which ids matter to each, from
    ``TransferContract.correction_subscription``/``Obligation`` data is
    E6-T01/E6-T02 wiring, out of this task's scope (see the module
    docstring's own "Deliberately deferred" section).
    """

    recipient_practice_id: str
    recipient_node_id: str
    recipient_endpoint: str
    notified_object_ids: Sequence[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class LocalNotificationImpact:
    """The RECEIVING side's own, LOCAL-ONLY result of processing one
    accepted ``CorrectionNotification`` (``receive_correction_notification``'s
    return value). ``locally_impacted_object_ids`` is the computed impacted
    set — explicit even when empty (MRR-NFR-012) — never a copy of the
    remote ``CorrectionEvent`` and never a disposition
    (accept/adapt/reject/defer, E6-T04).
    """

    notification_id: str
    locally_impacted_object_ids: frozenset[str]


def _correction_to_stored_object(correction: CorrectionEvent) -> StoredObject:
    """Convert an already-valid ``CorrectionEvent`` into the generic
    ``StoredObject`` ``mrr.domain.repositories.ObjectRepository`` persists.
    ``body`` is a plain ``model_dump_json(exclude_none=True)`` round trip,
    matching every other service's own ``_*_to_stored_object`` helper.
    """
    body: dict[str, Any] = json.loads(correction.model_dump_json(exclude_none=True))
    return StoredObject(
        id=correction.id,
        api_version=correction.api_version,
        kind=correction.kind,
        practice_id=correction.practice_id,
        revision=correction.revision,
        created_at=correction.created_at,
        created_by=correction.created_by,
        content_hash=correction.content_hash,
        supersedes=correction.supersedes,
        labels=correction.labels,
        body=body,
    )


class CorrectionImpactService:
    """docs/spec/01_SYSTEM_SPEC.md section 7.8 ("Correction Impact Service"),
    implemented per task-packets/E3-T06.yaml. See the module docstring for
    the full design rationale.
    """

    def __init__(
        self,
        object_repository: ObjectRepository,
        edge_repository: EdgeRepository,
        claim_service: ClaimService,
        event_log: _EventJournal,
        record: RecordRevisionWithEvent,
        record_event: RecordEventOnly | None = None,
        record_revision_with_edges: RecordRevisionWithEdgesAndEvent | None = None,
        delivery_pending_store: DeliveryPendingStore | None = None,
    ) -> None:
        self._object_repository = object_repository
        self._edge_repository = edge_repository
        self._claim_service = claim_service
        self._event_log = event_log
        self._record = record
        # E6-T03 addition. OPTIONAL (default None) so this service's
        # existing construction call sites outside this task's own
        # allowed_paths (tests/unit/services/projection/test_service.py,
        # tests/e2e/test_e2e_003_correction_propagation.py — neither of
        # which ever calls notify_affected_practices) keep working
        # unmodified. See the module docstring's "E6-T03 additions" section.
        self._record_event = record_event
        # E6-T04 addition. OPTIONAL (default None) for the identical reason
        # — existing construction call sites that never call
        # record_response keep working unmodified. See the module
        # docstring's "E6-T04 addition" section.
        self._record_revision_with_edges = record_revision_with_edges
        # E6-T06 addition. OPTIONAL (default None) for the identical reason
        # — existing construction call sites that never call any of the five
        # new delivery-tracking methods keep working unmodified. See the
        # module docstring's "E6-T06 additions" section.
        self._delivery_pending_store = delivery_pending_store

    # ------------------------------------------------------------------
    # Recording (MRR-FR-090): one-time creation, revision 1.
    # ------------------------------------------------------------------

    def record(
        self,
        correction: CorrectionEvent,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """Persist ``correction`` as revision 1, plus a ``correction.recorded``
        event, atomically. Rejects any initial status other than
        ``CORRECTION_LIFECYCLE.initial_state`` ("OPEN").

        ``correction`` must already be a fully valid ``CorrectionEvent`` —
        its own ``id``/``content_hash``/``created_at``/``created_by`` are
        minted by the caller, matching every other ``create()``/``record()``
        in this codebase; ``correction.revision`` must be ``1``. Per
        MRR-FR-090, the schema already requires ``affected_objects``,
        ``reason``, ``severity``, ``evidence_refs``, and
        ``requested_action`` to be present and non-empty where the schema
        says so — nothing about that is re-checked here.
        """
        if correction.status != CORRECTION_LIFECYCLE.initial_state:
            raise InvalidTransitionError(
                CORRECTION_LIFECYCLE.name, _NEW_CORRECTION_SENTINEL_STATE, correction.status
            )
        if correction.revision != 1:
            raise ValueError(
                f"CorrectionEvent.revision must be 1 for record(), got {correction.revision!r}"
            )

        obj = _correction_to_stored_object(correction)
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type="correction.recorded",
            occurred_at=datetime.now(UTC),
            actor=actor,
            policy_version=policy_version,
            causation_id=None,
            correlation_id=correlation_id,
            object_id=correction.id,
            object_revision=1,
            payload={
                "correction_type": correction.correction_type,
                "severity": correction.severity,
                "affected_object_ids": [ref.id for ref in correction.affected_objects],
            },
        )
        stored, _ = self._record(obj, None, event)
        return stored

    # ------------------------------------------------------------------
    # Impact propagation (MRR-FR-091/092/093): repeatable, idempotent.
    # ------------------------------------------------------------------

    def propagate_impact(
        self,
        correction_id: Urn,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """Compute the current downstream impact set for ``correction_id``
        (``mrr.domain.correction_impact.compute_impact`` over the real typed
        edge graph, seeded from the correction's own ``affected_objects``),
        write it onto the correction's ``impact_objects`` field if it
        changed, and mark every impacted ``Claim`` ``review_required``
        (skipping any claim already at least that strict — see the module
        docstring's "Idempotency" section).

        Safe to call repeatedly: if the computed impact set already matches
        the correction's current ``impact_objects``, no new correction
        revision is written; a claim already ``review_required`` (or
        terminal) is never re-transitioned. Returns the correction's latest
        stored revision (unchanged if this call was a no-op).

        Raises:
            CorrectionNotFoundError: ``correction_id`` resolves to no stored
                object at all.
        """
        latest = self._get_latest_correction_or_raise(correction_id)
        seed_ids: set[str] = {ref["id"] for ref in latest.body["affected_objects"]}
        edges = self._gather_impact_edges(seed_ids)
        impacted = compute_impact(seed_ids, edges)

        current_impact_objects = set(latest.body.get("impact_objects", []))
        if impacted != current_impact_objects:
            latest = self._write_impact_objects(
                latest,
                impacted,
                actor=actor,
                policy_version=policy_version,
                correlation_id=correlation_id,
            )

        for object_id in sorted(impacted):
            self._require_review_if_needed(
                object_id, actor=actor, policy_version=policy_version, correlation_id=correlation_id
            )

        return latest

    # ------------------------------------------------------------------
    # Cross-practice correction notification (MRR-FR-094, E6-T03) —
    # SENDING side.
    # ------------------------------------------------------------------

    def notify_affected_practices(
        self,
        correction_id: Urn,
        *,
        recipients: Sequence[NotificationRecipient],
        transport: EnvelopeTransport,
        sender_node_id: Urn,
        notifying_practice_id: Urn,
        signing_key: Ed25519PrivateKey,
        signing_key_id: str,
        sent_at: datetime,
        expires_at: datetime,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """Mint+sign one ``CorrectionNotification`` per recipient in
        ``recipients`` NOT already recorded ``"sent"`` for this correction,
        wrap each in a ``NodeMessageEnvelope``
        (``payload_kind="CorrectionNotification"``), attempt delivery via
        ``transport``, drive ``CORRECTION_LIFECYCLE`` forward (OPEN ->
        IMPACT_ANALYSIS -> NOTIFYING -> AWAITING_RESPONSES, continuing to
        DELIVERY_PENDING within this same call if any delivery attempt
        reports ``"failed"``), and record one ``correction.notification_sent``
        event per attempted recipient. See the module docstring's "E6-T03
        additions" section for the full design.

        Idempotent per recipient: a recipient already recorded ``"sent"``
        for this correction is skipped entirely (no delivery attempt, no
        event, no revision contribution); if every recipient in
        ``recipients`` is already ``"sent"``, this call is a complete no-op
        and returns the correction's CURRENT latest revision unchanged.

        Args:
            correction_id: the already-recorded ``CorrectionEvent`` to
                notify recipients about (typically already
                ``propagate_impact``-ed, though this method itself never
                inspects ``impact_objects``).
            recipients: the caller-resolved recipient list — see
                :class:`NotificationRecipient`'s own docstring for why this
                is a plain caller-supplied argument, never resolved from
                ``TransferContract``/``Obligation`` data here.
            transport: the abstract delivery port (test fake in unit tests;
                a real mTLS implementation, when one exists, in production).
            sender_node_id: this practice's own sending node id (the
                envelope's ``sender_node_id``).
            notifying_practice_id: this practice's own id — the notifying
                practice per MRR-FR-094; becomes both the notification's own
                ``notifying_practice_id``/``signature.signer_practice_id``
                and the envelope's ``sender_practice_id``/
                ``signature.signer_practice_id``.
            signing_key: this practice's own Ed25519 private key, used to
                sign BOTH the notification and the wrapping envelope. Key
                management itself is E5's scope, out of this task's own.
            signing_key_id: the key id for ``signing_key``.
            sent_at: the shared ``sent_at`` for every notification/envelope
                minted by this call.
            expires_at: the shared ``expires_at`` for every notification/
                envelope minted by this call (must be strictly after
                ``sent_at`` — enforced by both contracts' own
                ``model_validator``s).
            actor: MRR-NFR-001 provenance — who/what triggered this call.
            policy_version: MRR-NFR-001 provenance.
            correlation_id: MRR-NFR-001 provenance.

        Returns:
            The correction's latest ``StoredObject`` — a NEW revision only
            if its status actually advanced this call, otherwise the
            unchanged current revision.

        Raises:
            CorrectionNotFoundError: ``correction_id`` resolves to no stored
                object at all.
            InvalidTransitionError: an attempted lifecycle hop is not a
                legal ``CORRECTION_LIFECYCLE`` edge (guards against this
                method's own hop-chain logic ever drifting from the frozen
                lifecycle) — nothing is persisted.
            ValueError: more than one recipient's event must be recorded in
                this call (a second+ pending recipient, or ANY pending
                recipient when the correction's status does not change) but
                this service was constructed without a ``record_event``
                dependency.
        """
        latest = self._get_latest_correction_or_raise(correction_id)

        already_sent = self._already_sent_recipient_ids(correction_id)
        pending_recipients = [
            recipient
            for recipient in recipients
            if recipient.recipient_practice_id not in already_sent
        ]
        if not pending_recipients:
            return latest

        delivery_results: list[tuple[NotificationRecipient, CorrectionNotification, str, str]] = []
        for recipient in pending_recipients:
            notification = self._build_and_sign_notification(
                latest,
                recipient,
                notifying_practice_id=notifying_practice_id,
                signing_key=signing_key,
                signing_key_id=signing_key_id,
                sent_at=sent_at,
                expires_at=expires_at,
            )
            envelope = self._build_and_sign_envelope(
                notification,
                sender_node_id=sender_node_id,
                sender_practice_id=notifying_practice_id,
                recipient_node_id=recipient.recipient_node_id,
                signing_key=signing_key,
                signing_key_id=signing_key_id,
                sent_at=sent_at,
                expires_at=expires_at,
            )
            outcome = transport.send(
                EnvelopeDeliveryRequest(
                    envelope=envelope, recipient_endpoint=recipient.recipient_endpoint
                )
            )
            delivery_results.append((recipient, notification, envelope.message_id, outcome.status))

        any_failed = any(status == "failed" for _, _, _, status in delivery_results)
        current_status = latest.body["status"]
        final_status = self._target_status_after_notification(current_status, any_failed=any_failed)
        status_changed = final_status != current_status

        now = datetime.now(UTC)
        new_obj: StoredObject | None = None
        new_revision = latest.revision
        if status_changed:
            new_body = dict(latest.body)
            new_body["status"] = final_status
            new_revision = latest.revision + 1
            new_body["revision"] = new_revision
            new_body["created_at"] = now.isoformat()
            new_body["created_by"] = actor
            new_content_hash = compute_content_hash(new_body)
            new_body["content_hash"] = new_content_hash

            # Re-run the CorrectionEvent contract's own validation against
            # the exact revision body about to be persisted — matches
            # _write_impact_objects's identical "re-check before
            # persisting" stance.
            CorrectionEvent.model_validate(new_body)

            new_obj = StoredObject(
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

        causation_id = self._last_event_id_for(latest.id)
        events = [
            DomainEvent(
                id=new_urn("domain-event"),
                event_type="correction.notification_sent",
                occurred_at=now,
                actor=actor,
                policy_version=policy_version,
                causation_id=causation_id,
                correlation_id=correlation_id,
                object_id=latest.id,
                object_revision=new_revision,
                payload={
                    "recipient_practice_id": recipient.recipient_practice_id,
                    "notification_id": notification.notification_id,
                    "message_id": message_id,
                    "delivery_status": "sent" if status == "delivered" else "pending",
                },
            )
            for recipient, notification, message_id, status in delivery_results
        ]

        if new_obj is not None:
            stored, _ = self._record(new_obj, latest.revision, events[0])
            latest = stored
            remaining_events = events[1:]
        else:
            remaining_events = events

        if remaining_events:
            if self._record_event is None:
                raise ValueError(
                    "CorrectionImpactService was constructed without a record_event "
                    "dependency (bind_event_unit_of_work), required here to append more "
                    "than one correction.notification_sent event per "
                    "notify_affected_practices call"
                )
            for event in remaining_events:
                self._record_event(event)

        return latest

    # ------------------------------------------------------------------
    # Cross-practice correction notification (MRR-FR-094, E6-T03) —
    # RECEIVING side.
    # ------------------------------------------------------------------

    def receive_correction_notification(
        self,
        envelope: NodeMessageEnvelope,
        *,
        this_node_id: Urn,
        trusted_notifying_practice_id: Urn,
        ring: KeyRing,
        already_processed_envelope: AlreadyProcessed,
        already_processed_notification: Callable[[str], bool],
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
        at: datetime | None = None,
    ) -> LocalNotificationImpact:
        """Accept one inbound ``CorrectionNotification`` and recompute its
        LOCAL impact over THIS practice's own graph. See the module
        docstring's "E6-T03 additions" section for the full check ordering
        and design rationale.

        Never creates, stores, or mutates any copy of the remote
        ``CorrectionEvent`` referenced by the notification, and never
        decides accept/adapt/reject/defer toward the correction (E6-T04) —
        the only local mutation this method may cause is the EXISTING,
        unchanged ``ClaimService.require_review`` transition on a claim
        already resolvable in THIS practice's own object repository.

        Args:
            envelope: the received, not-yet-trusted ``NodeMessageEnvelope``
                whose payload is expected to be a ``CorrectionNotification``.
            this_node_id: this receiving node's own id (forwarded to
                ``validate_inbound_envelope``, UNCHANGED).
            trusted_notifying_practice_id: the id of the practice this call
                trusts as BOTH the envelope's transport sender AND the
                notification's own notifying practice.
            ring: ``trusted_notifying_practice_id``'s own trusted
                ``KeyRing``, used to verify both signatures.
            already_processed_envelope: the envelope-level replay predicate
                (forwarded to ``validate_inbound_envelope``, UNCHANGED) —
                keys on the envelope's own ``message_id``.
            already_processed_notification: the notification-level replay
                predicate — a SEPARATE namespace, keyed on the
                notification's own ``notification_id``.
            actor: MRR-NFR-001 provenance for any resulting
                ``ClaimService.require_review`` transition.
            policy_version: MRR-NFR-001 provenance.
            correlation_id: MRR-NFR-001 provenance.
            at: the evaluation instant for every window/validity check in
                this call (the envelope's own, the notification's own
                signature-key validity, and the notification's own
                validity window) — computed ONCE and shared across all of
                them so they can never disagree by a few microseconds.
                Defaults to ``datetime.now(UTC)``.

        Returns:
            A :class:`LocalNotificationImpact` naming the notification's own
            id and the computed LOCAL impacted set (explicit even when
            empty — MRR-NFR-012).

        Raises:
            (from ``validate_inbound_envelope``, UNCHANGED) any of its own
                typed failures — the envelope is rejected before its
                payload is ever parsed as a ``CorrectionNotification``.
            ValueError: ``envelope.payload_kind`` is not
                ``"CorrectionNotification"``.
            pydantic.ValidationError: ``envelope.payload`` does not validate
                as a ``CorrectionNotification``.
            mrr.domain.exceptions.CorrectionNotificationSignerMismatchError,
            mrr.domain.exceptions.UnknownKeyIdError,
            mrr.domain.exceptions.CorrectionNotificationKeyNotValidError,
            mrr.crypto.exceptions.SignatureVerificationError,
            mrr.crypto.exceptions.UnsupportedAlgorithmError: the
                notification's OWN signature fails to resolve/verify (see
                ``resolve_trusted_correction_notification_key``).
            mrr.domain.exceptions.CorrectionNotificationNotWithinValidityWindowError:
                the evaluation instant is outside the notification's OWN
                ``[sent_at, expires_at)`` window.
            mrr.domain.exceptions.CorrectionNotificationAlreadyProcessedError:
                ``already_processed_notification`` reports the
                notification's own ``notification_id`` as already seen.

        None of these leave any local claim touched.
        """
        evaluation_instant = at if at is not None else datetime.now(UTC)

        validate_inbound_envelope(
            envelope,
            this_node_id=this_node_id,
            trusted_sender_practice_id=trusted_notifying_practice_id,
            ring=ring,
            already_processed=already_processed_envelope,
            at=evaluation_instant,
        )

        if envelope.payload_kind != _CORRECTION_NOTIFICATION_PAYLOAD_KIND:
            raise ValueError(
                f"NodeMessageEnvelope {envelope.message_id!r} payload_kind is "
                f"{envelope.payload_kind!r}, not "
                f"{_CORRECTION_NOTIFICATION_PAYLOAD_KIND!r}"
            )
        notification = CorrectionNotification.model_validate(envelope.payload)

        # The notification's OWN signature, independent of the envelope's
        # already-verified transport signature.
        resolve_trusted_correction_notification_key(
            notification, trusted_notifying_practice_id, ring, at=evaluation_instant
        )

        if not (notification.sent_at <= evaluation_instant < notification.expires_at):
            raise CorrectionNotificationNotWithinValidityWindowError(
                notification.notification_id,
                notification.sent_at,
                notification.expires_at,
                evaluation_instant,
            )

        if already_processed_notification(notification.notification_id):
            raise CorrectionNotificationAlreadyProcessedError(notification.notification_id)

        seed_ids: set[str] = set(notification.notified_object_ids)
        edges = self._gather_local_impact_edges(seed_ids)
        impacted = compute_impact(seed_ids, edges)

        for object_id in sorted(impacted):
            self._require_review_if_needed(
                object_id, actor=actor, policy_version=policy_version, correlation_id=correlation_id
            )

        return LocalNotificationImpact(
            notification_id=notification.notification_id,
            locally_impacted_object_ids=frozenset(impacted),
        )

    # ------------------------------------------------------------------
    # Cross-practice correction notification (MRR-FR-084, E6-T04) — the
    # RECEIVING practice's own local accept/adapt/reject/defer response.
    # ------------------------------------------------------------------

    def record_response(
        self,
        *,
        correction_notification_id: Urn,
        notifying_practice_id: Urn,
        origin_correction_event_id: Urn,
        origin_correction_event_revision: int,
        notified_object_ids: Sequence[Urn],
        responding_practice_id: Urn,
        decision: CorrectionResponseDecision,
        reason: str | None = None,
        adaptations: Sequence[CorrectionResponseAdaptation] | None = None,
        already_responded: Callable[[str], bool],
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """Record THIS receiving practice's own local disposition toward one
        already-received ``CorrectionNotification`` (E6-T03's own
        ``receive_correction_notification``, called separately and BEFORE
        this method — its trust checks are trusted, not re-run here; no
        signature is verified by this method at all).

        Mints a brand-new, standalone, UNSIGNED, single-revision
        :class:`mrr.contracts.correction_response.CorrectionResponse` at
        revision 1 — never a copy or mutation of the sender's remote
        ``CorrectionEvent``, which this practice never stores (E6-T03's own
        invariant). ``correction_notification_id``,
        ``notifying_practice_id``, ``origin_correction_event_id``,
        ``origin_correction_event_revision``, and ``notified_object_ids`` are
        plain scalar fields the caller copies from the already-validated
        ``CorrectionNotification`` — never a nested reference to that
        contract (task-packets/E6-T04.yaml derived_decisions (g)).

        For ``decision == "adapt"``, every ``adaptations[].adapted_object_id``
        MUST already exist locally — verified via ``ObjectRepository.
        get_latest`` BEFORE anything is built or persisted — and records one
        ``corrects`` edge per adaptation entry (source=``adapted_object_id``,
        target=``notified_object_id``), written atomically with the
        ``CorrectionResponse`` revision and the ``correction.response_
        recorded`` event via :func:`bind_revision_with_edges_unit_of_work`.

        Args:
            correction_notification_id: the addressed notification's own id
                (this method's own idempotency key, via
                ``already_responded``).
            notifying_practice_id: copied from the notification.
            origin_correction_event_id: copied from the notification.
            origin_correction_event_revision: copied from the notification.
            notified_object_ids: copied from the notification — this
                response's own ``notified_object_ids``, and the membership
                set every ``adaptations[].notified_object_id`` is checked
                against (contract-level, ``CorrectionResponse``'s own
                ``model_validator``).
            responding_practice_id: THIS practice's own id — the
                ``CorrectionResponse``'s own ``BaseObject.practice_id``.
            decision: exactly one of accept/adapt/reject/defer.
            reason: REQUIRED (non-empty) iff ``decision`` is reject/defer,
                and must be omitted otherwise (MRR-FR-084; enforced by the
                ``CorrectionResponse`` contract's own ``model_validator`` —
                not re-checked here beyond what construction already does).
            adaptations: REQUIRED (non-empty) iff ``decision`` is adapt, and
                must be empty otherwise (contract-enforced, as above).
            already_responded: caller-supplied idempotency predicate, keyed
                by ``correction_notification_id`` — mirrors E6-T03's own
                ``already_processed_notification``. No durable
                processed-notification-id store is built here.
            actor: MRR-NFR-001 provenance.
            policy_version: MRR-NFR-001 provenance.
            correlation_id: MRR-NFR-001 provenance.

        Returns:
            The newly persisted ``CorrectionResponse`` revision-1
            ``StoredObject``.

        Raises:
            CorrectionResponseAlreadyRecordedError:
                ``already_responded(correction_notification_id)`` is
                ``True``. Checked FIRST; nothing is persisted.
            ObjectNotFoundError: an ``adaptations[].adapted_object_id`` does
                not resolve to any locally stored object — checked for
                EVERY entry before anything is built or persisted, so a
                later valid entry never masks an earlier missing one.
            pydantic.ValidationError: the constructed ``CorrectionResponse``
                is not valid (e.g. ``decision`` outside
                accept/adapt/reject/defer, ``reason``/``adaptations``
                inconsistent with ``decision``, or an
                ``adaptations[].notified_object_id`` not a member of
                ``notified_object_ids``). Nothing is persisted.
            ValueError: this service was constructed without a
                ``record_revision_with_edges`` dependency
                (:func:`bind_revision_with_edges_unit_of_work`).

        None of these leave anything persisted.
        """
        if already_responded(correction_notification_id):
            raise CorrectionResponseAlreadyRecordedError(correction_notification_id)

        adaptation_entries = list(adaptations) if adaptations else []

        # Every adapted_object_id must already exist locally — checked for
        # EVERY entry, before anything is built or persisted, so a missing
        # id aborts the whole call even when other entries in the same list
        # reference valid objects (mirrors E6-T01's own identical
        # adapted-decision verification).
        for adaptation in adaptation_entries:
            self._object_repository.get_latest(adaptation.adapted_object_id)

        if self._record_revision_with_edges is None:
            raise ValueError(
                "CorrectionImpactService was constructed without a "
                "record_revision_with_edges dependency "
                "(bind_revision_with_edges_unit_of_work), required by record_response"
            )

        response_id = new_urn("correction-response")
        now = datetime.now(UTC)
        body: dict[str, Any] = {
            "id": response_id,
            "api_version": "mrr/v1alpha1",
            "kind": "CorrectionResponse",
            "practice_id": responding_practice_id,
            "revision": 1,
            "created_at": now.isoformat(),
            "created_by": actor,
            "content_hash": _PLACEHOLDER_CORRECTION_RESPONSE_CONTENT_HASH,
            "correction_notification_id": correction_notification_id,
            "notifying_practice_id": notifying_practice_id,
            "origin_correction_event_id": origin_correction_event_id,
            "origin_correction_event_revision": origin_correction_event_revision,
            "notified_object_ids": list(notified_object_ids),
            "decision": decision,
            "adaptations": [
                {
                    "adapted_object_id": adaptation.adapted_object_id,
                    "notified_object_id": adaptation.notified_object_id,
                }
                for adaptation in adaptation_entries
            ],
        }
        if reason is not None:
            body["reason"] = reason

        content_hash = compute_content_hash(body)
        body["content_hash"] = content_hash

        # Re-run the CorrectionResponse contract's own validation against
        # the EXACT body about to be persisted — matches every other
        # service's own "re-check before persisting" stance. This is what
        # actually enforces decision/reason/adaptations consistency and the
        # adaptations[].notified_object_id membership check.
        CorrectionResponse.model_validate(body)

        obj = StoredObject(
            id=response_id,
            api_version="mrr/v1alpha1",
            kind="CorrectionResponse",
            practice_id=responding_practice_id,
            revision=1,
            created_at=now,
            created_by=actor,
            content_hash=content_hash,
            supersedes=None,
            labels=None,
            body=body,
        )
        edges = [
            TypedEdge(
                id=new_urn("edge"),
                source_id=adaptation.adapted_object_id,
                target_id=adaptation.notified_object_id,
                edge_type=_CORRECTS_EDGE_TYPE,
                created_at=now,
                created_by=actor,
                scope=None,
                status="active",
                practice_id=responding_practice_id,
            )
            for adaptation in adaptation_entries
        ]
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=_EVENT_RESPONSE_RECORDED,
            occurred_at=now,
            actor=actor,
            policy_version=policy_version,
            causation_id=None,
            correlation_id=correlation_id,
            object_id=response_id,
            object_revision=1,
            payload={
                "correction_notification_id": correction_notification_id,
                "decision": decision,
                "adaptation_count": len(adaptation_entries),
            },
        )

        stored, _edges, _appended = self._record_revision_with_edges(obj, None, edges, event)
        return stored

    # ------------------------------------------------------------------
    # Offline recipient delivery tracking (MRR-FR-094, E6-T06) — the durable
    # pending-delivery record task-packets/E6-T03.yaml's own
    # notify_affected_practices explicitly deferred. See the module
    # docstring's "E6-T06 additions" section for the full design.
    # ------------------------------------------------------------------

    def open_pending_delivery(
        self,
        correction_id: Urn,
        *,
        notification_id: Urn,
        recipient_node_id: str,
        notification_expires_at: datetime,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
        at: datetime | None = None,
    ) -> bool:
        """Idempotently open a durable pending-delivery record the FIRST
        time ``recipient_node_id``'s synchronous online attempt at
        delivering ``notification_id`` reports ``"failed"`` (the moment
        ``notify_affected_practices`` chains ``CORRECTION_LIFECYCLE``
        through its already-drawn ``AWAITING_RESPONSES -> DELIVERY_PENDING``
        edge for that recipient).

        Appends a ``correction.notification_sent`` event
        (``delivery_status="pending"``, ``attempt_number=1``,
        ``channel="online"``) ONLY when this call newly opens the record —
        a repeated call for the SAME ``(recipient_node_id, notification_id)``
        is a complete no-op: no duplicate row, no duplicate event.

        Args:
            correction_id: the correction this notification concerns —
                resolved via ``ObjectRepository.get_latest`` (raises
                ``CorrectionNotFoundError`` if absent) so the appended
                event's own ``object_revision`` is always the correction's
                REAL current revision, never a caller-guessed value.
            notification_id: the addressed ``CorrectionNotification``'s own
                stable id.
            recipient_node_id: the recipient node this record tracks.
            notification_expires_at: the addressed notification's own
                ``expires_at`` — copied onto the durable record so retry
                scheduling/exhaustion can be evaluated later without
                re-fetching the notification.
            actor: MRR-NFR-001 provenance.
            policy_version: MRR-NFR-001 provenance.
            correlation_id: MRR-NFR-001 provenance.
            at: the instant this call is made. Defaults to
                ``datetime.now(UTC)``.

        Returns:
            ``True`` if this call newly opened the record, ``False`` for the
            idempotent no-op case.

        Raises:
            CorrectionNotFoundError: ``correction_id`` resolves to no stored
                object at all.
            ValueError: this service was constructed without a
                ``delivery_pending_store`` dependency.
        """
        store = self._require_delivery_pending_store()
        self._require_record_event()
        latest = self._get_latest_correction_or_raise(correction_id)
        evaluation_instant = at if at is not None else datetime.now(UTC)

        newly_opened = store.open_pending_delivery(
            recipient_node_id,
            notification_id,
            correction_id=correction_id,
            notification_expires_at=notification_expires_at,
            at=evaluation_instant,
        )
        if newly_opened:
            self._append_delivery_event(
                correction=latest,
                notification_id=notification_id,
                recipient_node_id=recipient_node_id,
                attempt_number=1,
                delivery_status="pending",
                channel="online",
                occurred_at=evaluation_instant,
                actor=actor,
                policy_version=policy_version,
                correlation_id=correlation_id,
            )
        return newly_opened

    def retry_pending_delivery_online(
        self,
        correction_id: Urn,
        *,
        notification_id: Urn,
        recipient_node_id: str,
        envelope: NodeMessageEnvelope,
        transport: EnvelopeTransport,
        recipient_endpoint: str,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
        at: datetime | None = None,
    ) -> DeliveryPendingRecord:
        """Retry an open pending-delivery record by a FURTHER synchronous
        ``EnvelopeTransport.send`` attempt with the SAME already-signed
        ``envelope`` (never re-signed, never re-minted — task-packets/
        E6-T06.yaml derived_decisions (e)).

        Always appends a ``correction.notification_sent`` event recording
        the outcome (``delivery_status`` is ``"delivered"``, ``"pending"``,
        or ``"exhausted"`` depending on the resulting record's own
        ``status``; ``channel="online"``).

        Raises:
            CorrectionNotFoundError: ``correction_id`` resolves to no stored
                object at all.
            mrr.domain.exceptions.PendingDeliveryNotFoundError: no pending
                record exists for ``(recipient_node_id, notification_id)``
                (``open_pending_delivery`` was never called, or the caller
                passed the wrong pair) — no event is appended.
            mrr.domain.exceptions.InvalidTransitionError: the record is
                already ``delivered``/``exhausted`` — no event is appended.
                This method does not pre-check the record's status before
                attempting delivery (the transport attempt below already
                happened); a caller that only ever retries records returned
                by ``PostgresDeliveryPendingStore.list_due_for_retry`` never
                hits this in practice.
            ValueError: this service was constructed without a
                ``delivery_pending_store`` dependency.
        """
        store = self._require_delivery_pending_store()
        self._require_record_event()
        latest = self._get_latest_correction_or_raise(correction_id)
        evaluation_instant = at if at is not None else datetime.now(UTC)

        outcome = transport.send(
            EnvelopeDeliveryRequest(envelope=envelope, recipient_endpoint=recipient_endpoint)
        )
        record = store.record_retry_attempt(
            recipient_node_id, notification_id, outcome=outcome.status, at=evaluation_instant
        )
        self._append_delivery_event(
            correction=latest,
            notification_id=notification_id,
            recipient_node_id=recipient_node_id,
            attempt_number=record.attempt_count,
            delivery_status=record.status,
            channel="online",
            occurred_at=evaluation_instant,
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
        )
        return record

    def retry_pending_delivery_offline(
        self,
        correction_id: Urn,
        *,
        notification_id: Urn,
        recipient_node_id: str,
        envelope: NodeMessageEnvelope,
        bundle_id: str,
        bundle_nonce: str,
        sender_node_id: Urn,
        sender_practice_id: Urn,
        bundle_created_at: datetime,
        bundle_expires_at: datetime,
        signing_key: Ed25519PrivateKey,
        signing_key_id: str,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
        encryption: BundleEncryption | None = None,
        at: datetime | None = None,
    ) -> tuple[OfflineBundle, DeliveryPendingRecord]:
        """Retry an open pending-delivery record by wrapping the SAME
        already-signed ``envelope`` into a FRESH single-entry
        ``OfflineBundle`` via the UNCHANGED ``mrr.domain.offline_bundle.
        build_outbox_bundle`` — proving the online and offline retry
        channels compose without modifying either (task-packets/E6-T06.yaml
        invariant: "composition with the E5-T06 offline bundle export is
        REUSE, not reimplementation").

        The built bundle gets its OWN new ``bundle_id``/``bundle_nonce`` (so
        it is never itself mistaken for a bundle replay) while carrying the
        SAME unchanged envelope inside it. This method does NOT transmit the
        bundle anywhere — no physical air-gap/file/USB transport medium is
        built here (out of scope, exactly like every prior E5/E6 packet's
        identical deferral); the caller owns actual transmission.

        Because the offline channel has no in-repo delivery-acknowledgement
        mechanism (specification_gaps: no "optional acknowledgement request"
        field exists on ``NodeMessageEnvelope``), this always records
        outcome ``"failed"`` against the tracking store (i.e. "attempted,
        not yet confirmed delivered") — a caller with an out-of-band
        delivery signal calls :meth:`mark_pending_delivery_delivered`
        instead.

        Always appends a ``correction.notification_sent`` event
        (``channel="offline_bundle"``; ``delivery_status`` is ``"pending"``
        or ``"exhausted"`` depending on the resulting record's own
        ``status`` — never ``"delivered"``, since this channel never
        self-reports that outcome).

        Returns:
            The freshly built ``OfflineBundle`` and the updated
            ``DeliveryPendingRecord``.

        Raises:
            CorrectionNotFoundError, PendingDeliveryNotFoundError,
            InvalidTransitionError: see
                :meth:`retry_pending_delivery_online` — identical semantics,
                over the offline channel.
            ValueError: this service was constructed without a
                ``delivery_pending_store`` dependency.
        """
        store = self._require_delivery_pending_store()
        self._require_record_event()
        latest = self._get_latest_correction_or_raise(correction_id)
        evaluation_instant = at if at is not None else datetime.now(UTC)

        bundle = build_outbox_bundle(
            [envelope],
            bundle_id=bundle_id,
            bundle_nonce=bundle_nonce,
            sender_node_id=sender_node_id,
            sender_practice_id=sender_practice_id,
            recipient_node_id=recipient_node_id,
            created_at=bundle_created_at,
            expires_at=bundle_expires_at,
            signing_key=signing_key,
            key_id=signing_key_id,
            encryption=encryption,
        )
        record = store.record_retry_attempt(
            recipient_node_id, notification_id, outcome="failed", at=evaluation_instant
        )
        self._append_delivery_event(
            correction=latest,
            notification_id=notification_id,
            recipient_node_id=recipient_node_id,
            attempt_number=record.attempt_count,
            delivery_status=record.status,
            channel="offline_bundle",
            occurred_at=evaluation_instant,
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
        )
        return bundle, record

    def mark_pending_delivery_delivered(
        self,
        correction_id: Urn,
        *,
        notification_id: Urn,
        recipient_node_id: str,
        channel: DeliveryChannel,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
        at: datetime | None = None,
    ) -> DeliveryPendingRecord:
        """Resolve an open pending-delivery record to ``delivered`` from a
        caller-supplied out-of-band delivery signal — the only way an
        OFFLINE retry (which never self-reports delivery) can ever resolve
        (specification_gaps: no in-repo acknowledgement channel exists).
        Also usable for an online retry the caller confirmed delivered by
        some other means.

        Always appends a ``correction.notification_sent`` event
        (``delivery_status="delivered"``).

        Raises:
            CorrectionNotFoundError, PendingDeliveryNotFoundError,
            InvalidTransitionError: see
                :meth:`retry_pending_delivery_online`.
            ValueError: this service was constructed without a
                ``delivery_pending_store`` dependency.
        """
        store = self._require_delivery_pending_store()
        self._require_record_event()
        latest = self._get_latest_correction_or_raise(correction_id)
        evaluation_instant = at if at is not None else datetime.now(UTC)

        record = store.record_retry_attempt(
            recipient_node_id, notification_id, outcome="delivered", at=evaluation_instant
        )
        self._append_delivery_event(
            correction=latest,
            notification_id=notification_id,
            recipient_node_id=recipient_node_id,
            attempt_number=record.attempt_count,
            delivery_status="delivered",
            channel=channel,
            occurred_at=evaluation_instant,
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
        )
        return record

    def mark_pending_delivery_exhausted(
        self,
        correction_id: Urn,
        *,
        notification_id: Urn,
        recipient_node_id: str,
        reason: str,
        channel: DeliveryChannel,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
        at: datetime | None = None,
    ) -> DeliveryPendingRecord:
        """Explicitly resolve an open pending-delivery record to
        ``exhausted`` for a caller-decided reason (e.g. the recipient
        endpoint is known permanently gone) rather than one discovered as a
        side effect of a retry attempt — mirrors ``mrr.domain.
        delivery_retry.DeliveryPendingStore.mark_exhausted``'s own
        "explicit, caller-decided" framing.

        Always appends a ``correction.notification_sent`` event
        (``delivery_status="exhausted"``) — exhaustion is an explicit
        recorded outcome, never a silent give-up (task-packets/E6-T06.yaml
        invariant).

        Raises:
            CorrectionNotFoundError: ``correction_id`` resolves to no stored
                object at all.
            ValueError: ``reason`` is empty, or this service was constructed
                without a ``delivery_pending_store`` dependency.
            mrr.domain.exceptions.PendingDeliveryNotFoundError,
            mrr.domain.exceptions.InvalidTransitionError: see
                :meth:`retry_pending_delivery_online`.
        """
        store = self._require_delivery_pending_store()
        self._require_record_event()
        latest = self._get_latest_correction_or_raise(correction_id)
        evaluation_instant = at if at is not None else datetime.now(UTC)

        record = store.mark_exhausted(
            recipient_node_id, notification_id, reason=reason, at=evaluation_instant
        )
        self._append_delivery_event(
            correction=latest,
            notification_id=notification_id,
            recipient_node_id=recipient_node_id,
            attempt_number=record.attempt_count,
            delivery_status="exhausted",
            channel=channel,
            occurred_at=evaluation_instant,
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
        )
        return record

    # ------------------------------------------------------------------
    # Internal helpers.
    # ------------------------------------------------------------------

    def _get_latest_correction_or_raise(self, correction_id: str) -> StoredObject:
        try:
            return self._object_repository.get_latest(correction_id)
        except ObjectNotFoundError:
            raise CorrectionNotFoundError(correction_id) from None

    def _last_event_id_for(self, object_id: str) -> str | None:
        """The id of the most recently appended event for ``object_id``, or
        ``None`` if there is none yet — identical rationale to
        ``ClaimService._last_event_id_for``.
        """
        matching_ids = [
            appended.event.id
            for appended in self._event_log.read_all()
            if appended.event.object_id == object_id
        ]
        return matching_ids[-1] if matching_ids else None

    def _gather_impact_edges(self, seed_ids: set[str]) -> list[TypedEdge]:
        """Breadth-first-expand the correction's downstream closure via
        ``EdgeRepository.edges_to``, collecting only impact-typed edges. See
        the module docstring's "Gathering edges" section for why this exists
        and why it does not itself decide the final impacted set.
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

    def _write_impact_objects(
        self,
        latest: StoredObject,
        impacted: set[str],
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """Persist a new correction revision carrying the freshly computed
        ``impact_objects`` (sorted for a deterministic, reproducible
        ``content_hash``), plus a ``correction.impact_computed`` event,
        atomically. Only called when the impact set actually changed
        relative to the correction's current revision (see
        ``propagate_impact``'s no-op guard).
        """
        new_body = dict(latest.body)
        new_body["impact_objects"] = sorted(impacted)
        new_revision = latest.revision + 1
        now = datetime.now(UTC)
        new_body["revision"] = new_revision
        new_body["created_at"] = now.isoformat()
        new_body["created_by"] = actor
        new_content_hash = compute_content_hash(new_body)
        new_body["content_hash"] = new_content_hash

        # Re-run the CorrectionEvent contract's own validation against the
        # exact revision body about to be persisted — matches
        # ClaimService._transition's identical "re-check before persisting"
        # stance for its own headline gate.
        CorrectionEvent.model_validate(new_body)

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
            event_type="correction.impact_computed",
            occurred_at=now,
            actor=actor,
            policy_version=policy_version,
            causation_id=self._last_event_id_for(latest.id),
            correlation_id=correlation_id,
            object_id=latest.id,
            object_revision=new_revision,
            payload={"impact_objects": sorted(impacted)},
        )
        stored, _ = self._record(obj, latest.revision, event)
        return stored

    def _require_review_if_needed(
        self,
        object_id: str,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> None:
        """Transition ``object_id`` to ``review_required`` via
        ``ClaimService.require_review`` iff it currently resolves to a
        ``Claim`` not already at least that strict. Silently does nothing
        for an id that resolves to no stored object at all, or to a
        non-``Claim`` kind — see the module docstring's "What counts as
        affected" section for the claims-only scope, and the ``EdgeRepository``
        having no existence constraint on edge endpoints (there is no
        foreign key from ``edges`` to ``objects`` — an edge may reference an
        id this repository has never seen).
        """
        try:
            obj = self._object_repository.get_latest(object_id)
        except ObjectNotFoundError:
            return
        if obj.kind != _CLAIM_KIND:
            return
        if obj.body.get("status") in _CLAIM_REVIEW_ALREADY_SATISFIED_STATUSES:
            return
        self._claim_service.require_review(
            object_id, actor=actor, policy_version=policy_version, correlation_id=correlation_id
        )

    # ------------------------------------------------------------------
    # E6-T03 internal helpers — sending side.
    # ------------------------------------------------------------------

    def _already_sent_recipient_ids(self, correction_id: str) -> set[str]:
        """The set of ``recipient_practice_id``s already recorded
        ``delivery_status == "sent"`` in a prior ``correction.
        notification_sent`` event for ``correction_id`` — the durable,
        append-only-log-backed record ``notify_affected_practices``'s own
        idempotency check reads (AGENTS.md's source-of-truth discipline:
        "the append-only domain event log is authoritative for audit
        history"). No separate persisted state is built for this.

        Reviewer-noted nuance: idempotency is keyed on ``recipient_practice_
        id`` ONLY, never on the specific ``notified_object_ids`` a prior
        call actually sent. A recipient once recorded ``"sent"`` for THIS
        correction is therefore never re-notified by a later
        ``notify_affected_practices`` call, even if that later call passes
        a different (or larger) ``notified_object_ids`` set for the same
        recipient — e.g. because ``propagate_impact`` discovered additional
        downstream impact after the first notification round already went
        out. Whether/how a recipient should be re-notified when the
        correction's own known impact set grows after their first
        acknowledged delivery is a future specification question, not
        invented here (mirrors this task's own stance on E6-T06's deferred
        retry/redelivery machinery).
        """
        return {
            appended.event.payload["recipient_practice_id"]
            for appended in self._event_log.read_all()
            if appended.event.object_id == correction_id
            and appended.event.event_type == "correction.notification_sent"
            and appended.event.payload.get("delivery_status") == "sent"
        }

    def _target_status_after_notification(self, current_status: str, *, any_failed: bool) -> str:
        """Compute the correction's status AFTER this ``notify_affected_
        practices`` call, validating every hop of the chain via
        ``CORRECTION_LIFECYCLE.assert_transition`` (task-packets/
        E6-T03.yaml derived_decisions (c)).

        From ``OPEN`` (never notified before): always advances through
        IMPACT_ANALYSIS -> NOTIFYING -> AWAITING_RESPONSES, continuing to
        DELIVERY_PENDING within this same call only if ``any_failed``. From
        ``AWAITING_RESPONSES`` (already fully notified once): advances to
        DELIVERY_PENDING only if ``any_failed`` and it has not already;
        otherwise unchanged. From ``DELIVERY_PENDING`` (or any other
        status): unchanged — no further edge is drawn out of
        DELIVERY_PENDING (the module docstring's/``mrr.domain.lifecycles``'s
        own open question, left exactly as-is), and no other status is ever
        actually reached by THIS method (IMPACT_ANALYSIS/NOTIFYING are
        transient within one call, never separately persisted).
        """
        if current_status == "OPEN":
            hops = ["IMPACT_ANALYSIS", "NOTIFYING", "AWAITING_RESPONSES"]
            if any_failed:
                hops.append("DELIVERY_PENDING")
        elif current_status == "AWAITING_RESPONSES" and any_failed:
            hops = ["DELIVERY_PENDING"]
        else:
            hops = []

        status = current_status
        for next_status in hops:
            CORRECTION_LIFECYCLE.assert_transition(status, next_status)
            status = next_status
        return status

    def _build_and_sign_notification(
        self,
        correction: StoredObject,
        recipient: NotificationRecipient,
        *,
        notifying_practice_id: str,
        signing_key: Ed25519PrivateKey,
        signing_key_id: str,
        sent_at: datetime,
        expires_at: datetime,
    ) -> CorrectionNotification:
        """Build and sign one ``CorrectionNotification`` for ``recipient``,
        self-contained from ``correction``'s own stored body (``correction_
        type``/``severity``/``reason``/``requested_action``/
        ``replacement_object_id`` copied in — task-packets/E6-T03.yaml
        derived_decisions (a)) — mirrors ``EvidenceCrateSealer.seal``'s own
        draft-then-resign convention: build with a placeholder signature,
        compute ``content_hash`` over the ADR-0004 ``exclude_none=True``
        form, sign that same form, then re-validate.
        """
        body = correction.body
        placeholder_signature = Signature(
            signer_practice_id=notifying_practice_id,
            key_id=signing_key_id,
            algorithm="Ed25519",
            signed_at=sent_at,
            value=_PLACEHOLDER_SIGNATURE_VALUE,
        )
        draft = CorrectionNotification(
            notification_id=new_urn("correction-notification"),
            correction_id=correction.id,
            correction_revision=correction.revision,
            notifying_practice_id=notifying_practice_id,
            recipient_practice_id=recipient.recipient_practice_id,
            notified_object_ids=list(recipient.notified_object_ids),
            correction_type=body["correction_type"],
            severity=body["severity"],
            reason=body["reason"],
            requested_action=body["requested_action"],
            replacement_object_id=body.get("replacement_object_id"),
            content_hash="sha256:" + "0" * 64,  # placeholder; recomputed below
            nonce=secrets.token_hex(16),
            sent_at=sent_at,
            expires_at=expires_at,
            signature=placeholder_signature,
        )

        # ADR-0004: hash and sign over the SAME exclude_none=True body a
        # caller would persist or transmit — never a second, null-including
        # representation.
        payload_body: dict[str, Any] = json.loads(draft.model_dump_json(exclude_none=True))
        payload_body["content_hash"] = compute_content_hash(payload_body)

        signature_value = sign_object(signing_key, payload_body)
        signature = Signature(
            signer_practice_id=notifying_practice_id,
            key_id=signing_key_id,
            algorithm="Ed25519",
            signed_at=sent_at,
            value=signature_value,
        )
        payload_body["signature"] = signature.model_dump(mode="json")
        return CorrectionNotification.model_validate(payload_body)

    def _build_and_sign_envelope(
        self,
        notification: CorrectionNotification,
        *,
        sender_node_id: str,
        sender_practice_id: str,
        recipient_node_id: str,
        signing_key: Ed25519PrivateKey,
        signing_key_id: str,
        sent_at: datetime,
        expires_at: datetime,
    ) -> NodeMessageEnvelope:
        """Wrap an already-signed ``CorrectionNotification`` in a
        ``NodeMessageEnvelope`` and sign the envelope's OWN transport
        signature — the UNCHANGED E5-T03 contract, never re-modeled here.
        ``payload_content_hash`` is the notification's own ``content_hash``,
        which is what makes ``validate_inbound_envelope``'s unmodified
        payload-hash consistency check hold for this payload kind.
        """
        payload_body: dict[str, Any] = json.loads(notification.model_dump_json(exclude_none=True))
        placeholder_signature = Signature(
            signer_practice_id=sender_practice_id,
            key_id=signing_key_id,
            algorithm="Ed25519",
            signed_at=sent_at,
            value=_PLACEHOLDER_SIGNATURE_VALUE,
        )
        draft = NodeMessageEnvelope(
            message_id=new_urn("node-message-envelope"),
            sender_node_id=sender_node_id,
            sender_practice_id=sender_practice_id,
            recipient_node_id=recipient_node_id,
            sent_at=sent_at,
            expires_at=expires_at,
            payload_kind=_CORRECTION_NOTIFICATION_PAYLOAD_KIND,
            payload_content_hash=notification.content_hash,
            payload=payload_body,
            signature=placeholder_signature,
        )

        envelope_body: dict[str, Any] = json.loads(draft.model_dump_json(exclude_none=True))
        signature_value = sign_object(signing_key, envelope_body)
        signature = Signature(
            signer_practice_id=sender_practice_id,
            key_id=signing_key_id,
            algorithm="Ed25519",
            signed_at=sent_at,
            value=signature_value,
        )
        envelope_body["signature"] = signature.model_dump(mode="json")
        return NodeMessageEnvelope.model_validate(envelope_body)

    # ------------------------------------------------------------------
    # E6-T03 internal helper — receiving side.
    # ------------------------------------------------------------------

    def _gather_local_impact_edges(self, seed_ids: set[str]) -> list[TypedEdge]:
        """Deliberate, documented DUPLICATE of ``_gather_impact_edges`` for
        the RECEIVING side of a cross-practice correction notification —
        task-packets/E6-T03.yaml derived_decisions (d): the two call sites
        have different seed provenance (a LOCAL correction's own
        ``affected_objects`` vs. a received notification's
        ``notified_object_ids``) even though the traversal shape is
        identical; kept small and side-by-side rather than force-shared
        across two services with different callers, mirroring
        ``mrr.services.claim.service.bind_edge_unit_of_work``'s own
        documented-duplication precedent.
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
    # E6-T06 internal helpers.
    # ------------------------------------------------------------------

    def _require_delivery_pending_store(self) -> DeliveryPendingStore:
        if self._delivery_pending_store is None:
            raise ValueError(
                "CorrectionImpactService was constructed without a delivery_pending_store "
                "dependency, required for delivery-tracking methods (open_pending_delivery, "
                "retry_pending_delivery_online, retry_pending_delivery_offline, "
                "mark_pending_delivery_delivered, mark_pending_delivery_exhausted)"
            )
        return self._delivery_pending_store

    def _require_record_event(self) -> RecordEventOnly:
        """Checked BEFORE any of the five E6-T06 methods mutates the
        delivery-pending store — a misconfigured service (``delivery_
        pending_store`` wired but ``record_event`` is not) fails closed
        before any write, rather than mutating the store and only then
        discovering the event cannot be appended.
        """
        if self._record_event is None:
            raise ValueError(
                "CorrectionImpactService was constructed without a record_event dependency "
                "(bind_event_unit_of_work), required to append a correction.notification_sent "
                "event for a delivery-tracking outcome"
            )
        return self._record_event

    def _append_delivery_event(
        self,
        *,
        correction: StoredObject,
        notification_id: str,
        recipient_node_id: str,
        attempt_number: int,
        delivery_status: str,
        channel: str,
        occurred_at: datetime,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> None:
        """Append one ``correction.notification_sent`` event via the
        EXISTING, unmodified ``record_event`` (ADR-0007 event-only path,
        :func:`bind_event_unit_of_work`) — never a new ``CorrectionEvent``
        revision, since none of the five E6-T06 methods above ever writes
        ``CorrectionEvent.status``/drives ``CORRECTION_LIFECYCLE``.
        """
        if self._record_event is None:
            raise ValueError(
                "CorrectionImpactService was constructed without a record_event dependency "
                "(bind_event_unit_of_work), required to append a correction.notification_sent "
                "event for a delivery-tracking outcome"
            )
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=_EVENT_NOTIFICATION_SENT,
            occurred_at=occurred_at,
            actor=actor,
            policy_version=policy_version,
            causation_id=self._last_event_id_for(correction.id),
            correlation_id=correlation_id,
            object_id=correction.id,
            object_revision=correction.revision,
            payload={
                "notification_id": notification_id,
                "recipient_node_id": recipient_node_id,
                "attempt_number": attempt_number,
                "delivery_status": delivery_status,
                "channel": channel,
            },
        )
        self._record_event(event)
