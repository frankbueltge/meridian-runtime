"""Property test for the RECEIVING side of a cross-practice correction
notification (task-packets/E6-T03.yaml) — "the receiving-side local impact
computation is order-independent and idempotent over arbitrary local edge
graphs (re-verifies compute_impact's existing guarantees hold at this new
call site, seeded from notification data rather than a local correction's
own affected_objects)".

Drives ``CorrectionImpactService.receive_correction_notification`` (the
REAL public method, not a private helper) end to end: ONE already-signed,
in-window CorrectionNotification/NodeMessageEnvelope pair is built ONCE
(fixed ``notified_object_ids`` drawn from a small, deliberately overlapping
node-id pool), and hypothesis varies only the LOCAL edge graph fed to a
fresh in-memory EdgeRepository per example — mirroring tests/property/
test_correction_impact_properties.py's own node-id-pool/edge-strategy
convention, applied at this new service-layer call site. Both replay
predicates (envelope-level and notification-level) are fixed to "never
seen" so a call never fails as a false replay across examples/repeated
calls within one example.

task-packets/E9-T00.yaml amendment (2): ``_service_for`` now also wires the
OPTIONAL ``record_event`` dependency (task-packets/E6-T03.yaml), via the
same ``_FakeEventLog``/``append_for_test`` fake every other fixture in this
batch already uses — required since item 1 made
``receive_correction_notification``'s own ``correction.notification_received``
event mandatory on every successful call (fail-closed via
``_require_record_event`` otherwise). This was the exact caller the
packet's own stop_condition named: a property test constructing
``CorrectionImpactService`` without ``record_event``. The fake is a real,
observable one (backed by the same ``_FakeEventLog.read_all()`` every other
assertion in this file could use), not a swallow-everything stub —
``_receive_over`` and the idempotency test both assert the new event's
presence.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from hypothesis import given
from hypothesis import strategies as st
from mrr.contracts.correction_notification import CorrectionNotification
from mrr.contracts.node_message_envelope import NodeMessageEnvelope
from mrr.contracts.practice import Practice
from mrr.crypto.keys import derive_key_id, encode_public_key, generate_ed25519_keypair
from mrr.domain.exceptions import ObjectNotFoundError, RevisionConflictError
from mrr.domain.hashing_policy import sign_object
from mrr.domain.identity import new_urn
from mrr.domain.manifest_trust import practice_key_ring
from mrr.domain.repositories import EDGE_VOCABULARY, StoredObject, TypedEdge
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.claim.service import ClaimService
from mrr.services.correction.service import CorrectionImpactService

_NOW = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)

#: A small, deliberately overlapping pool of node ids so that
#: hypothesis-generated edges collide into cycles, self-loops, and shared
#: dependents often enough to actually exercise the traversal's cycle-safety
#: and dedup behavior — mirrors test_correction_impact_properties.py's own
#: ``_NODE_IDS`` pool.
_NODE_IDS = tuple(f"urn:mrr:claim:{i:026d}" for i in range(6))
_ALL_EDGE_TYPES = sorted(EDGE_VOCABULARY)

#: notified_object_ids seeds a subset of the pool — fixed across every
#: hypothesis example so the ONE pre-built, pre-signed notification/envelope
#: below never has to be re-signed per example.
_SEED_IDS = frozenset(_NODE_IDS[:2])


def _make_edge(source_id: str, edge_type: str, target_id: str, suffix: int) -> TypedEdge:
    return TypedEdge(
        id=f"urn:mrr:edge:{suffix:026d}",
        source_id=source_id,
        target_id=target_id,
        edge_type=edge_type,
        created_at=_NOW,
        created_by="urn:mrr:agent-role:00000000000000000000000000",
        scope=None,
        status="active",
        practice_id="urn:mrr:practice:00000000000000000000000000",
    )


_edge_strategy = st.builds(
    _make_edge,
    source_id=st.sampled_from(_NODE_IDS),
    edge_type=st.sampled_from(_ALL_EDGE_TYPES),
    target_id=st.sampled_from(_NODE_IDS),
    suffix=st.integers(min_value=0, max_value=10_000),
)
_edges_list_strategy = st.lists(_edge_strategy, max_size=25)


# ---------------------------------------------------------------------------
# In-memory fakes — identical in spirit to
# tests/unit/services/correction/test_service.py's own fakes.
# ---------------------------------------------------------------------------


class _FakeObjectRepository:
    def __init__(self) -> None:
        self._revisions: dict[str, list[StoredObject]] = {}

    def insert_revision(
        self, obj: StoredObject, expected_current_revision: int | None
    ) -> StoredObject:
        current = self._revisions.get(obj.id, [])
        current_max = current[-1].revision if current else None
        if current_max != expected_current_revision:
            raise RevisionConflictError(obj.id, expected_current_revision, current_max)
        self._revisions.setdefault(obj.id, []).append(obj)
        return obj

    def get_latest(self, id: str) -> StoredObject:
        revisions = self._revisions.get(id)
        if not revisions:
            raise ObjectNotFoundError(id)
        return revisions[-1]

    def get_revision(self, id: str, revision: int) -> StoredObject:
        for rev in self._revisions.get(id, []):
            if rev.revision == revision:
                return rev
        raise ObjectNotFoundError(id, revision)

    def list_revisions(self, id: str) -> list[StoredObject]:
        return list(self._revisions.get(id, []))


class _FakeEdgeRepository:
    def __init__(self, edges: list[TypedEdge]) -> None:
        self._edges = list(edges)

    def add_edge(self, edge: TypedEdge) -> TypedEdge:
        self._edges.append(edge)
        return edge

    def edges_from(self, id: str, edge_type: str | None = None) -> list[TypedEdge]:
        return [
            e
            for e in self._edges
            if e.source_id == id and (edge_type is None or e.edge_type == edge_type)
        ]

    def edges_to(self, id: str, edge_type: str | None = None) -> list[TypedEdge]:
        return [
            e
            for e in self._edges
            if e.target_id == id and (edge_type is None or e.edge_type == edge_type)
        ]


class _FakeEventLog:
    def __init__(self) -> None:
        self.appended: list[AppendedEvent] = []

    def append_for_test(self, event: DomainEvent) -> AppendedEvent:
        appended = AppendedEvent(
            event=event,
            sequence=len(self.appended) + 1,
            content_hash=f"sha256:{'c' * 64}",
            prev_hash=self.appended[-1].content_hash if self.appended else None,
        )
        self.appended.append(appended)
        return appended

    def read_all(self) -> list[AppendedEvent]:
        return list(self.appended)


def _fake_record(object_repository: _FakeObjectRepository, event_log: _FakeEventLog) -> Any:
    def _record(
        obj: StoredObject,
        expected_current_revision: int | None,
        event: DomainEvent,
    ) -> tuple[StoredObject, AppendedEvent]:
        stored = object_repository.insert_revision(obj, expected_current_revision)
        appended = event_log.append_for_test(event)
        return stored, appended

    return _record


def _fake_record_edge(edge_repository: _FakeEdgeRepository, event_log: _FakeEventLog) -> Any:
    def _record_edge(edge: TypedEdge, event: DomainEvent) -> tuple[TypedEdge, AppendedEvent]:
        stored_edge = edge_repository.add_edge(edge)
        appended = event_log.append_for_test(event)
        return stored_edge, appended

    return _record_edge


def _fake_record_event(event_log: _FakeEventLog) -> Any:
    """The ``RecordEventOnly`` shape (ADR-0007's event-only
    ``mrr.persistence.unit_of_work.record_event`` path) — a real, observable
    fake backed by the SAME ``_FakeEventLog`` every other fixture in this
    module already appends to, not a swallow-everything stub. Wired as
    ``CorrectionImpactService``'s OPTIONAL ``record_event`` dependency
    (task-packets/E9-T00.yaml amendment (2)) so
    ``receive_correction_notification``'s own mandatory ``correction.
    notification_received`` event (item 1) can actually be appended.
    """

    def _record_event(event: DomainEvent) -> AppendedEvent:
        return event_log.append_for_test(event)

    return _record_event


def _service_for(edges: list[TypedEdge]) -> tuple[CorrectionImpactService, _FakeEventLog]:
    object_repository = _FakeObjectRepository()
    event_log = _FakeEventLog()
    edge_repository = _FakeEdgeRepository(edges)
    claim_service = ClaimService(
        object_repository,
        event_log,
        edge_repository,
        _fake_record(object_repository, event_log),
        _fake_record_edge(edge_repository, event_log),
    )
    service = CorrectionImpactService(
        object_repository,
        edge_repository,
        claim_service,
        event_log,
        _fake_record(object_repository, event_log),
        _fake_record_event(event_log),
    )
    return service, event_log


# ---------------------------------------------------------------------------
# ONE already-signed, in-window CorrectionNotification/NodeMessageEnvelope
# pair, built once at module scope — reused unmodified across every
# hypothesis example (only the local edge graph varies).
# ---------------------------------------------------------------------------

_PRIVATE_KEY, _PUBLIC_KEY = generate_ed25519_keypair()
_NOTIFYING_PRACTICE_ID = new_urn("practice")
_THIS_NODE_ID = new_urn("node")
_KID = derive_key_id(_PUBLIC_KEY)
_RING = practice_key_ring(
    Practice.model_validate(
        {
            "id": _NOTIFYING_PRACTICE_ID,
            "api_version": "mrr/v1alpha1",
            "kind": "Practice",
            "practice_id": _NOTIFYING_PRACTICE_ID,
            "revision": 1,
            "created_at": _NOW,
            "created_by": new_urn("agent-role"),
            "content_hash": "sha256:" + "a" * 64,
            "name": "Fixture Notifying Practice",
            "description": "Fixture practice for the E6-T03 property test.",
            "keys": [
                {
                    "kid": _KID,
                    "algorithm": "Ed25519",
                    "encoded_public_key": encode_public_key(_PUBLIC_KEY),
                    "valid_from": _NOW - timedelta(days=1),
                    "valid_until": _NOW + timedelta(days=365),
                    "state": "active",
                }
            ],
            "governance_contacts": ["mailto:governance@fixture.invalid"],
            "supported_policy_versions": ["policy-2026-07-01"],
            "disclosure": {"max_disclosure": "PUBLIC", "trust_statement": "fixture"},
        }
    )
)


def _sign_notification(draft: CorrectionNotification) -> CorrectionNotification:
    signature_value = sign_object(
        _PRIVATE_KEY, json.loads(draft.model_dump_json(exclude_none=True))
    )
    return draft.model_copy(
        update={"signature": draft.signature.model_copy(update={"value": signature_value})}
    )


def _sign_envelope(draft: NodeMessageEnvelope) -> NodeMessageEnvelope:
    signature_value = sign_object(
        _PRIVATE_KEY, json.loads(draft.model_dump_json(exclude_none=True))
    )
    return draft.model_copy(
        update={"signature": draft.signature.model_copy(update={"value": signature_value})}
    )


_notification_draft = CorrectionNotification.model_validate(
    {
        "notification_id": new_urn("correction-notification"),
        "correction_id": new_urn("correction"),
        "correction_revision": 1,
        "notifying_practice_id": _NOTIFYING_PRACTICE_ID,
        "recipient_practice_id": new_urn("practice"),
        "notified_object_ids": sorted(_SEED_IDS),
        "correction_type": "numeric_error",
        "severity": "material",
        "reason": "Fixture reason for the E6-T03 property test.",
        "requested_action": "Mark dependent claims review_required and recompute.",
        "replacement_object_id": None,
        "content_hash": "sha256:" + "3" * 64,
        "nonce": "n" * 16,
        "sent_at": _NOW,
        "expires_at": _NOW + timedelta(minutes=5),
        "signature": {
            "signer_practice_id": _NOTIFYING_PRACTICE_ID,
            "key_id": _KID,
            "algorithm": "Ed25519",
            "signed_at": _NOW,
            "value": "0" * 44,
        },
    }
)
_NOTIFICATION = _sign_notification(_notification_draft)

_envelope_draft = NodeMessageEnvelope.model_validate(
    {
        "message_id": new_urn("node-message-envelope"),
        "sender_node_id": new_urn("node"),
        "sender_practice_id": _NOTIFYING_PRACTICE_ID,
        "recipient_node_id": _THIS_NODE_ID,
        "sent_at": _NOW,
        "expires_at": _NOW + timedelta(minutes=5),
        "payload_kind": "CorrectionNotification",
        "payload_content_hash": _NOTIFICATION.content_hash,
        "payload": json.loads(_NOTIFICATION.model_dump_json(exclude_none=True)),
        "signature": {
            "signer_practice_id": _NOTIFYING_PRACTICE_ID,
            "key_id": _KID,
            "algorithm": "Ed25519",
            "signed_at": _NOW,
            "value": "0" * 44,
        },
    }
)
_ENVELOPE = _sign_envelope(_envelope_draft)


def _never_processed(_: str) -> bool:
    return False


def _receive_over(edges: list[TypedEdge]) -> frozenset[str]:
    service, event_log = _service_for(edges)
    impact = service.receive_correction_notification(
        _ENVELOPE,
        this_node_id=_THIS_NODE_ID,
        trusted_notifying_practice_id=_NOTIFYING_PRACTICE_ID,
        ring=_RING,
        already_processed_envelope=_never_processed,
        already_processed_notification=_never_processed,
        actor=new_urn("agent-role"),
        policy_version="policy-2026-07-01",
        correlation_id=new_urn("correction-run"),
        at=_NOW,
    )

    # task-packets/E9-T00.yaml item 1: every successful call appends exactly
    # one correction.notification_received event, keyed on the notification's
    # own id, whose payload reports the SAME computed impact set this
    # function returns — a fresh service/event_log per call, so exactly one.
    received_events = [
        appended.event
        for appended in event_log.read_all()
        if appended.event.event_type == "correction.notification_received"
    ]
    assert len(received_events) == 1
    assert received_events[0].object_id == _NOTIFICATION.notification_id
    assert received_events[0].object_revision == 1
    assert received_events[0].payload["locally_impacted_object_ids"] == sorted(
        impact.locally_impacted_object_ids
    )

    return impact.locally_impacted_object_ids


@given(edges=_edges_list_strategy)
def test_local_impact_is_idempotent_over_repeated_calls(edges: list[TypedEdge]) -> None:
    service, event_log = _service_for(edges)
    first = service.receive_correction_notification(
        _ENVELOPE,
        this_node_id=_THIS_NODE_ID,
        trusted_notifying_practice_id=_NOTIFYING_PRACTICE_ID,
        ring=_RING,
        already_processed_envelope=_never_processed,
        already_processed_notification=_never_processed,
        actor=new_urn("agent-role"),
        policy_version="policy-2026-07-01",
        correlation_id=new_urn("correction-run"),
        at=_NOW,
    )
    second = service.receive_correction_notification(
        _ENVELOPE,
        this_node_id=_THIS_NODE_ID,
        trusted_notifying_practice_id=_NOTIFYING_PRACTICE_ID,
        ring=_RING,
        already_processed_envelope=_never_processed,
        already_processed_notification=_never_processed,
        actor=new_urn("agent-role"),
        policy_version="policy-2026-07-01",
        correlation_id=new_urn("correction-run"),
        at=_NOW,
    )
    assert first.locally_impacted_object_ids == second.locally_impacted_object_ids

    # Both calls succeed (the replay predicates are fixed to "never seen"),
    # so both independently append their own correction.notification_received
    # event — this service/helper builds no durable replay store of its own
    # (E6-T03's own invariant, unchanged); replay protection is entirely the
    # CALLER's concern. Two events, same notification id, same impact set.
    received_events = [
        appended.event
        for appended in event_log.read_all()
        if appended.event.event_type == "correction.notification_received"
    ]
    assert len(received_events) == 2
    assert received_events[0].id != received_events[1].id  # two DISTINCT event ids
    for event in received_events:
        assert event.object_id == _NOTIFICATION.notification_id
        assert event.object_revision == 1
        assert event.payload["locally_impacted_object_ids"] == sorted(
            first.locally_impacted_object_ids
        )


@given(edges=_edges_list_strategy, data=st.data())
def test_local_impact_is_order_independent_over_edge_permutations(
    edges: list[TypedEdge], data: st.DataObject
) -> None:
    permuted = data.draw(st.permutations(edges))

    original = _receive_over(edges)
    from_permuted = _receive_over(permuted)

    assert original == from_permuted


@given(edges=_edges_list_strategy)
def test_local_impact_is_always_a_subset_of_the_graph_nodes(edges: list[TypedEdge]) -> None:
    nodes = set(_SEED_IDS) | {edge.source_id for edge in edges} | {edge.target_id for edge in edges}
    impacted = _receive_over(edges)
    assert impacted <= nodes
