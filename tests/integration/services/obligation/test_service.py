"""Integration tests for ``mrr.services.obligation.service.ObligationService``
(task-packets/E6-T02.yaml), run against a real PostgreSQL via the
``postgres_engine`` fixture in tests/integration/conftest.py — wired exactly
as production code would: ``PostgresObjectRepository``/``PostgresEdgeRepository``/
``PostgresEventLog`` over the fixture's engine.

Unlike the unit tier, this module DOES drive a real
``mrr.services.transfer.service.TransferService`` (E6-T01, now present in the
codebase) to create -> offer -> respond a genuine, signed ``TransferContract``
— task-packets/E6-T02.yaml's own acceptance_tests describes this as
"standing in for E6-T01's own service if not yet merged"; it IS merged
(commit f32273c, PR #41), so this integration tier exercises the real
cross-service composition rather than a stand-in. ``ObligationService``
itself still never imports or calls ``TransferService`` (forbidden_changes)
— it only reads the ``TransferContract`` row and ``transfer.responded``
event ``TransferService`` already wrote, through the generic
``ObjectRepository``/event log.

Acceptance-test mapping (task-packets/E6-T02.yaml, integration tier):

- "an accepted/adapted TransferContract ... drives materialize_from_transfer
  -> propagate -> resolve; confirms subject_to_obligation edges and
  Obligation revisions persist atomically with their events and that no
  migration was needed" -> ``test_materialize_propagate_resolve_persist_atomically``,
  ``test_no_migration_needed_kind_and_edge_type_accepted_by_existing_schema``.
- materialize_from_transfer is at-most-once per transfer against a real
  database (reviewer-driven amendment) ->
  ``test_materialize_is_at_most_once_per_transfer_against_real_postgres``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from mrr.contracts import Obligation, Practice, TransferContract
from mrr.crypto.keys import derive_key_id, encode_public_key, generate_ed25519_keypair
from mrr.domain.exceptions import ObligationsAlreadyMaterializedError
from mrr.domain.hashing_policy import sign_object
from mrr.domain.identity import new_urn
from mrr.persistence.repositories import (
    PostgresEdgeRepository,
    PostgresEventLog,
    PostgresObjectRepository,
)
from mrr.persistence.tables import domain_events_table, edges_table, objects_table
from mrr.services.obligation.service import (
    ObligationService,
    bind_revision_with_edges_unit_of_work,
)
from mrr.services.obligation.service import bind_unit_of_work as bind_obligation_unit_of_work
from mrr.services.transfer.service import TransferService, bind_event_unit_of_work
from mrr.services.transfer.service import bind_edge_unit_of_work as bind_transfer_edge_unit_of_work
from mrr.services.transfer.service import bind_unit_of_work as bind_transfer_unit_of_work
from sqlalchemy import Engine

_POLICY_VERSION = "policy-2026-07-01"
_ACTOR = new_urn("agent-role")


def _transfer_service_for(engine: Engine) -> tuple[TransferService, PostgresObjectRepository]:
    object_repository = PostgresObjectRepository(engine)
    event_log = PostgresEventLog(engine)
    record = bind_transfer_unit_of_work(engine, object_repository, event_log)
    record_event = bind_event_unit_of_work(engine, event_log)
    record_edges = bind_transfer_edge_unit_of_work(engine, event_log)
    service = TransferService(object_repository, event_log, record, record_event, record_edges)
    return service, object_repository


def _obligation_service_for(
    engine: Engine,
) -> tuple[ObligationService, PostgresObjectRepository, PostgresEdgeRepository, PostgresEventLog]:
    object_repository = PostgresObjectRepository(engine)
    edge_repository = PostgresEdgeRepository(engine)
    event_log = PostgresEventLog(engine)
    record = bind_obligation_unit_of_work(engine, object_repository, event_log)
    record_revision_with_edges = bind_revision_with_edges_unit_of_work(
        engine, object_repository, event_log
    )
    service = ObligationService(
        object_repository, edge_repository, event_log, record, record_revision_with_edges
    )
    return service, object_repository, edge_repository, event_log


def _practice_and_key() -> tuple[Practice, Any, str]:
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
            "description": "Fixture practice for obligation service integration tests.",
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
        "caveats": ["Sample size is small; treat estimates as provisional."],
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


def test_materialize_propagate_resolve_persist_atomically(postgres_engine: Engine) -> None:
    transfer_service, transfer_object_repository = _transfer_service_for(postgres_engine)
    obligation_service, object_repository, edge_repository, event_log = _obligation_service_for(
        postgres_engine
    )
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

    transfer_service.create(
        contract, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    transfer_service.offer(
        contract.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    transfer_service.respond(
        contract.id,
        "accepted",
        practice,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    # Two Obligations materialize: the explicit preserve_attribution stub,
    # plus one retain_caveat Obligation from the non-empty caveats field.
    materialized = obligation_service.materialize_from_transfer(
        contract.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    assert len(materialized) == 2
    preserve_attribution = next(
        o for o in materialized if o.body["obligation_kind"] == "preserve_attribution"
    )

    assert preserve_attribution.body["bound_objects"] == [transferred_object_id]
    bound_edges = edge_repository.edges_to(preserve_attribution.id, "subject_to_obligation")
    assert len(bound_edges) == 1
    assert bound_edges[0].source_id == transferred_object_id

    # A real, independently-persisted local object built ON the transferred
    # claim (a depends_on edge — exactly what propagate should discover).
    dependent_id = new_urn("claim")
    with postgres_engine.begin() as conn:
        conn.execute(
            sa.insert(objects_table).values(
                id=dependent_id,
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
        conn.execute(
            sa.insert(edges_table).values(
                id=new_urn("edge"),
                source_id=dependent_id,
                target_id=transferred_object_id,
                edge_type="depends_on",
                created_at=datetime.now(UTC),
                created_by=_ACTOR,
                practice_id=new_urn("practice"),
                scope=None,
                status="active",
            )
        )

    propagated = obligation_service.propagate(
        preserve_attribution.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )
    assert propagated.revision == 2
    assert propagated.body["propagated_objects"] == [dependent_id]
    bound_edges_after_propagate = edge_repository.edges_to(
        preserve_attribution.id, "subject_to_obligation"
    )
    assert {e.source_id for e in bound_edges_after_propagate} == {
        transferred_object_id,
        dependent_id,
    }

    resolved = obligation_service.resolve(
        preserve_attribution.id,
        "Attribution notice added to the published dataset.",
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )
    assert resolved.revision == 3
    assert resolved.body["status"] == "resolved"

    # The full revision history is append-only and addressable, and the
    # binding edges recorded before resolve() remain queryable afterward.
    revisions = object_repository.list_revisions(preserve_attribution.id)
    assert [r.body["status"] for r in revisions] == ["open", "open", "resolved"]
    assert edge_repository.edges_to(preserve_attribution.id, "subject_to_obligation") == (
        bound_edges_after_propagate
    )

    events = [e.event for e in event_log.read_all() if e.event.object_id == preserve_attribution.id]
    assert [e.event_type for e in events] == [
        "obligation.created",
        "obligation.propagated",
        "obligation.resolved",
    ]

    # Still resolvable through the transfer's own repository handle too —
    # confirms both services share the identical underlying objects/edges
    # tables (no migration, no separate storage).
    assert transfer_object_repository.get_latest(contract.id).kind == "TransferContract"


def test_no_migration_needed_kind_and_edge_type_accepted_by_existing_schema(
    postgres_engine: Engine,
) -> None:
    """Direct raw-SQL confirmation, bypassing every repository/service: the
    generic ``objects``/``edges`` tables (unmodified by this task) already
    accept ``kind="Obligation"`` rows and ``edge_type="subject_to_obligation"``
    edges — task-packets/E6-T02.yaml forbidden_changes: "confirmed
    unnecessary ... subject_to_obligation is already a declared vocabulary
    member ... this task's edges are new rows under an existing constraint,
    not a schema change".
    """
    transfer_service, _transfer_object_repository = _transfer_service_for(postgres_engine)
    obligation_service, object_repository, _edge_repository, _event_log = _obligation_service_for(
        postgres_engine
    )
    practice, private_key, kid = _practice_and_key()
    # caveats=[] here (unlike the module's default fixture) so exactly one
    # Obligation materializes — this test only cares about a single object's
    # kind/edge_type, not the caveats-produce-a-second-Obligation behavior
    # already covered by test_materialize_propagate_resolve_persist_atomically.
    contract = _sign(_contract(sender_practice_id=practice.id, key_id=kid, caveats=[]), private_key)
    correlation_id = new_urn("research-run")

    transfer_service.create(
        contract, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    transfer_service.offer(
        contract.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    transfer_service.respond(
        contract.id,
        "accepted",
        practice,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )
    [obligation] = obligation_service.materialize_from_transfer(
        contract.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    with postgres_engine.connect() as conn:
        kind_row = conn.execute(
            sa.select(objects_table.c.kind).where(objects_table.c.id == obligation.id)
        ).one()
        assert kind_row.kind == "Obligation"

        edge_row = conn.execute(
            sa.select(edges_table.c.edge_type).where(edges_table.c.target_id == obligation.id)
        ).one()
        assert edge_row.edge_type == "subject_to_obligation"

        event_count = conn.execute(
            sa.select(sa.func.count())
            .select_from(domain_events_table)
            .where(domain_events_table.c.object_id == obligation.id)
        ).scalar_one()
        assert event_count == 1

    stored = object_repository.get_latest(obligation.id)
    assert Obligation.model_validate(stored.body).kind == "Obligation"


def test_materialize_is_at_most_once_per_transfer_against_real_postgres(
    postgres_engine: Engine,
) -> None:
    """A second materialize_from_transfer call for the same, real, persisted
    TransferContract raises ObligationsAlreadyMaterializedError and leaves
    exactly the first call's Obligation rows, subject_to_obligation edges,
    and obligation.created events in place — no silent duplicate Obligation
    set (reviewer-driven amendment to the original PR).
    """
    transfer_service, _transfer_object_repository = _transfer_service_for(postgres_engine)
    obligation_service, object_repository, edge_repository, event_log = _obligation_service_for(
        postgres_engine
    )
    practice, private_key, kid = _practice_and_key()
    contract = _sign(_contract(sender_practice_id=practice.id, key_id=kid), private_key)
    correlation_id = new_urn("research-run")

    transfer_service.create(
        contract, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    transfer_service.offer(
        contract.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    transfer_service.respond(
        contract.id,
        "accepted",
        practice,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    # This fixture's contract carries both an explicit preserve_attribution
    # stub AND a non-empty caveats field, so two Obligations materialize.
    first_call = obligation_service.materialize_from_transfer(
        contract.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    assert len(first_call) == 2
    first_call_ids = {obj.id for obj in first_call}

    def _count_domain_events(object_id: str) -> int:
        with postgres_engine.connect() as conn:
            return conn.execute(
                sa.select(sa.func.count())
                .select_from(domain_events_table)
                .where(domain_events_table.c.object_id == object_id)
            ).scalar_one()

    revisions_after_first = {obj.id: object_repository.list_revisions(obj.id) for obj in first_call}
    bound_edges_after_first = {
        obj.id: list(edge_repository.edges_to(obj.id, "subject_to_obligation"))
        for obj in first_call
    }
    event_counts_after_first = {obj.id: _count_domain_events(obj.id) for obj in first_call}

    with pytest.raises(ObligationsAlreadyMaterializedError) as excinfo:
        obligation_service.materialize_from_transfer(
            contract.id,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=correlation_id,
        )

    assert excinfo.value.transfer_id == contract.id
    assert set(excinfo.value.obligation_ids) == first_call_ids

    # Exactly the first call's rows remain: no new Obligation object, no new
    # subject_to_obligation edge, no new obligation.created event.
    for obj in first_call:
        assert object_repository.list_revisions(obj.id) == revisions_after_first[obj.id]
        assert (
            list(edge_repository.edges_to(obj.id, "subject_to_obligation"))
            == bound_edges_after_first[obj.id]
        )
        assert _count_domain_events(obj.id) == event_counts_after_first[obj.id]
