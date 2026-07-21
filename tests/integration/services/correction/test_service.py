"""Integration tests for
``mrr.services.correction.service.CorrectionImpactService`` (task-packets/
E3-T06.yaml, extended by task-packets/E6-T03.yaml's cross-practice
correction notification), run against a real PostgreSQL via the
``postgres_engine`` fixture in tests/integration/conftest.py — wired exactly
as production code would: ``PostgresObjectRepository``/
``PostgresEdgeRepository``/``PostgresEventLog`` over the fixture's engine,
with a real ``mrr.services.claim.service.ClaimService`` injected (never
reimplemented). Skips visibly if ``MRR_TEST_DATABASE_URL`` is unset (fails
hard instead if ``CI=true``) — see that module's docstring.

Acceptance-test mapping (task-packets/E3-T06.yaml, integration tier):

- "an affected claim gains a review_required revision while its prior status
  remains in list_revisions (integration, real PostgreSQL)" ->
  ``test_dependent_claim_gains_review_required_revision_prior_status_preserved``.
- "re-run is idempotent (no duplicate revisions)" ->
  ``test_repeated_propagate_impact_adds_no_duplicate_revisions``.

Acceptance-test mapping (task-packets/E6-T03.yaml, integration tier):

- "a full outbound record -> propagate_impact -> notify_affected_practices
  sequence persists atomically with its events; a separate practice's
  database receiving the resulting notification via
  receive_correction_notification produces claim.status_changed events with
  full provenance for the correct local claims only" ->
  ``test_outbound_then_inbound_correction_notification_flow``. Both
  "practices" share the SAME physical schema (one ``postgres_engine`` per
  test, matching every other integration test in this repository's own
  one-schema-per-test convention) but are logically isolated: the receiving
  side never reads, stores, or references the sender's own
  ``CorrectionEvent`` object at all — only the signed
  ``CorrectionNotification``/``NodeMessageEnvelope`` pair the transport
  fake actually carried crosses the simulated practice boundary.

Acceptance-test mapping (task-packets/E6-T04.yaml, integration tier):

- "an adapt record_response call persists the CorrectionResponse revision,
  every corrects edge, and the correction.response_recorded event
  atomically; ... querying the generic objects/edges tables directly
  confirms no migration was needed" ->
  ``test_record_response_adapt_persists_response_edges_and_event_atomically``.
- "a fault injected after the edges but before the event commit ... leaves
  none of the three rows behind" ->
  ``test_record_response_fault_injected_after_edges_leaves_nothing_persisted``.
- duplicate response against a real Postgres-backed event log ->
  ``test_record_response_duplicate_raises_and_persists_nothing_against_real_postgres``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.contracts import Claim, CorrectionEvent
from mrr.contracts.correction_response import CorrectionResponse, CorrectionResponseAdaptation
from mrr.contracts.practice import Practice
from mrr.crypto.keys import derive_key_id, encode_public_key, generate_ed25519_keypair
from mrr.domain.envelope_transport import EnvelopeDeliveryOutcome, EnvelopeDeliveryRequest
from mrr.domain.exceptions import (
    CorrectionResponseAlreadyRecordedError,
    InvalidTransitionError,
    ObjectNotFoundError,
    PendingDeliveryNotFoundError,
)
from mrr.domain.hashing_policy import compute_content_hash
from mrr.domain.identity import new_urn
from mrr.domain.manifest_trust import practice_key_ring
from mrr.domain.offline_bundle import validate_inbound_bundle
from mrr.domain.repositories import StoredObject, TypedEdge
from mrr.persistence.repositories import (
    PostgresDeliveryPendingStore,
    PostgresEdgeRepository,
    PostgresEventLog,
    PostgresObjectRepository,
)
from mrr.persistence.tables import domain_events_table, edges_table, objects_table
from mrr.provenance.events import DomainEvent
from mrr.services.claim.service import ClaimService
from mrr.services.claim.service import bind_edge_unit_of_work as bind_claim_edge_unit_of_work
from mrr.services.claim.service import bind_unit_of_work as bind_claim_unit_of_work
from mrr.services.correction.service import (
    CorrectionImpactService,
    NotificationRecipient,
    bind_event_unit_of_work,
    bind_revision_with_edges_unit_of_work,
    bind_unit_of_work,
    record_response_revision_with_edges_and_event,
)
from sqlalchemy import Engine

_POLICY_VERSION = "policy-2026-07-01"


def _claim(*, id: str | None = None, **overrides: Any) -> Claim:
    data: dict[str, Any] = {
        "id": id or new_urn("claim"),
        "api_version": "mrr/v1alpha1",
        "kind": "Claim",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": datetime.now(UTC),
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "assertion": "Does this fixture assertion satisfy the schema's minimum length rule?",
        "claim_type": "statistical",
        "scope": {},
        "status": "draft",
        "evidence_relations": [],
        "counterevidence_relations": [],
        "dependencies": [],
        "source_family_ids": [],
        "uncertainty": [],
        "known_unknowns": [],
        "proposer_id": new_urn("agent-role"),
        "verification_ids": [],
        "correction_ids": [],
    }
    data.update(overrides)
    return Claim.model_validate(data)


def _correction(*, affected_object_ids: list[str], **overrides: Any) -> CorrectionEvent:
    data: dict[str, Any] = {
        "id": new_urn("correction"),
        "api_version": "mrr/v1alpha1",
        "kind": "CorrectionEvent",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": datetime.now(UTC),
        "created_by": new_urn("person"),
        "content_hash": "sha256:" + "b" * 64,
        "affected_objects": [
            {"id": object_id, "content_hash": "sha256:" + "e" * 64}
            for object_id in affected_object_ids
        ],
        "correction_type": "numeric_error",
        "severity": "material",
        "reason": "Fixture reason: the denominator was later shown to be wrong.",
        "evidence_refs": [new_urn("evidence-anchor")],
        "originator_id": new_urn("person"),
        "requested_action": "Mark dependent claims review_required and recompute.",
        "replacement_object_id": None,
        "status": "OPEN",
        "impact_objects": [],
    }
    data.update(overrides)
    return CorrectionEvent.model_validate(data)


def _services_for(
    engine: Engine,
) -> tuple[
    CorrectionImpactService,
    ClaimService,
    PostgresObjectRepository,
    PostgresEdgeRepository,
    PostgresEventLog,
]:
    object_repository = PostgresObjectRepository(engine)
    edge_repository = PostgresEdgeRepository(engine)
    event_log = PostgresEventLog(engine)
    claim_service = ClaimService(
        object_repository,
        event_log,
        edge_repository,
        bind_claim_unit_of_work(engine, object_repository, event_log),
        bind_claim_edge_unit_of_work(engine, event_log),
    )
    correction_service = CorrectionImpactService(
        object_repository,
        edge_repository,
        claim_service,
        event_log,
        bind_unit_of_work(engine, object_repository, event_log),
    )
    return correction_service, claim_service, object_repository, edge_repository, event_log


def _services_for_notification(
    engine: Engine,
) -> tuple[
    CorrectionImpactService,
    ClaimService,
    PostgresObjectRepository,
    PostgresEdgeRepository,
    PostgresEventLog,
]:
    """Identical to :func:`_services_for` but additionally wires the
    OPTIONAL ``record_event`` dependency (task-packets/E6-T03.yaml) via
    ``bind_event_unit_of_work`` — the real ADR-0007 event-only Postgres
    path, needed by ``notify_affected_practices`` whenever more than one
    recipient's event must be appended per call.
    """
    object_repository = PostgresObjectRepository(engine)
    edge_repository = PostgresEdgeRepository(engine)
    event_log = PostgresEventLog(engine)
    claim_service = ClaimService(
        object_repository,
        event_log,
        edge_repository,
        bind_claim_unit_of_work(engine, object_repository, event_log),
        bind_claim_edge_unit_of_work(engine, event_log),
    )
    correction_service = CorrectionImpactService(
        object_repository,
        edge_repository,
        claim_service,
        event_log,
        bind_unit_of_work(engine, object_repository, event_log),
        bind_event_unit_of_work(engine, event_log),
    )
    return correction_service, claim_service, object_repository, edge_repository, event_log


def _services_for_delivery_tracking(
    engine: Engine, *, max_attempts: int = 3
) -> tuple[
    CorrectionImpactService,
    ClaimService,
    PostgresObjectRepository,
    PostgresEdgeRepository,
    PostgresEventLog,
    PostgresDeliveryPendingStore,
]:
    """Identical to :func:`_services_for_notification` but additionally
    wires the OPTIONAL ``delivery_pending_store`` dependency (task-packets/
    E6-T06.yaml) via a real, Postgres-backed ``PostgresDeliveryPendingStore``
    — needed by ``open_pending_delivery``/``retry_pending_delivery_online``/
    ``retry_pending_delivery_offline``/``mark_pending_delivery_delivered``/
    ``mark_pending_delivery_exhausted``.
    """
    object_repository = PostgresObjectRepository(engine)
    edge_repository = PostgresEdgeRepository(engine)
    event_log = PostgresEventLog(engine)
    claim_service = ClaimService(
        object_repository,
        event_log,
        edge_repository,
        bind_claim_unit_of_work(engine, object_repository, event_log),
        bind_claim_edge_unit_of_work(engine, event_log),
    )
    delivery_pending_store = PostgresDeliveryPendingStore(
        engine, max_attempts=max_attempts, backoff=lambda n: timedelta(minutes=n)
    )
    correction_service = CorrectionImpactService(
        object_repository,
        edge_repository,
        claim_service,
        event_log,
        bind_unit_of_work(engine, object_repository, event_log),
        bind_event_unit_of_work(engine, event_log),
        delivery_pending_store=delivery_pending_store,
    )
    return (
        correction_service,
        claim_service,
        object_repository,
        edge_repository,
        event_log,
        delivery_pending_store,
    )


def _services_for_response(
    engine: Engine,
) -> tuple[
    CorrectionImpactService,
    ClaimService,
    PostgresObjectRepository,
    PostgresEdgeRepository,
    PostgresEventLog,
]:
    """Identical to :func:`_services_for` but additionally wires the
    OPTIONAL ``record_revision_with_edges`` dependency (task-packets/
    E6-T04.yaml) via ``bind_revision_with_edges_unit_of_work`` — the real
    Postgres object-revision+edges+event path, needed by ``record_response``.
    """
    object_repository = PostgresObjectRepository(engine)
    edge_repository = PostgresEdgeRepository(engine)
    event_log = PostgresEventLog(engine)
    claim_service = ClaimService(
        object_repository,
        event_log,
        edge_repository,
        bind_claim_unit_of_work(engine, object_repository, event_log),
        bind_claim_edge_unit_of_work(engine, event_log),
    )
    correction_service = CorrectionImpactService(
        object_repository,
        edge_repository,
        claim_service,
        event_log,
        bind_unit_of_work(engine, object_repository, event_log),
        record_revision_with_edges=bind_revision_with_edges_unit_of_work(
            engine, object_repository, event_log
        ),
    )
    return correction_service, claim_service, object_repository, edge_repository, event_log


def test_dependent_claim_gains_review_required_revision_prior_status_preserved(
    postgres_engine: Engine,
) -> None:
    service, claim_service, object_repository, edge_repository, _ = _services_for(postgres_engine)
    actor = new_urn("agent-role")
    correlation_id = new_urn("correction-run")

    root = _claim(status="draft")
    dependent = _claim(status="draft")
    claim_service.create(
        root, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    claim_service.create(
        dependent, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    claim_service.submit_for_review(
        dependent.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    claim_service.add_dependency_edge(
        dependent.id,
        root.id,
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    correction = _correction(affected_object_ids=[root.id])
    service.record(
        correction, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    stored_correction = service.propagate_impact(
        correction.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    assert stored_correction.body["impact_objects"] == [dependent.id]

    dependent_revisions = object_repository.list_revisions(dependent.id)
    # under_review (from submit_for_review) is still present in history,
    # never overwritten — a new review_required revision is appended on top.
    assert [rev.body["status"] for rev in dependent_revisions] == [
        "draft",
        "under_review",
        "review_required",
    ]
    assert object_repository.get_latest(dependent.id).body["status"] == "review_required"

    # The typed dependency edge is queryable both directions.
    assert [e.target_id for e in edge_repository.edges_from(dependent.id, "depends_on")] == [
        root.id
    ]


def test_repeated_propagate_impact_adds_no_duplicate_revisions(postgres_engine: Engine) -> None:
    service, claim_service, object_repository, edge_repository, event_log = _services_for(
        postgres_engine
    )
    actor = new_urn("agent-role")
    correlation_id = new_urn("correction-run")

    root = _claim(status="draft")
    dependent = _claim(status="draft")
    claim_service.create(
        root, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    claim_service.create(
        dependent, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    claim_service.add_dependency_edge(
        dependent.id,
        root.id,
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    correction = _correction(affected_object_ids=[root.id])
    service.record(
        correction, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    first = service.propagate_impact(
        correction.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    dependent_revisions_after_first = object_repository.list_revisions(dependent.id)
    correction_revisions_after_first = object_repository.list_revisions(correction.id)
    events_after_first = [
        appended for appended in event_log.read_all() if appended.event.object_id == correction.id
    ]

    second = service.propagate_impact(
        correction.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    assert first.body["impact_objects"] == second.body["impact_objects"] == [dependent.id]
    assert first.revision == second.revision
    assert object_repository.list_revisions(dependent.id) == dependent_revisions_after_first
    assert object_repository.list_revisions(correction.id) == correction_revisions_after_first
    assert [
        appended for appended in event_log.read_all() if appended.event.object_id == correction.id
    ] == events_after_first


# ---------------------------------------------------------------------------
# E6-T03: cross-practice correction notification, full outbound+inbound
# flow against real PostgreSQL.
# ---------------------------------------------------------------------------

_NOTIFICATION_SENT_AT = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)
_NOTIFICATION_EXPIRES_AT = _NOTIFICATION_SENT_AT + timedelta(minutes=5)


class _FakeEnvelopeTransport:
    """Captures the envelope for one recipient endpoint and reports it
    ``"delivered"`` — this test drives the receiving side directly from the
    captured envelope (no real network), mirroring
    ``mrr.domain.envelope_transport``'s own "tests use only an in-test
    fake" precedent.
    """

    def __init__(self) -> None:
        self.sent_requests: list[EnvelopeDeliveryRequest] = []

    def send(self, request: EnvelopeDeliveryRequest) -> EnvelopeDeliveryOutcome:
        self.sent_requests.append(request)
        return EnvelopeDeliveryOutcome(status="delivered", message_id=request.envelope.message_id)


class _ConfigurableEnvelopeTransport:
    """Like ``_FakeEnvelopeTransport`` above, but reports a caller-fixed
    ``status`` per instance instead of always ``"delivered"`` — needed by
    the E6-T06 tests below to first drive a recipient to ``DELIVERY_PENDING``
    (a "failed" first attempt) and then, separately, retry it. A distinct
    class rather than monkeypatching ``_FakeEnvelopeTransport.send``, so
    ``sent_requests`` tracking (used by several tests above) is never
    bypassed.
    """

    def __init__(self, status: str) -> None:
        self._status = status
        self.sent_requests: list[EnvelopeDeliveryRequest] = []

    def send(self, request: EnvelopeDeliveryRequest) -> EnvelopeDeliveryOutcome:
        self.sent_requests.append(request)
        return EnvelopeDeliveryOutcome(status=self._status, message_id=request.envelope.message_id)  # type: ignore[arg-type]


def _notifying_practice_fixture() -> tuple[Practice, Ed25519PrivateKey, str]:
    private_key, public_key = generate_ed25519_keypair()
    practice_id = new_urn("practice")
    kid = derive_key_id(public_key)
    practice = Practice.model_validate(
        {
            "id": practice_id,
            "api_version": "mrr/v1alpha1",
            "kind": "Practice",
            "practice_id": practice_id,
            "revision": 1,
            "created_at": _NOTIFICATION_SENT_AT,
            "created_by": new_urn("agent-role"),
            "content_hash": "sha256:" + "a" * 64,
            "name": "Fixture Notifying Practice",
            "description": "Fixture practice for the E6-T03 integration test.",
            "keys": [
                {
                    "kid": kid,
                    "algorithm": "Ed25519",
                    "encoded_public_key": encode_public_key(public_key),
                    "valid_from": _NOTIFICATION_SENT_AT - timedelta(days=1),
                    "valid_until": _NOTIFICATION_SENT_AT + timedelta(days=365),
                    "state": "active",
                }
            ],
            "governance_contacts": ["mailto:governance@fixture.invalid"],
            "supported_policy_versions": ["policy-2026-07-01"],
            "disclosure": {"max_disclosure": "PUBLIC", "trust_statement": "fixture"},
        }
    )
    return practice, private_key, kid


def _never_processed(_: str) -> bool:
    return False


def test_outbound_then_inbound_correction_notification_flow(postgres_engine: Engine) -> None:
    """record -> propagate_impact -> notify_affected_practices (the sending
    practice, real Postgres) produces a real, signed
    CorrectionNotification/NodeMessageEnvelope pair; a SEPARATE
    CorrectionImpactService/ClaimService pair (the receiving practice,
    sharing the same physical schema — see this module's own docstring)
    then processes it via receive_correction_notification, producing
    claim.status_changed-shaped events with full provenance for the correct
    LOCAL claim only. The receiving side never touches the sender's own
    CorrectionEvent object at all.
    """
    actor = new_urn("agent-role")
    correlation_id = new_urn("e6-t03-run")

    # --- Sending practice: record + propagate_impact + notify. -----------
    sender_service, _, sender_objects, _, sender_events = _services_for_notification(
        postgres_engine
    )
    notifying_practice, signing_key, key_id = _notifying_practice_fixture()

    corrected_object_id = new_urn("claim")
    correction = _correction(affected_object_ids=[corrected_object_id])
    sender_service.record(
        correction, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    sender_service.propagate_impact(
        correction.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    recipient_practice_id = new_urn("practice")
    recipient_node_id = new_urn("node")
    transport = _FakeEnvelopeTransport()

    stored_correction = sender_service.notify_affected_practices(
        correction.id,
        recipients=[
            NotificationRecipient(
                recipient_practice_id=recipient_practice_id,
                recipient_node_id=recipient_node_id,
                recipient_endpoint="endpoint-a",
                notified_object_ids=[corrected_object_id],
            )
        ],
        transport=transport,
        sender_node_id=new_urn("node"),
        notifying_practice_id=notifying_practice.id,
        signing_key=signing_key,
        signing_key_id=key_id,
        sent_at=_NOTIFICATION_SENT_AT,
        expires_at=_NOTIFICATION_EXPIRES_AT,
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    assert stored_correction.body["status"] == "AWAITING_RESPONSES"
    sent_events = [
        appended.event
        for appended in sender_events.read_all()
        if appended.event.object_id == correction.id
        and appended.event.event_type == "correction.notification_sent"
    ]
    assert len(sent_events) == 1
    assert sent_events[0].payload["delivery_status"] == "sent"
    assert len(transport.sent_requests) == 1
    envelope = transport.sent_requests[0].envelope
    correction_revisions_before_receive = sender_objects.list_revisions(correction.id)

    # --- Receiving practice: a SEPARATE service pair over the SAME schema.
    # The physical objects/edges/events tables are shared (one Postgres
    # schema per test, matching every other integration test here); what
    # this test actually verifies is that receive_correction_notification's
    # OWN code path never writes anything referencing the sender's
    # CorrectionEvent id (checked below via its unchanged revision list). --
    receiver_service, receiver_claims, receiver_objects, receiver_edges, receiver_events = (
        _services_for(postgres_engine)
    )

    local_dependent = _claim(status="draft")
    local_unrelated = _claim(status="draft")
    receiver_claims.create(
        local_dependent, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    receiver_claims.create(
        local_unrelated, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    receiver_claims.submit_for_review(
        local_dependent.id,
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )
    receiver_claims.submit_for_review(
        local_unrelated.id,
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )
    receiver_claims.add_dependency_edge(
        local_dependent.id,
        corrected_object_id,
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    ring = practice_key_ring(notifying_practice)
    impact = receiver_service.receive_correction_notification(
        envelope,
        this_node_id=recipient_node_id,
        trusted_notifying_practice_id=notifying_practice.id,
        ring=ring,
        already_processed_envelope=_never_processed,
        already_processed_notification=_never_processed,
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
        at=_NOTIFICATION_SENT_AT,
    )

    assert impact.locally_impacted_object_ids == frozenset({local_dependent.id})
    assert receiver_objects.get_latest(local_dependent.id).body["status"] == "review_required"
    assert receiver_objects.get_latest(local_unrelated.id).body["status"] == "under_review"

    claim_events = [
        appended.event
        for appended in receiver_events.read_all()
        if appended.event.object_id == local_dependent.id
    ]
    assert any(event.event_type == "claim.review_required" for event in claim_events)
    for event in claim_events:
        assert event.actor == actor
        assert event.policy_version == _POLICY_VERSION
        assert event.occurred_at.tzinfo is not None

    # receive_correction_notification never created, stored, or mutated any
    # copy of the remote CorrectionEvent — its own revision history is
    # exactly what the SENDING side alone produced, untouched by the
    # receiving call.
    assert sender_objects.list_revisions(correction.id) == correction_revisions_before_receive


# ---------------------------------------------------------------------------
# record_response() (task-packets/E6-T04.yaml), against real PostgreSQL.
# ---------------------------------------------------------------------------


def _never_responded(_correction_notification_id: str) -> bool:
    return False


def test_record_response_adapt_persists_response_edges_and_event_atomically(
    postgres_engine: Engine,
) -> None:
    """An adapt ``record_response`` call persists the CorrectionResponse
    revision, every ``corrects`` edge, and the ``correction.response_
    recorded`` event atomically; direct raw-SQL queries against the generic
    ``objects``/``edges`` tables confirm no migration was needed
    (task-packets/E6-T04.yaml forbidden_changes: `corrects` is already a
    declared EDGE_VOCABULARY member).
    """
    service, claim_service, object_repository, edge_repository, event_log = _services_for_response(
        postgres_engine
    )
    actor = new_urn("agent-role")
    correlation_id = new_urn("correction-run")
    responding_practice_id = new_urn("practice")

    adapted_one = _claim(status="draft")
    adapted_two = _claim(status="draft")
    claim_service.create(
        adapted_one, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    claim_service.create(
        adapted_two, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    notified_one = new_urn("claim")
    notified_two = new_urn("claim")

    stored = service.record_response(
        correction_notification_id=new_urn("correction-notification"),
        notifying_practice_id=new_urn("practice"),
        origin_correction_event_id=new_urn("correction"),
        origin_correction_event_revision=1,
        notified_object_ids=[notified_one, notified_two],
        responding_practice_id=responding_practice_id,
        decision="adapt",
        adaptations=[
            CorrectionResponseAdaptation(
                adapted_object_id=adapted_one.id, notified_object_id=notified_one
            ),
            CorrectionResponseAdaptation(
                adapted_object_id=adapted_two.id, notified_object_id=notified_two
            ),
        ],
        already_responded=_never_responded,
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    assert stored.revision == 1
    assert stored.kind == "CorrectionResponse"
    assert object_repository.get_latest(stored.id).body["decision"] == "adapt"

    edges_one = edge_repository.edges_from(adapted_one.id, "corrects")
    edges_two = edge_repository.edges_from(adapted_two.id, "corrects")
    assert len(edges_one) == 1 and edges_one[0].target_id == notified_one
    assert len(edges_two) == 1 and edges_two[0].target_id == notified_two

    events = [
        appended.event for appended in event_log.read_all() if appended.event.object_id == stored.id
    ]
    assert [e.event_type for e in events] == ["correction.response_recorded"]
    assert events[0].actor == actor
    assert events[0].policy_version == _POLICY_VERSION
    assert events[0].correlation_id == correlation_id

    # Direct raw SQL against the generic objects/edges tables (bypassing
    # every repository) confirms no migration was needed: kind=
    # "CorrectionResponse" and edge_type="corrects" are already accepted by
    # the existing schema/CHECK constraint.
    with postgres_engine.connect() as conn:
        kind_row = conn.execute(
            sa.select(objects_table.c.kind).where(objects_table.c.id == stored.id)
        ).one()
        assert kind_row.kind == "CorrectionResponse"

        edge_type_rows = conn.execute(
            sa.select(edges_table.c.edge_type).where(
                edges_table.c.source_id.in_([adapted_one.id, adapted_two.id])
            )
        ).all()
        assert {row.edge_type for row in edge_type_rows} == {"corrects"}

        event_count = conn.execute(
            sa.select(sa.func.count())
            .select_from(domain_events_table)
            .where(domain_events_table.c.object_id == stored.id)
        ).scalar_one()
        assert event_count == 1


def test_record_response_fault_injected_after_edges_leaves_nothing_persisted(
    postgres_engine: Engine,
) -> None:
    """A fault injected after every `corrects` edge is inserted but before
    the `correction.response_recorded` event is appended (still inside the
    one open transaction) leaves none of the three rows behind — mirroring
    the existing `_after_append` seam
    (tests/integration/persistence/test_event_log_and_outbox.py's own
    `test_injected_failure_after_append_leaves_nothing_persisted`), calling
    :func:`record_response_revision_with_edges_and_event` directly (the same
    way that test calls `record_object_revision_with_event` directly) rather
    than through `record_response` itself.
    """
    object_repository = PostgresObjectRepository(postgres_engine)
    edge_repository = PostgresEdgeRepository(postgres_engine)
    event_log = PostgresEventLog(postgres_engine)
    claim_service = ClaimService(
        object_repository,
        event_log,
        edge_repository,
        bind_claim_unit_of_work(postgres_engine, object_repository, event_log),
        bind_claim_edge_unit_of_work(postgres_engine, event_log),
    )
    actor = new_urn("agent-role")
    correlation_id = new_urn("correction-run")

    adapted = _claim(status="draft")
    claim_service.create(
        adapted, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    notified_object_id = new_urn("claim")
    response_id = new_urn("correction-response")
    now = datetime.now(UTC)

    body: dict[str, Any] = {
        "id": response_id,
        "api_version": "mrr/v1alpha1",
        "kind": "CorrectionResponse",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": now.isoformat(),
        "created_by": actor,
        "content_hash": "sha256:" + "0" * 64,
        "correction_notification_id": new_urn("correction-notification"),
        "notifying_practice_id": new_urn("practice"),
        "origin_correction_event_id": new_urn("correction"),
        "origin_correction_event_revision": 1,
        "notified_object_ids": [notified_object_id],
        "decision": "adapt",
        "adaptations": [
            {"adapted_object_id": adapted.id, "notified_object_id": notified_object_id}
        ],
    }
    body["content_hash"] = compute_content_hash(body)
    CorrectionResponse.model_validate(body)  # sanity: a genuinely valid body

    obj = StoredObject(
        id=response_id,
        api_version="mrr/v1alpha1",
        kind="CorrectionResponse",
        practice_id=body["practice_id"],
        revision=1,
        created_at=now,
        created_by=actor,
        content_hash=body["content_hash"],
        supersedes=None,
        labels=None,
        body=body,
    )
    edge = TypedEdge(
        id=new_urn("edge"),
        source_id=adapted.id,
        target_id=notified_object_id,
        edge_type="corrects",
        created_at=now,
        created_by=actor,
        scope=None,
        status="active",
        practice_id=body["practice_id"],
    )
    event = DomainEvent(
        id=new_urn("domain-event"),
        event_type="correction.response_recorded",
        occurred_at=now,
        actor=actor,
        policy_version=_POLICY_VERSION,
        causation_id=None,
        correlation_id=correlation_id,
        object_id=response_id,
        object_revision=1,
        payload={"decision": "adapt"},
    )

    def _inject_failure() -> None:
        raise RuntimeError("injected failure after edges, before event append")

    with pytest.raises(RuntimeError, match="injected failure"):
        record_response_revision_with_edges_and_event(
            postgres_engine,
            object_repository,
            event_log,
            obj,
            None,
            [edge],
            event,
            _after_edges=_inject_failure,
        )

    # Neither the CorrectionResponse revision...
    with pytest.raises(ObjectNotFoundError):
        object_repository.get_latest(response_id)

    # ...nor the corrects edge...
    assert edge_repository.edges_from(adapted.id, "corrects") == []

    # ...nor the event survive.
    assert [
        appended for appended in event_log.read_all() if appended.event.object_id == response_id
    ] == []


def test_record_response_duplicate_raises_and_persists_nothing_against_real_postgres(
    postgres_engine: Engine,
) -> None:
    """A second `record_response` call for a `correction_notification_id`
    the caller-supplied `already_responded` predicate reports as already-seen
    raises `CorrectionResponseAlreadyRecordedError` before any write, against
    a real Postgres-backed object repository/event log; a call for a
    DIFFERENT `correction_notification_id` is unaffected.
    """
    service, _claim_service, _object_repository, _edge_repository, event_log = (
        _services_for_response(postgres_engine)
    )
    actor = new_urn("agent-role")
    correlation_id = new_urn("correction-run")
    notification_id = new_urn("correction-notification")
    other_notification_id = new_urn("correction-notification")

    def _already_responded(candidate_id: str) -> bool:
        return candidate_id == notification_id

    with pytest.raises(CorrectionResponseAlreadyRecordedError):
        service.record_response(
            correction_notification_id=notification_id,
            notifying_practice_id=new_urn("practice"),
            origin_correction_event_id=new_urn("correction"),
            origin_correction_event_revision=1,
            notified_object_ids=[new_urn("claim")],
            responding_practice_id=new_urn("practice"),
            decision="accept",
            already_responded=_already_responded,
            actor=actor,
            policy_version=_POLICY_VERSION,
            correlation_id=correlation_id,
        )
    assert event_log.read_all() == []

    stored = service.record_response(
        correction_notification_id=other_notification_id,
        notifying_practice_id=new_urn("practice"),
        origin_correction_event_id=new_urn("correction"),
        origin_correction_event_revision=1,
        notified_object_ids=[new_urn("claim")],
        responding_practice_id=new_urn("practice"),
        decision="accept",
        already_responded=_already_responded,
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )
    assert stored.body["correction_notification_id"] == other_notification_id


# ---------------------------------------------------------------------------
# E6-T06: offline recipient delivery tracking, against a real
# PostgresDeliveryPendingStore.
# ---------------------------------------------------------------------------


def test_full_online_delivery_retry_flow_against_real_postgres(postgres_engine: Engine) -> None:
    """record -> propagate_impact -> notify_affected_practices (one
    recipient's synchronous attempt reports "failed", driving
    CORRECTION_LIFECYCLE to DELIVERY_PENDING, unchanged E6-T03 behavior) ->
    open_pending_delivery persists a real row -> a FURTHER
    retry_pending_delivery_online attempt with the SAME already-signed
    envelope, now "delivered", resolves the record — all against a real
    PostgresDeliveryPendingStore/PostgresEventLog, and the CorrectionEvent
    object itself is never touched by any of the delivery-tracking calls.
    """
    actor = new_urn("agent-role")
    correlation_id = new_urn("e6-t06-run")
    service, _, object_repository, _, event_log, store = _services_for_delivery_tracking(
        postgres_engine
    )
    notifying_practice, signing_key, key_id = _notifying_practice_fixture()

    corrected_object_id = new_urn("claim")
    correction = _correction(affected_object_ids=[corrected_object_id])
    service.record(
        correction, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    service.propagate_impact(
        correction.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    recipient_practice_id = new_urn("practice")
    recipient_node_id = new_urn("node")
    failing_transport = _ConfigurableEnvelopeTransport("failed")

    stored_correction = service.notify_affected_practices(
        correction.id,
        recipients=[
            NotificationRecipient(
                recipient_practice_id=recipient_practice_id,
                recipient_node_id=recipient_node_id,
                recipient_endpoint="endpoint-a",
                notified_object_ids=[corrected_object_id],
            )
        ],
        transport=failing_transport,
        sender_node_id=new_urn("node"),
        notifying_practice_id=notifying_practice.id,
        signing_key=signing_key,
        signing_key_id=key_id,
        sent_at=_NOTIFICATION_SENT_AT,
        expires_at=_NOTIFICATION_EXPIRES_AT,
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )
    assert stored_correction.body["status"] == "DELIVERY_PENDING"
    correction_revisions_after_notify = object_repository.list_revisions(correction.id)

    envelope = failing_transport.sent_requests[0].envelope
    notification_id = envelope.payload["notification_id"]

    newly_opened = service.open_pending_delivery(
        correction.id,
        notification_id=notification_id,
        recipient_node_id=recipient_node_id,
        notification_expires_at=envelope.expires_at,
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
        at=_NOTIFICATION_SENT_AT + timedelta(seconds=1),
    )
    assert newly_opened is True
    opened_record = store.get_pending_delivery(recipient_node_id, notification_id)
    assert opened_record is not None
    assert opened_record.status == "pending"
    assert opened_record.attempt_count == 1

    delivering_transport = _FakeEnvelopeTransport()
    record = service.retry_pending_delivery_online(
        correction.id,
        notification_id=notification_id,
        recipient_node_id=recipient_node_id,
        envelope=envelope,
        transport=delivering_transport,
        recipient_endpoint="endpoint-a",
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
        at=_NOTIFICATION_SENT_AT + timedelta(seconds=2),
    )

    assert record.status == "delivered"
    assert record.attempt_count == 2
    assert delivering_transport.sent_requests[0].envelope is envelope

    delivery_events = [
        appended.event
        for appended in event_log.read_all()
        if appended.event.event_type == "correction.notification_sent"
        and appended.event.payload.get("notification_id") == notification_id
    ]
    delivery_statuses = [event.payload["delivery_status"] for event in delivery_events]
    assert "pending" in delivery_statuses  # the open_pending_delivery event
    assert "delivered" in delivery_statuses  # the retry_pending_delivery_online event

    # The CorrectionEvent object itself was never touched by ANY of the
    # delivery-tracking calls — still DELIVERY_PENDING at revision 1 hop
    # from before (task-packets/E6-T06.yaml derived_decisions (f)).
    assert object_repository.list_revisions(correction.id) == correction_revisions_after_notify


def test_full_offline_delivery_retry_composes_and_validates_against_real_postgres(
    postgres_engine: Engine,
) -> None:
    """The same shape as the online flow above, but the retry channel is
    the offline one: the SAME already-signed envelope is wrapped, via the
    UNCHANGED ``build_outbox_bundle``, into a fresh ``OfflineBundle`` that
    passes the UNCHANGED ``validate_inbound_bundle`` — against a real
    Postgres-backed ``PostgresDeliveryPendingStore``. Because the offline
    channel has no acknowledgement mechanism, the record stays "pending"
    until an explicit ``mark_pending_delivery_delivered`` out-of-band signal
    arrives.
    """
    actor = new_urn("agent-role")
    correlation_id = new_urn("e6-t06-run")
    service, _, object_repository, _, event_log, store = _services_for_delivery_tracking(
        postgres_engine
    )
    notifying_practice, signing_key, key_id = _notifying_practice_fixture()

    corrected_object_id = new_urn("claim")
    correction = _correction(affected_object_ids=[corrected_object_id])
    service.record(
        correction, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    service.propagate_impact(
        correction.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    recipient_practice_id = new_urn("practice")
    recipient_node_id = new_urn("node")
    failing_transport = _ConfigurableEnvelopeTransport("failed")

    service.notify_affected_practices(
        correction.id,
        recipients=[
            NotificationRecipient(
                recipient_practice_id=recipient_practice_id,
                recipient_node_id=recipient_node_id,
                recipient_endpoint="endpoint-a",
                notified_object_ids=[corrected_object_id],
            )
        ],
        transport=failing_transport,
        sender_node_id=new_urn("node"),
        notifying_practice_id=notifying_practice.id,
        signing_key=signing_key,
        signing_key_id=key_id,
        sent_at=_NOTIFICATION_SENT_AT,
        expires_at=_NOTIFICATION_EXPIRES_AT,
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )
    envelope = failing_transport.sent_requests[0].envelope
    notification_id = envelope.payload["notification_id"]

    service.open_pending_delivery(
        correction.id,
        notification_id=notification_id,
        recipient_node_id=recipient_node_id,
        notification_expires_at=envelope.expires_at,
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
        at=_NOTIFICATION_SENT_AT + timedelta(seconds=1),
    )

    bundle_created_at = _NOTIFICATION_SENT_AT + timedelta(seconds=2)
    bundle_expires_at = bundle_created_at + timedelta(days=1)
    bundle, record = service.retry_pending_delivery_offline(
        correction.id,
        notification_id=notification_id,
        recipient_node_id=recipient_node_id,
        envelope=envelope,
        bundle_id=new_urn("offline-bundle"),
        bundle_nonce="n" * 16,
        sender_node_id=new_urn("node"),
        sender_practice_id=notifying_practice.id,
        bundle_created_at=bundle_created_at,
        bundle_expires_at=bundle_expires_at,
        signing_key=signing_key,
        signing_key_id=key_id,
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
        at=bundle_created_at,
    )

    assert bundle.envelopes == [envelope]
    ring = practice_key_ring(notifying_practice)
    validated_envelopes = validate_inbound_bundle(
        bundle,
        this_node_id=recipient_node_id,
        trusted_sender_practice_id=notifying_practice.id,
        ring=ring,
        already_processed=lambda _bundle_id: False,
        at=bundle_created_at,
    )
    assert validated_envelopes == [envelope]
    assert record.status == "pending"  # no ack channel; not yet "delivered"

    # An out-of-band delivery signal (e.g. the recipient later confirms via
    # some external channel) resolves it.
    delivered_record = service.mark_pending_delivery_delivered(
        correction.id,
        notification_id=notification_id,
        recipient_node_id=recipient_node_id,
        channel="offline_bundle",
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
        at=bundle_created_at + timedelta(hours=1),
    )
    assert delivered_record.status == "delivered"

    with pytest.raises(PendingDeliveryNotFoundError):
        store.record_retry_attempt(
            new_urn("node"),  # a never-opened recipient
            notification_id,
            outcome="failed",
            at=bundle_created_at,
        )
    with pytest.raises(InvalidTransitionError):
        service.mark_pending_delivery_exhausted(
            correction.id,
            notification_id=notification_id,
            recipient_node_id=recipient_node_id,
            reason="attempted after already delivered",
            channel="offline_bundle",
            actor=actor,
            policy_version=_POLICY_VERSION,
            correlation_id=correlation_id,
            at=bundle_created_at + timedelta(hours=2),
        )
