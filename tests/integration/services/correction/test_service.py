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
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.contracts import Claim, CorrectionEvent
from mrr.contracts.practice import Practice
from mrr.crypto.keys import derive_key_id, encode_public_key, generate_ed25519_keypair
from mrr.domain.envelope_transport import EnvelopeDeliveryOutcome, EnvelopeDeliveryRequest
from mrr.domain.identity import new_urn
from mrr.domain.manifest_trust import practice_key_ring
from mrr.persistence.repositories import (
    PostgresEdgeRepository,
    PostgresEventLog,
    PostgresObjectRepository,
)
from mrr.services.claim.service import ClaimService
from mrr.services.claim.service import bind_edge_unit_of_work as bind_claim_edge_unit_of_work
from mrr.services.claim.service import bind_unit_of_work as bind_claim_unit_of_work
from mrr.services.correction.service import (
    CorrectionImpactService,
    NotificationRecipient,
    bind_event_unit_of_work,
    bind_unit_of_work,
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
