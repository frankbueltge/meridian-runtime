"""Integration tests for ``mrr.services.transfer.service.TransferService``
(task-packets/E6-T01.yaml), run against a real PostgreSQL via the
``postgres_engine`` fixture in tests/integration/conftest.py — wired exactly
as production code would: ``PostgresObjectRepository``/``PostgresEventLog``
over the fixture's engine, with ``bind_unit_of_work``/
``bind_event_unit_of_work``/``bind_edge_unit_of_work`` closing over them to
produce the atomic ``record``/``record_event``/``record_edges``
dependencies. Skips visibly if ``MRR_TEST_DATABASE_URL`` is unset (fails
hard instead if ``CI=true``) — see that module's docstring.

Acceptance-test mapping (task-packets/E6-T01.yaml, integration tier):

- "create -> offer -> respond persist atomically with their events" ->
  ``test_create_offer_respond_persists_atomically_with_events``.
- "querying the generic objects/edges tables directly confirms no migration
  was needed (kind=\"TransferContract\" rows exist; the adapted_from edge
  satisfies the existing CHECK constraint)" ->
  ``test_no_migration_needed_kind_and_edge_type_accepted_by_existing_schema``.
- "the edge is queryable via EdgeRepository.edges_from(adapted_object_id,
  'adapted_from')" -> ``test_respond_adapted_edge_queryable_via_edge_repository``.
- "illegal-transition matrix ... fails closed with no event appended" ->
  ``test_respond_before_offer_rolls_back_nothing_persisted``.
- signature fail-closed matrix, straight from the database ->
  ``test_respond_signer_mismatch_against_real_database_persists_nothing``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from mrr.contracts import Practice, TransferContract
from mrr.crypto.keys import derive_key_id, encode_public_key, generate_ed25519_keypair
from mrr.domain.exceptions import InvalidTransitionError, TransferSignerMismatchError
from mrr.domain.hashing_policy import sign_object
from mrr.domain.identity import new_urn
from mrr.persistence.repositories import (
    PostgresEdgeRepository,
    PostgresEventLog,
    PostgresObjectRepository,
)
from mrr.persistence.tables import domain_events_table, edges_table, objects_table
from mrr.services.transfer.service import (
    TransferService,
    bind_edge_unit_of_work,
    bind_event_unit_of_work,
    bind_unit_of_work,
)
from sqlalchemy import Engine

_POLICY_VERSION = "policy-2026-07-01"
_ACTOR = new_urn("agent-role")


def _service_for(
    engine: Engine,
) -> tuple[TransferService, PostgresObjectRepository, PostgresEventLog, PostgresEdgeRepository]:
    object_repository = PostgresObjectRepository(engine)
    event_log = PostgresEventLog(engine)
    edge_repository = PostgresEdgeRepository(engine)
    record = bind_unit_of_work(engine, object_repository, event_log)
    record_event = bind_event_unit_of_work(engine, event_log)
    record_edges = bind_edge_unit_of_work(engine, event_log)
    service = TransferService(object_repository, event_log, record, record_event, record_edges)
    return service, object_repository, event_log, edge_repository


def _practice_and_key(engine: Engine | None = None) -> tuple[Practice, Any, str]:
    now = datetime.now(UTC)
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
            "created_at": now,
            "created_by": new_urn("agent-role"),
            "content_hash": "sha256:" + "a" * 64,
            "name": "Fixture Practice",
            "description": "Fixture practice for transfer service integration tests.",
            "keys": [
                {
                    "kid": kid,
                    "algorithm": "Ed25519",
                    "encoded_public_key": encode_public_key(public_key),
                    "valid_from": now - timedelta(days=1),
                    "valid_until": now + timedelta(days=365),
                    "state": "active",
                }
            ],
            "governance_contacts": ["mailto:governance@fixture.invalid"],
            "supported_policy_versions": ["policy-2026-07-01"],
            "disclosure": {"max_disclosure": "PUBLIC", "trust_statement": "fixture"},
        }
    )
    return practice, private_key, kid


def _contract(*, sender_practice_id: str, key_id: str, **overrides: Any) -> TransferContract:
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
        "transferred_objects": [{"id": new_urn("claim"), "content_hash": "sha256:" + "b" * 64}],
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


def _sign(contract: TransferContract, private_key: Any) -> TransferContract:
    signature_value = sign_object(
        private_key, json.loads(contract.model_dump_json(exclude_none=True))
    )
    return contract.model_copy(
        update={"signature": contract.signature.model_copy(update={"value": signature_value})}
    )


def test_create_offer_respond_persists_atomically_with_events(postgres_engine: Engine) -> None:
    service, object_repository, event_log, _edge_repository = _service_for(postgres_engine)
    practice, private_key, kid = _practice_and_key()
    contract = _sign(_contract(sender_practice_id=practice.id, key_id=kid), private_key)
    correlation_id = new_urn("research-run")

    service.create(
        contract, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    service.offer(
        contract.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    result = service.respond(
        contract.id,
        "accepted",
        practice,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    assert result.status == "accepted"

    stored = object_repository.get_latest(contract.id)
    assert stored.revision == 1
    assert stored.body["status"] == "created"  # the creation-time snapshot, unchanged (ADR-0007)
    assert stored.kind == "TransferContract"

    revisions = object_repository.list_revisions(contract.id)
    assert [rev.revision for rev in revisions] == [1]  # never a second content revision

    events = [e for e in event_log.read_all() if e.event.object_id == contract.id]
    assert [e.event.event_type for e in events] == [
        "transfer.created",
        "transfer.offered",
        "transfer.responded",
    ]
    assert events[1].event.causation_id == events[0].event.id
    assert events[2].event.causation_id == events[1].event.id


def test_respond_adapted_edge_queryable_via_edge_repository(postgres_engine: Engine) -> None:
    service, object_repository, event_log, edge_repository = _service_for(postgres_engine)
    practice, private_key, kid = _practice_and_key()
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
    correlation_id = new_urn("research-run")
    service.create(
        contract, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    service.offer(
        contract.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    # A real, independently-persisted local object to adapt into.
    adapted_object_id = new_urn("claim")
    with postgres_engine.begin() as conn:
        conn.execute(
            sa.insert(objects_table).values(
                id=adapted_object_id,
                revision=1,
                api_version="mrr/v1alpha1",
                kind="Claim",
                practice_id=new_urn("practice"),
                created_at=datetime.now(UTC),
                created_by=_ACTOR,
                content_hash="sha256:" + "c" * 64,
                supersedes=None,
                labels=None,
                body={"status": "draft"},
            )
        )

    result = service.respond(
        contract.id,
        "adapted",
        practice,
        adapted_object_id=adapted_object_id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    assert result.status == "adapted"

    # The adapted_from edge is queryable via the real EdgeRepository — the
    # existing edges CHECK constraint accepts it, no migration needed.
    edges = edge_repository.edges_from(adapted_object_id, "adapted_from")
    assert len(edges) == 1
    assert edges[0].source_id == adapted_object_id
    assert edges[0].target_id == transferred_object_id

    events = [e for e in event_log.read_all() if e.event.object_id == contract.id]
    assert [e.event.event_type for e in events] == [
        "transfer.created",
        "transfer.offered",
        "transfer.responded",
    ]

    # Content record still untouched — the edge/event were the only writes.
    stored = object_repository.get_latest(contract.id)
    assert stored.revision == 1


def test_no_migration_needed_kind_and_edge_type_accepted_by_existing_schema(
    postgres_engine: Engine,
) -> None:
    """Direct raw-SQL confirmation, bypassing every repository/service: the
    generic ``objects``/``edges`` tables (unmodified by this task) already
    accept ``kind="TransferContract"`` rows and ``edge_type="adapted_from"``
    edges — task-packets/E6-T01.yaml forbidden_changes: "confirmed
    unnecessary ... a new kind='TransferContract' is just new rows ...
    using them requires zero DDL change."
    """
    service, object_repository, _event_log, _edge_repository = _service_for(postgres_engine)
    practice, private_key, kid = _practice_and_key()
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
    correlation_id = new_urn("research-run")
    service.create(
        contract, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    service.offer(
        contract.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    adapted_object_id = new_urn("claim")
    with postgres_engine.begin() as conn:
        conn.execute(
            sa.insert(objects_table).values(
                id=adapted_object_id,
                revision=1,
                api_version="mrr/v1alpha1",
                kind="Claim",
                practice_id=new_urn("practice"),
                created_at=datetime.now(UTC),
                created_by=_ACTOR,
                content_hash="sha256:" + "c" * 64,
                supersedes=None,
                labels=None,
                body={"status": "draft"},
            )
        )
    service.respond(
        contract.id,
        "adapted",
        practice,
        adapted_object_id=adapted_object_id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    with postgres_engine.connect() as conn:
        kind_row = conn.execute(
            sa.select(objects_table.c.kind).where(objects_table.c.id == contract.id)
        ).one()
        assert kind_row.kind == "TransferContract"

        edge_row = conn.execute(
            sa.select(edges_table.c.edge_type).where(edges_table.c.source_id == adapted_object_id)
        ).one()
        assert edge_row.edge_type == "adapted_from"

        event_count = conn.execute(
            sa.select(sa.func.count())
            .select_from(domain_events_table)
            .where(domain_events_table.c.object_id == contract.id)
        ).scalar_one()
        assert event_count == 3

    # And the object repository's own reconstruction still round-trips.
    stored = object_repository.get_latest(contract.id)
    assert TransferContract.model_validate(stored.body).kind == "TransferContract"


# ---------------------------------------------------------------------------
# Illegal transitions roll back — nothing persisted beyond what already was.
# ---------------------------------------------------------------------------


def test_respond_before_offer_rolls_back_nothing_persisted(postgres_engine: Engine) -> None:
    service, object_repository, event_log, _edge_repository = _service_for(postgres_engine)
    practice, private_key, kid = _practice_and_key()
    contract = _sign(_contract(sender_practice_id=practice.id, key_id=kid), private_key)
    correlation_id = new_urn("research-run")
    service.create(
        contract, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    # Never offered.

    with pytest.raises(InvalidTransitionError):
        service.respond(
            contract.id,
            "accepted",
            practice,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=correlation_id,
        )

    events = [e for e in event_log.read_all() if e.event.object_id == contract.id]
    assert len(events) == 1  # only "created" — nothing rolled forward
    assert object_repository.get_latest(contract.id).revision == 1


def test_second_respond_after_terminal_outcome_rolls_back(postgres_engine: Engine) -> None:
    service, _object_repository, event_log, _edge_repository = _service_for(postgres_engine)
    practice, private_key, kid = _practice_and_key()
    contract = _sign(_contract(sender_practice_id=practice.id, key_id=kid), private_key)
    correlation_id = new_urn("research-run")
    service.create(
        contract, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    service.offer(
        contract.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    service.respond(
        contract.id,
        "rejected",
        practice,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    with pytest.raises(InvalidTransitionError):
        service.respond(
            contract.id,
            "accepted",
            practice,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=correlation_id,
        )

    events = [e for e in event_log.read_all() if e.event.object_id == contract.id]
    assert len(events) == 3  # created, offered, responded(rejected) — no second response


# ---------------------------------------------------------------------------
# Signature fail-closed, straight from the database.
# ---------------------------------------------------------------------------


def test_respond_signer_mismatch_against_real_database_persists_nothing(
    postgres_engine: Engine,
) -> None:
    service, object_repository, event_log, _edge_repository = _service_for(postgres_engine)
    practice, private_key, kid = _practice_and_key()
    other_practice, _, _ = _practice_and_key()
    contract = _sign(_contract(sender_practice_id=practice.id, key_id=kid), private_key)
    correlation_id = new_urn("research-run")
    service.create(
        contract, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    service.offer(
        contract.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    with pytest.raises(TransferSignerMismatchError):
        service.respond(
            contract.id,
            "accepted",
            other_practice,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=correlation_id,
        )

    events = [e for e in event_log.read_all() if e.event.object_id == contract.id]
    assert len(events) == 2  # created, offered — no response recorded
    assert object_repository.get_latest(contract.id).revision == 1
