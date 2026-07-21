"""Integration tests for
``mrr.services.method_protocol.service.MethodProtocolService``
(task-packets/K1-T04.yaml), run against a real PostgreSQL via the
``postgres_engine`` fixture. Scoped to the three transitions this service
implements — ``create``/``submit_for_review``/``lock``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from mrr.contracts import MethodProtocol
from mrr.domain.exceptions import InvalidTransitionError, MethodProtocolNotFoundError
from mrr.domain.identity import new_urn
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.persistence.tables import domain_events_table, objects_table
from mrr.services.method_protocol.service import MethodProtocolService, bind_unit_of_work
from sqlalchemy import Engine

_POLICY_VERSION = "policy-2026-07-21"


def _protocol(*, id: str | None = None, **overrides: Any) -> MethodProtocol:
    data: dict[str, Any] = {
        "id": id or new_urn("method-protocol"),
        "api_version": "mrr/v1alpha1",
        "kind": "MethodProtocol",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": datetime.now(UTC),
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "profile_id": new_urn("method-profile"),
        "extraction_fields": ["claim_relevant_finding"],
        "inclusion_criteria": ["catalogued"],
        "exclusion_criteria": ["derived"],
        "sensitivity_variations": [],
        "planned_analyses": ["some-analysis"],
        "kill_conditions": ["fewer than 3 -> stop_insufficient_evidence"],
        "locked_at": None,
        "locked_by": None,
        "amendment": None,
        "status": "draft",
    }
    data.update(overrides)
    return MethodProtocol.model_validate(data)


def _service_for(
    engine: Engine,
) -> tuple[MethodProtocolService, PostgresObjectRepository, PostgresEventLog]:
    object_repository = PostgresObjectRepository(engine)
    event_log = PostgresEventLog(engine)
    record = bind_unit_of_work(engine, object_repository, event_log)
    service = MethodProtocolService(object_repository, event_log, record)
    return service, object_repository, event_log


def test_create_persists_one_revision_and_one_event_atomically(postgres_engine: Engine) -> None:
    service, _, _ = _service_for(postgres_engine)
    protocol = _protocol()
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")

    stored = service.create(
        protocol, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    assert stored.revision == 1
    with postgres_engine.connect() as conn:
        object_rows = conn.execute(
            sa.select(objects_table).where(objects_table.c.id == protocol.id)
        ).fetchall()
        event_rows = conn.execute(
            sa.select(domain_events_table).where(domain_events_table.c.object_id == protocol.id)
        ).fetchall()
    assert len(object_rows) == 1
    assert len(event_rows) == 1
    assert event_rows[0].event_type == "method_protocol.created"


def test_submit_for_review_then_lock_persists_revisions_2_and_3(postgres_engine: Engine) -> None:
    service, object_repository, _ = _service_for(postgres_engine)
    protocol = _protocol()
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")
    service.create(
        protocol, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    service.submit_for_review(
        protocol.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    stored = service.lock(
        protocol.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    assert stored.revision == 3
    assert stored.body["status"] == "locked"
    assert stored.body["locked_at"] is not None
    assert stored.body["locked_by"] == actor
    revisions = object_repository.list_revisions(protocol.id)
    assert [rev.body["status"] for rev in revisions] == ["draft", "reviewed", "locked"]

    # The lock hash IS this revision's own content_hash (MRR-MTH-007),
    # resolvable from the persisted row.
    latest = object_repository.get_latest(protocol.id)
    assert latest.content_hash == latest.body["content_hash"]
    MethodProtocol.model_validate(latest.body)


def test_illegal_transition_persists_nothing(postgres_engine: Engine) -> None:
    service, _, _ = _service_for(postgres_engine)
    protocol = _protocol()
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")
    service.create(
        protocol, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    with pytest.raises(InvalidTransitionError):
        service.lock(
            protocol.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
        )

    with postgres_engine.connect() as conn:
        object_rows = conn.execute(
            sa.select(objects_table).where(objects_table.c.id == protocol.id)
        ).fetchall()
    assert len(object_rows) == 1


def test_submit_for_review_on_unknown_id_raises_method_protocol_not_found_error(
    postgres_engine: Engine,
) -> None:
    service, _, _ = _service_for(postgres_engine)
    with pytest.raises(MethodProtocolNotFoundError):
        service.submit_for_review(
            new_urn("method-protocol"),
            actor=new_urn("agent-role"),
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )
