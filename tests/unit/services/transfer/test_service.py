"""Unit tests for ``mrr.services.transfer.service.TransferService``
(task-packets/E6-T01.yaml), run entirely DB-free against in-memory fakes of
``mrr.domain.repositories.ObjectRepository``, an edge store, and the
event-log read/append surface — no PostgreSQL, no ``sqlalchemy.Engine``.
Same "lightweight fake unit-of-work" pattern
``tests/unit/services/task_bundle/test_service.py`` uses.

ADR-0007 in one sentence, for orientation while reading these tests: a
lifecycle transition (``offer``/``respond``) is an append-only domain event,
NOT a new content revision — the content record
(``ObjectRepository.get_latest``) stays at revision 1 for the whole
``created -> offered -> {accepted, adapted, rejected, deferred,
unresolved}`` path, and the sender's signature — produced exactly once, at
``create`` — always verifies directly against it, with no historical scan.

Acceptance-test mapping (task-packets/E6-T01.yaml):

- "happy path, accepted" -> ``test_create_offer_respond_accepted_happy_path``.
- "happy path, adapted" ->
  ``test_respond_adapted_records_adapted_from_edge_and_event``,
  ``test_respond_adapted_with_multiple_transferred_objects_records_one_edge_each``.
- "rejected / deferred / unresolved paths" ->
  ``test_respond_rejected_deferred_or_unresolved_records_event``.
- "illegal-transition matrix" -> ``test_respond_before_offer_fails_closed``,
  ``test_second_respond_after_terminal_outcome_fails_closed``,
  ``test_offer_called_twice_fails_closed``.
- "a create referencing a PARTICIPANT_IDENTIFIABLE object is rejected" ->
  ``test_create_rejects_participant_identifiable_transferred_object``.
- "signature fail-closed matrix at respond" -> the
  ``test_respond_*_fails_closed`` group below.
- "adaptation with a nonexistent adapted object id is rejected" ->
  ``test_respond_adapted_with_nonexistent_adapted_object_id_is_rejected``.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from mrr.contracts import Practice, TransferContract
from mrr.crypto.exceptions import SignatureVerificationError
from mrr.crypto.keys import derive_key_id, encode_public_key, generate_ed25519_keypair
from mrr.domain.exceptions import (
    InvalidTransitionError,
    ObjectNotFoundError,
    ParticipantIdentifiableTransferError,
    RevisionConflictError,
    TransferContractNotFoundError,
    TransferKeyNotValidError,
    TransferSignerMismatchError,
    UnknownKeyIdError,
)
from mrr.domain.hashing_policy import sign_object
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject, TypedEdge
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.transfer.service import (
    RecordEdgesWithEvent,
    RecordEvent,
    RecordRevisionWithEvent,
    TransferService,
    _current_status,
)

# ---------------------------------------------------------------------------
# In-memory fakes (ObjectRepository protocol conformance, a minimal event
# journal, and an edge store), and fake "unit of work" callables for the
# content-revision path, the event-only path, and the edges+event path.
# Deliberate local duplicate of the E2-T03 test module's own fakes.
# ---------------------------------------------------------------------------


class FakeObjectRepository:
    """In-memory stand-in for ``mrr.domain.repositories.ObjectRepository``.
    Enforces the same optimistic-concurrency contract
    ``PostgresObjectRepository`` does, so a service bug that computes the
    wrong expected/next revision fails the test loudly.
    """

    def __init__(self) -> None:
        self._revisions: dict[str, list[StoredObject]] = {}

    def insert_revision(
        self, obj: StoredObject, expected_current_revision: int | None
    ) -> StoredObject:
        current = self._revisions.get(obj.id, [])
        current_max = current[-1].revision if current else None
        if current_max != expected_current_revision:
            raise RevisionConflictError(obj.id, expected_current_revision, current_max)
        expected_new_revision = (
            1 if expected_current_revision is None else expected_current_revision + 1
        )
        if obj.revision != expected_new_revision:
            raise ValueError(
                f"obj.revision ({obj.revision!r}) does not match the revision implied by "
                f"expected_current_revision ({expected_current_revision!r}): expected "
                f"{expected_new_revision!r}"
            )
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

    def overwrite_latest_body_for_test(self, id: str, body: dict[str, Any]) -> None:
        """Test-only: directly replace the CURRENT revision's stored ``body``
        — simulating a row tampered with after it was written, bypassing
        every service. Mirrors
        ``tests/unit/services/task_bundle/test_service.py``'s own helper of
        the same name.
        """
        revisions = self._revisions[id]
        revisions[-1] = replace(revisions[-1], body=body)


class FakeEventLog:
    """In-memory stand-in for the full event-log surface this module's
    service needs: ``read_all`` (causation-chain lookups, ``_current_status``)
    and an append entry point used by every fake unit-of-work callable below.
    """

    def __init__(self) -> None:
        self.appended: list[AppendedEvent] = []

    def append_for_test(self, event: DomainEvent) -> AppendedEvent:
        appended = AppendedEvent(
            event=event,
            sequence=len(self.appended) + 1,
            content_hash=f"sha256:{'b' * 64}",
            prev_hash=self.appended[-1].content_hash if self.appended else None,
        )
        self.appended.append(appended)
        return appended

    def read_all(self) -> list[AppendedEvent]:
        return list(self.appended)


class FakeEdgeStore:
    """In-memory stand-in for the ``edges`` table this module's
    ``bind_edge_unit_of_work`` writes to directly (bypassing
    ``EdgeRepository``, exactly like ``mrr.services.claim.service``'s own
    edge-writing helper does). Exposes ``edges_from`` so tests can assert
    against it exactly the way the packet's own acceptance test describes
    (``EdgeRepository.edges_from(adapted_object_id, "adapted_from")``).
    """

    def __init__(self) -> None:
        self.edges: list[TypedEdge] = []

    def edges_from(self, id: str, edge_type: str | None = None) -> list[TypedEdge]:
        return [
            edge
            for edge in self.edges
            if edge.source_id == id and (edge_type is None or edge.edge_type == edge_type)
        ]


def _fake_record(
    object_repository: FakeObjectRepository, event_log: FakeEventLog
) -> RecordRevisionWithEvent:
    def _record(
        obj: StoredObject,
        expected_current_revision: int | None,
        event: DomainEvent,
    ) -> tuple[StoredObject, AppendedEvent]:
        stored = object_repository.insert_revision(obj, expected_current_revision)
        appended = event_log.append_for_test(event)
        return stored, appended

    return _record


def _fake_record_event(event_log: FakeEventLog) -> RecordEvent:
    def _record_event(event: DomainEvent) -> AppendedEvent:
        return event_log.append_for_test(event)

    return _record_event


def _fake_record_edges(edge_store: FakeEdgeStore, event_log: FakeEventLog) -> RecordEdgesWithEvent:
    def _record_edges(
        edges: list[TypedEdge], event: DomainEvent
    ) -> tuple[list[TypedEdge], AppendedEvent]:
        edge_store.edges.extend(edges)
        appended = event_log.append_for_test(event)
        return edges, appended

    return _record_edges


class Harness:
    """Everything one test needs: the shared fake store/event log/edge
    store, and the ``TransferService`` under test.
    """

    def __init__(self) -> None:
        self.object_repository = FakeObjectRepository()
        self.event_log = FakeEventLog()
        self.edge_store = FakeEdgeStore()
        record = _fake_record(self.object_repository, self.event_log)
        record_event = _fake_record_event(self.event_log)
        record_edges = _fake_record_edges(self.edge_store, self.event_log)
        self.transfer_service = TransferService(
            self.object_repository, self.event_log, record, record_event, record_edges
        )


def _harness() -> Harness:
    return Harness()


_ACTOR = new_urn("agent-role")
_POLICY_VERSION = "policy-2026-07-01"


def _correlation_id() -> str:
    return new_urn("research-run")


# ---------------------------------------------------------------------------
# Fixture builders: keys/practices, TransferContract, seeded local objects.
# ---------------------------------------------------------------------------


def _key_entry(
    public_key: Ed25519PublicKey,
    *,
    valid_from: datetime,
    valid_until: datetime,
    state: str = "active",
) -> dict[str, Any]:
    return {
        "kid": derive_key_id(public_key),
        "algorithm": "Ed25519",
        "encoded_public_key": encode_public_key(public_key),
        "valid_from": valid_from,
        "valid_until": valid_until,
        "state": state,
    }


def _practice(*, practice_id: str, keys: list[dict[str, Any]]) -> Practice:
    now = datetime.now(UTC)
    data: dict[str, Any] = {
        "id": practice_id,
        "api_version": "mrr/v1alpha1",
        "kind": "Practice",
        "practice_id": practice_id,
        "revision": 1,
        "created_at": now,
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "name": "Fixture Practice",
        "description": "Fixture practice for transfer service unit tests.",
        "keys": keys,
        "governance_contacts": ["mailto:governance@fixture.invalid"],
        "supported_policy_versions": ["policy-2026-07-01"],
        "disclosure": {"max_disclosure": "PUBLIC", "trust_statement": "fixture"},
    }
    return Practice.model_validate(data)


def _sender_practice_and_key(
    *,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    state: str = "active",
) -> tuple[Practice, Ed25519PrivateKey, str]:
    now = datetime.now(UTC)
    private_key, public_key = generate_ed25519_keypair()
    practice_id = new_urn("practice")
    entry = _key_entry(
        public_key,
        valid_from=valid_from or (now - timedelta(days=1)),
        valid_until=valid_until or (now + timedelta(days=365)),
        state=state,
    )
    practice = _practice(practice_id=practice_id, keys=[entry])
    return practice, private_key, entry["kid"]


def _contract(
    *,
    sender_practice_id: str,
    key_id: str,
    transferred_objects: list[dict[str, str]] | None = None,
    **overrides: Any,
) -> TransferContract:
    now = datetime.now(UTC)
    data: dict[str, Any] = {
        "id": new_urn("transfer-contract"),
        "api_version": "mrr/v1alpha1",
        "kind": "TransferContract",
        "practice_id": sender_practice_id,
        "revision": 1,
        "created_at": now,
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "sender_practice_id": sender_practice_id,
        "receiver_practice_id": new_urn("practice"),
        "transferred_objects": transferred_objects
        or [{"id": new_urn("claim"), "content_hash": "sha256:" + "b" * 64}],
        "purpose": "Share the confirmatory claim for independent replication.",
        "permitted_uses": ["replication_analysis"],
        "disclosure_rules": {"max_disclosure": "INTERNAL"},
        "attribution_rules": {"cite_as": "Fixture Practice"},
        "caveats": [],
        "correction_subscription": True,
        "obligations": [{"kind": "preserve_attribution"}],
        "nonce": "n" * 16,
        "expires_at": now + timedelta(days=7),
        "signature": {
            "signer_practice_id": sender_practice_id,
            "key_id": key_id,
            "algorithm": "Ed25519",
            "signed_at": now,
            "value": "0" * 44,
        },
        "status": "created",
    }
    data.update(overrides)
    return TransferContract.model_validate(data)


def _sign(contract: TransferContract, private_key: Ed25519PrivateKey) -> TransferContract:
    """Sign ``contract`` for real (E1-T02 ``sign_object``) over the
    ``exclude_none=True`` form (ADR-0004) — the same canonical body
    ``TransferService``/``resolve_trusted_transfer_key`` verify against,
    directly off the persisted ``StoredObject.body``.
    """
    signature_value = sign_object(
        private_key, json.loads(contract.model_dump_json(exclude_none=True))
    )
    return contract.model_copy(
        update={"signature": contract.signature.model_copy(update={"value": signature_value})}
    )


def _seed_local_object(
    object_repository: FakeObjectRepository, *, id: str, kind: str, body: dict[str, Any]
) -> StoredObject:
    obj = StoredObject(
        id=id,
        api_version="mrr/v1alpha1",
        kind=kind,
        practice_id=new_urn("practice"),
        revision=1,
        created_at=datetime.now(UTC),
        created_by=new_urn("agent-role"),
        content_hash="sha256:" + "c" * 64,
        supersedes=None,
        labels=None,
        body=body,
    )
    object_repository.insert_revision(obj, expected_current_revision=None)
    return obj


def _create_and_offer(
    harness: Harness, contract: TransferContract, *, correlation_id: str | None = None
) -> str:
    correlation_id = correlation_id or _correlation_id()
    harness.transfer_service.create(
        contract, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    harness.transfer_service.offer(
        contract.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    return contract.id


# ---------------------------------------------------------------------------
# create(): the one-time content write.
# ---------------------------------------------------------------------------


def test_create_persists_revision_1_created_status_and_event() -> None:
    harness = _harness()
    practice, private_key, kid = _sender_practice_and_key()
    contract = _sign(_contract(sender_practice_id=practice.id, key_id=kid), private_key)

    stored = harness.transfer_service.create(
        contract, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    assert stored.revision == 1
    assert stored.body["status"] == "created"
    events = [e for e in harness.event_log.read_all() if e.event.object_id == contract.id]
    assert len(events) == 1
    assert events[0].event.event_type == "transfer.created"
    assert events[0].event.causation_id is None


def test_create_rejects_non_created_initial_status() -> None:
    harness = _harness()
    practice, private_key, kid = _sender_practice_and_key()
    contract = _sign(
        _contract(sender_practice_id=practice.id, key_id=kid, status="offered"), private_key
    )

    with pytest.raises(InvalidTransitionError) as excinfo:
        harness.transfer_service.create(
            contract, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
        )
    assert excinfo.value.to_state == "offered"
    with pytest.raises(ObjectNotFoundError):
        harness.object_repository.get_latest(contract.id)


def test_create_rejects_wrong_revision() -> None:
    harness = _harness()
    practice, private_key, kid = _sender_practice_and_key()
    contract = _sign(_contract(sender_practice_id=practice.id, key_id=kid, revision=2), private_key)

    with pytest.raises(ValueError, match="revision"):
        harness.transfer_service.create(
            contract, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
        )


def test_created_body_round_trips_through_transfer_contract_model_validate() -> None:
    harness = _harness()
    practice, private_key, kid = _sender_practice_and_key()
    contract = _sign(_contract(sender_practice_id=practice.id, key_id=kid), private_key)

    stored = harness.transfer_service.create(
        contract, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    round_tripped = TransferContract.model_validate(stored.body)
    assert round_tripped.status == "created"


# ---------------------------------------------------------------------------
# create(): PARTICIPANT_IDENTIFIABLE rejection (MRR-NFR-006).
# ---------------------------------------------------------------------------


def test_create_rejects_participant_identifiable_transferred_object() -> None:
    harness = _harness()
    practice, private_key, kid = _sender_practice_and_key()
    restricted_id = new_urn("task")
    _seed_local_object(
        harness.object_repository,
        id=restricted_id,
        kind="TaskBundle",
        body={"classification": "PARTICIPANT_IDENTIFIABLE"},
    )
    contract = _sign(
        _contract(
            sender_practice_id=practice.id,
            key_id=kid,
            transferred_objects=[{"id": restricted_id, "content_hash": "sha256:" + "d" * 64}],
        ),
        private_key,
    )

    with pytest.raises(ParticipantIdentifiableTransferError) as excinfo:
        harness.transfer_service.create(
            contract, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
        )
    assert excinfo.value.transferred_object_id == restricted_id

    with pytest.raises(ObjectNotFoundError):
        harness.object_repository.get_latest(contract.id)
    assert [e for e in harness.event_log.read_all() if e.event.object_id == contract.id] == []


def test_create_allows_transferred_object_with_public_classification() -> None:
    harness = _harness()
    practice, private_key, kid = _sender_practice_and_key()
    public_id = new_urn("task")
    _seed_local_object(
        harness.object_repository,
        id=public_id,
        kind="TaskBundle",
        body={"classification": "PUBLIC"},
    )
    contract = _sign(
        _contract(
            sender_practice_id=practice.id,
            key_id=kid,
            transferred_objects=[{"id": public_id, "content_hash": "sha256:" + "d" * 64}],
        ),
        private_key,
    )

    stored = harness.transfer_service.create(
        contract, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    assert stored.revision == 1


def test_create_allows_transferred_object_that_does_not_resolve_locally() -> None:
    """A referenced object with no local counterpart is not rejected — there
    is no existence constraint on transferred_objects entries, only a
    classification check on what DOES resolve (see the service's own
    ``_ensure_no_participant_identifiable_object`` docstring).
    """
    harness = _harness()
    practice, private_key, kid = _sender_practice_and_key()
    contract = _sign(
        _contract(
            sender_practice_id=practice.id,
            key_id=kid,
            transferred_objects=[{"id": new_urn("claim"), "content_hash": "sha256:" + "d" * 64}],
        ),
        private_key,
    )

    stored = harness.transfer_service.create(
        contract, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    assert stored.revision == 1


def test_create_allows_transferred_object_with_no_classification_field() -> None:
    """Most object kinds carry no top-level `classification` field at all
    (only TaskBundle does today) — such a referenced object is never
    rejected by this check.
    """
    harness = _harness()
    practice, private_key, kid = _sender_practice_and_key()
    claim_id = new_urn("claim")
    _seed_local_object(
        harness.object_repository, id=claim_id, kind="Claim", body={"status": "draft"}
    )
    contract = _sign(
        _contract(
            sender_practice_id=practice.id,
            key_id=kid,
            transferred_objects=[{"id": claim_id, "content_hash": "sha256:" + "d" * 64}],
        ),
        private_key,
    )

    stored = harness.transfer_service.create(
        contract, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    assert stored.revision == 1


# ---------------------------------------------------------------------------
# offer(): created -> offered. ADR-0007: EVENT-ONLY.
# ---------------------------------------------------------------------------


def test_offer_transitions_and_persists_event_no_new_revision() -> None:
    harness = _harness()
    practice, private_key, kid = _sender_practice_and_key()
    contract = _sign(_contract(sender_practice_id=practice.id, key_id=kid), private_key)
    harness.transfer_service.create(
        contract, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    offered = harness.transfer_service.offer(
        contract.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    assert offered.status == "offered"
    assert offered.content.revision == 1
    assert offered.content.body["status"] == "created"  # the creation snapshot, unchanged


def test_offer_called_twice_fails_closed() -> None:
    harness = _harness()
    practice, private_key, kid = _sender_practice_and_key()
    contract = _sign(_contract(sender_practice_id=practice.id, key_id=kid), private_key)
    _create_and_offer(harness, contract)

    with pytest.raises(InvalidTransitionError):
        harness.transfer_service.offer(
            contract.id,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )

    events = [e for e in harness.event_log.read_all() if e.event.object_id == contract.id]
    assert len(events) == 2  # created, offered — the second offer appended nothing


def test_offer_unknown_transfer_id_raises_not_found() -> None:
    harness = _harness()
    with pytest.raises(TransferContractNotFoundError):
        harness.transfer_service.offer(
            new_urn("transfer-contract"),
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )


# ---------------------------------------------------------------------------
# respond(): the happy accepted path, full create -> offer -> respond.
# ---------------------------------------------------------------------------


def test_create_offer_respond_accepted_happy_path() -> None:
    harness = _harness()
    practice, private_key, kid = _sender_practice_and_key()
    contract = _sign(_contract(sender_practice_id=practice.id, key_id=kid), private_key)
    _create_and_offer(harness, contract)

    result = harness.transfer_service.respond(
        contract.id,
        "accepted",
        practice,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert result.status == "accepted"
    assert result.content.revision == 1
    assert harness.object_repository.get_latest(contract.id).revision == 1

    events = [e.event for e in harness.event_log.read_all() if e.event.object_id == contract.id]
    assert [e.event_type for e in events] == [
        "transfer.created",
        "transfer.offered",
        "transfer.responded",
    ]
    assert events[1].causation_id == events[0].id
    assert events[2].causation_id == events[1].id
    assert {e.object_revision for e in events} == {1}
    assert events[2].payload["to_status"] == "accepted"
    assert harness.edge_store.edges == []  # no adaptation, no edge


@pytest.mark.parametrize("decision", ["rejected", "deferred", "unresolved"])
def test_respond_rejected_deferred_or_unresolved_records_event(decision: str) -> None:
    harness = _harness()
    practice, private_key, kid = _sender_practice_and_key()
    contract = _sign(_contract(sender_practice_id=practice.id, key_id=kid), private_key)
    _create_and_offer(harness, contract)

    result = harness.transfer_service.respond(
        contract.id,
        decision,  # type: ignore[arg-type]
        practice,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert result.status == decision
    assert result.content.revision == 1
    assert result.appended_event.event.payload["decision"] == decision
    assert harness.edge_store.edges == []


# ---------------------------------------------------------------------------
# respond(): illegal-transition matrix.
# ---------------------------------------------------------------------------


def test_respond_before_offer_fails_closed() -> None:
    harness = _harness()
    practice, private_key, kid = _sender_practice_and_key()
    contract = _sign(_contract(sender_practice_id=practice.id, key_id=kid), private_key)
    harness.transfer_service.create(
        contract, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    # Never offered — still "created".

    with pytest.raises(InvalidTransitionError) as excinfo:
        harness.transfer_service.respond(
            contract.id,
            "accepted",
            practice,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )
    assert excinfo.value.from_state == "created"
    assert excinfo.value.to_state == "accepted"

    events = [e for e in harness.event_log.read_all() if e.event.object_id == contract.id]
    assert len(events) == 1  # only "created"


def test_second_respond_after_terminal_outcome_fails_closed() -> None:
    harness = _harness()
    practice, private_key, kid = _sender_practice_and_key()
    contract = _sign(_contract(sender_practice_id=practice.id, key_id=kid), private_key)
    _create_and_offer(harness, contract)
    harness.transfer_service.respond(
        contract.id,
        "accepted",
        practice,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    with pytest.raises(InvalidTransitionError):
        harness.transfer_service.respond(
            contract.id,
            "rejected",
            practice,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )

    events = [e for e in harness.event_log.read_all() if e.event.object_id == contract.id]
    assert len(events) == 3  # created, offered, responded(accepted) — no second response


def test_respond_unknown_transfer_id_raises_not_found() -> None:
    harness = _harness()
    practice, _, _ = _sender_practice_and_key()
    with pytest.raises(TransferContractNotFoundError):
        harness.transfer_service.respond(
            new_urn("transfer-contract"),
            "accepted",
            practice,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )


# ---------------------------------------------------------------------------
# respond(): signature fail-closed matrix — checked BEFORE any outcome is
# recorded, for every decision alike, including reject/defer/unresolved
# (task-packets/E6-T01.yaml derived_decisions (c)).
# ---------------------------------------------------------------------------


def test_respond_signer_mismatch_fails_closed_and_persists_nothing() -> None:
    harness = _harness()
    practice, private_key, kid = _sender_practice_and_key()
    other_practice, _, _ = _sender_practice_and_key()
    contract = _sign(_contract(sender_practice_id=practice.id, key_id=kid), private_key)
    _create_and_offer(harness, contract)

    with pytest.raises(TransferSignerMismatchError):
        harness.transfer_service.respond(
            contract.id,
            "accepted",
            other_practice,  # wrong trusted sender
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )

    events = [e for e in harness.event_log.read_all() if e.event.object_id == contract.id]
    assert len(events) == 2  # created, offered — no response recorded


def test_respond_unknown_kid_fails_closed_and_persists_nothing() -> None:
    harness = _harness()
    practice, private_key, kid = _sender_practice_and_key()
    contract = _sign(_contract(sender_practice_id=practice.id, key_id=kid), private_key)
    _create_and_offer(harness, contract)

    tampered_signature_key_id = harness.object_repository.get_latest(contract.id).body
    tampered_signature_key_id = dict(tampered_signature_key_id)
    tampered_signature_key_id["signature"] = dict(tampered_signature_key_id["signature"])
    tampered_signature_key_id["signature"]["key_id"] = "kid:does-not-exist"
    harness.object_repository.overwrite_latest_body_for_test(contract.id, tampered_signature_key_id)

    with pytest.raises(UnknownKeyIdError):
        harness.transfer_service.respond(
            contract.id,
            "accepted",
            practice,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )

    events = [e for e in harness.event_log.read_all() if e.event.object_id == contract.id]
    assert len(events) == 2


@pytest.mark.parametrize(
    ("valid_from_delta", "valid_until_delta"),
    [
        (timedelta(days=-10), timedelta(days=-1)),  # expired
        (timedelta(days=1), timedelta(days=10)),  # not yet valid
    ],
    ids=["expired", "not-yet-valid"],
)
def test_respond_key_not_valid_at_evaluation_instant_fails_closed(
    valid_from_delta: timedelta, valid_until_delta: timedelta
) -> None:
    harness = _harness()
    now = datetime.now(UTC)
    practice, private_key, kid = _sender_practice_and_key(
        valid_from=now + valid_from_delta, valid_until=now + valid_until_delta
    )
    contract = _sign(_contract(sender_practice_id=practice.id, key_id=kid), private_key)
    _create_and_offer(harness, contract)

    with pytest.raises(TransferKeyNotValidError):
        harness.transfer_service.respond(
            contract.id,
            "accepted",
            practice,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )

    events = [e for e in harness.event_log.read_all() if e.event.object_id == contract.id]
    assert len(events) == 2


@pytest.mark.parametrize("state", ["revoked", "rotated"])
def test_respond_revoked_or_rotated_key_fails_closed(state: str) -> None:
    harness = _harness()
    practice, private_key, kid = _sender_practice_and_key()
    contract = _sign(_contract(sender_practice_id=practice.id, key_id=kid), private_key)
    _create_and_offer(harness, contract)

    revoked_practice = practice.model_copy(
        update={"keys": [practice.keys[0].model_copy(update={"state": state})]}
    )

    with pytest.raises(TransferKeyNotValidError):
        harness.transfer_service.respond(
            contract.id,
            "accepted",
            revoked_practice,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )

    events = [e for e in harness.event_log.read_all() if e.event.object_id == contract.id]
    assert len(events) == 2


def test_respond_tampered_content_fails_closed_with_signature_verification_error() -> None:
    """CRITICAL — content tampered directly (bypassing every service) while
    ``signature.value`` is left untouched: the reconstructed contract's
    fields no longer canonicalize to what the signature actually covers.
    """
    harness = _harness()
    practice, private_key, kid = _sender_practice_and_key()
    contract = _sign(_contract(sender_practice_id=practice.id, key_id=kid), private_key)
    _create_and_offer(harness, contract)

    stored = harness.object_repository.get_latest(contract.id)
    tampered_body = dict(stored.body)
    tampered_body["purpose"] = "a completely different purpose the sender never signed"
    harness.object_repository.overwrite_latest_body_for_test(contract.id, tampered_body)

    with pytest.raises(SignatureVerificationError):
        harness.transfer_service.respond(
            contract.id,
            "accepted",
            practice,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )

    events = [e for e in harness.event_log.read_all() if e.event.object_id == contract.id]
    assert len(events) == 2


def test_respond_verifies_signature_before_lifecycle_transition_check() -> None:
    """The signature is verified even when the contract has not yet been
    offered — proving the check order the packet's derived_decisions (c)
    requires: signature resolution never depends on, or waits for, lifecycle
    legality.
    """
    harness = _harness()
    practice, private_key, kid = _sender_practice_and_key()
    other_practice, _, _ = _sender_practice_and_key()
    contract = _sign(_contract(sender_practice_id=practice.id, key_id=kid), private_key)
    harness.transfer_service.create(
        contract, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    # Still "created" — never offered.

    with pytest.raises(TransferSignerMismatchError):
        harness.transfer_service.respond(
            contract.id,
            "accepted",
            other_practice,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )


# ---------------------------------------------------------------------------
# respond("adapted", ...): MRR-FR-082.
# ---------------------------------------------------------------------------


def test_respond_adapted_records_adapted_from_edge_and_event() -> None:
    harness = _harness()
    practice, private_key, kid = _sender_practice_and_key()
    transferred_object_id = new_urn("claim")
    contract = _sign(
        _contract(
            sender_practice_id=practice.id,
            key_id=kid,
            transferred_objects=[
                {"id": transferred_object_id, "content_hash": "sha256:" + "d" * 64}
            ],
        ),
        private_key,
    )
    _create_and_offer(harness, contract)
    adapted_object_id = new_urn("claim")
    _seed_local_object(
        harness.object_repository, id=adapted_object_id, kind="Claim", body={"status": "draft"}
    )

    result = harness.transfer_service.respond(
        contract.id,
        "adapted",
        practice,
        adapted_object_id=adapted_object_id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert result.status == "adapted"
    assert result.content.revision == 1
    assert result.appended_event.event.event_type == "transfer.responded"

    edges = harness.edge_store.edges_from(adapted_object_id, "adapted_from")
    assert len(edges) == 1
    assert edges[0].source_id == adapted_object_id
    assert edges[0].target_id == transferred_object_id

    events = [e.event for e in harness.event_log.read_all() if e.event.object_id == contract.id]
    assert [e.event_type for e in events] == [
        "transfer.created",
        "transfer.offered",
        "transfer.responded",
    ]


def test_respond_adapted_with_multiple_transferred_objects_records_one_edge_each() -> None:
    harness = _harness()
    practice, private_key, kid = _sender_practice_and_key()
    first_id, second_id = new_urn("claim"), new_urn("claim")
    contract = _sign(
        _contract(
            sender_practice_id=practice.id,
            key_id=kid,
            transferred_objects=[
                {"id": first_id, "content_hash": "sha256:" + "d" * 64},
                {"id": second_id, "content_hash": "sha256:" + "e" * 64},
            ],
        ),
        private_key,
    )
    _create_and_offer(harness, contract)
    adapted_object_id = new_urn("claim")
    _seed_local_object(
        harness.object_repository, id=adapted_object_id, kind="Claim", body={"status": "draft"}
    )

    result = harness.transfer_service.respond(
        contract.id,
        "adapted",
        practice,
        adapted_object_id=adapted_object_id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    edges = harness.edge_store.edges_from(adapted_object_id, "adapted_from")
    assert {edge.target_id for edge in edges} == {first_id, second_id}
    assert len(edges) == 2
    # Exactly ONE transfer.responded event, shared atomically by both edges.
    responded_events = [
        e
        for e in harness.event_log.read_all()
        if e.event.object_id == contract.id and e.event.event_type == "transfer.responded"
    ]
    assert len(responded_events) == 1
    assert result.appended_event.event.id == responded_events[0].event.id


def test_respond_adapted_without_adapted_object_id_raises_value_error() -> None:
    harness = _harness()
    practice, private_key, kid = _sender_practice_and_key()
    contract = _sign(_contract(sender_practice_id=practice.id, key_id=kid), private_key)
    _create_and_offer(harness, contract)

    with pytest.raises(ValueError, match="adapted_object_id"):
        harness.transfer_service.respond(
            contract.id,
            "adapted",
            practice,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )

    events = [e for e in harness.event_log.read_all() if e.event.object_id == contract.id]
    assert len(events) == 2  # created, offered — nothing recorded for the bad adaptation
    assert harness.edge_store.edges == []


def test_respond_adapted_with_nonexistent_adapted_object_id_is_rejected() -> None:
    harness = _harness()
    practice, private_key, kid = _sender_practice_and_key()
    contract = _sign(_contract(sender_practice_id=practice.id, key_id=kid), private_key)
    _create_and_offer(harness, contract)
    nonexistent_id = new_urn("claim")

    with pytest.raises(ObjectNotFoundError):
        harness.transfer_service.respond(
            contract.id,
            "adapted",
            practice,
            adapted_object_id=nonexistent_id,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )

    assert harness.edge_store.edges == []
    events = [e for e in harness.event_log.read_all() if e.event.object_id == contract.id]
    assert len(events) == 2  # created, offered — no transfer.responded event


# ---------------------------------------------------------------------------
# _current_status: direct unit test of the ADR-0007 helper, mirroring
# mrr.services.task_bundle.service's own test of the identical helper.
# ---------------------------------------------------------------------------


def test_current_status_is_event_derived() -> None:
    event_log = FakeEventLog()
    transfer_id = new_urn("transfer-contract")
    other_transfer_id = new_urn("transfer-contract")
    content = StoredObject(
        id=transfer_id,
        api_version="mrr/v1alpha1",
        kind="TransferContract",
        practice_id=new_urn("practice"),
        revision=1,
        created_at=datetime.now(UTC),
        created_by=new_urn("agent-role"),
        content_hash="sha256:" + "a" * 64,
        supersedes=None,
        labels=None,
        body={"status": "created"},
    )

    assert _current_status(event_log, content) == "created"

    def _record(event_type: str, object_id: str, payload: dict[str, Any]) -> None:
        event_log.append_for_test(
            DomainEvent(
                id=new_urn("domain-event"),
                event_type=event_type,
                occurred_at=datetime.now(UTC),
                actor=_ACTOR,
                policy_version=_POLICY_VERSION,
                causation_id=None,
                correlation_id=_correlation_id(),
                object_id=object_id,
                object_revision=1,
                payload=payload,
            )
        )

    _record("transfer.offered", other_transfer_id, {"to_status": "offered"})  # noise
    _record("transfer.created", transfer_id, {"status": "created"})  # not a transition
    assert _current_status(event_log, content) == "created"

    _record("transfer.offered", transfer_id, {"from_status": "created", "to_status": "offered"})
    assert _current_status(event_log, content) == "offered"

    _record(
        "transfer.responded",
        transfer_id,
        {"from_status": "offered", "to_status": "accepted", "decision": "accepted"},
    )
    assert _current_status(event_log, content) == "accepted"


# ---------------------------------------------------------------------------
# Event provenance (MRR-NFR-001).
# ---------------------------------------------------------------------------


def test_events_carry_complete_provenance() -> None:
    harness = _harness()
    practice, private_key, kid = _sender_practice_and_key()
    contract = _sign(_contract(sender_practice_id=practice.id, key_id=kid), private_key)
    correlation_id = _correlation_id()

    harness.transfer_service.create(
        contract, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    harness.transfer_service.offer(
        contract.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    events = [e.event for e in harness.event_log.read_all() if e.event.object_id == contract.id]
    for event in events:
        assert event.actor == _ACTOR
        assert event.policy_version == _POLICY_VERSION
        assert event.correlation_id == correlation_id
        assert event.object_id == contract.id
        assert event.occurred_at.tzinfo is not None
        assert event.object_revision == 1
    assert events[0].causation_id is None
    assert events[1].causation_id == events[0].id
