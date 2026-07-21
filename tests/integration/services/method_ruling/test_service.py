"""Integration tests for
``mrr.services.method_ruling.service.MethodRulingService``
(task-packets/K1-T03.yaml), run against a real PostgreSQL via the
``postgres_engine`` fixture in tests/integration/conftest.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from mrr.contracts import MethodRuling
from mrr.domain.exceptions import InvalidTransitionError, MethodRulingNotFoundError
from mrr.domain.identity import new_urn
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.persistence.tables import domain_events_table, objects_table
from mrr.services.method_ruling.service import MethodRulingService, bind_unit_of_work
from sqlalchemy import Engine

_POLICY_VERSION = "policy-2026-07-01"


def _ruling(*, id: str | None = None, **overrides: Any) -> MethodRuling:
    data: dict[str, Any] = {
        "id": id or new_urn("method-ruling"),
        "api_version": "mrr/v1alpha1",
        "kind": "MethodRuling",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": datetime.now(UTC),
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "ruled_ceiling": "associational_unadjusted",
        "scope_of_validity": {},
        "non_applicability_conditions": ["does not license causal language"],
        "ruling_basis": "deterministic_rule",
        "deterministic_rule_reference": "k1-t03.eligibility_and_ceiling_rules.v1",
        "issued_by": new_urn("agent-role"),
        "protocol_id": new_urn("method-protocol"),
        "applies_to_analysis": "instantiation-vs-reference-classification",
        "status": "pending",
    }
    data.update(overrides)
    return MethodRuling.model_validate(data)


def _service_for(
    engine: Engine,
) -> tuple[MethodRulingService, PostgresObjectRepository, PostgresEventLog]:
    object_repository = PostgresObjectRepository(engine)
    event_log = PostgresEventLog(engine)
    record = bind_unit_of_work(engine, object_repository, event_log)
    service = MethodRulingService(object_repository, event_log, record)
    return service, object_repository, event_log


def test_create_persists_one_revision_and_one_event_atomically(postgres_engine: Engine) -> None:
    service, _, _ = _service_for(postgres_engine)
    ruling = _ruling()
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")

    stored = service.create(
        ruling, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    assert stored.revision == 1
    with postgres_engine.connect() as conn:
        object_rows = conn.execute(
            sa.select(objects_table).where(objects_table.c.id == ruling.id)
        ).fetchall()
        event_rows = conn.execute(
            sa.select(domain_events_table).where(domain_events_table.c.object_id == ruling.id)
        ).fetchall()

    assert len(object_rows) == 1
    assert len(event_rows) == 1
    assert event_rows[0].event_type == "method_ruling.created"


def test_issue_persists_revision_2_and_issued_event(postgres_engine: Engine) -> None:
    service, object_repository, _ = _service_for(postgres_engine)
    ruling = _ruling()
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")
    service.create(
        ruling, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    stored = service.issue(
        ruling.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    assert stored.revision == 2
    assert stored.body["status"] == "issued"
    rev1 = object_repository.get_revision(ruling.id, 1)
    assert rev1.body["status"] == "pending"

    with postgres_engine.connect() as conn:
        event_rows = conn.execute(
            sa.select(domain_events_table)
            .where(domain_events_table.c.object_id == ruling.id)
            .order_by(domain_events_table.c.sequence)
        ).fetchall()
    assert event_rows[1].event_type == "method_ruling.issued"
    assert event_rows[1].causation_id == event_rows[0].id


def test_illegal_transition_persists_nothing(postgres_engine: Engine) -> None:
    service, _, _ = _service_for(postgres_engine)
    ruling = _ruling()
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")
    service.create(
        ruling, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    service.issue(
        ruling.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    # issued -> issued is not a drawn edge (no self-transitions).
    with pytest.raises(InvalidTransitionError):
        service.issue(
            ruling.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
        )

    with postgres_engine.connect() as conn:
        object_rows = conn.execute(
            sa.select(objects_table).where(objects_table.c.id == ruling.id)
        ).fetchall()
    assert len(object_rows) == 2  # create + one legal issue; the illegal second issue wrote nothing


def test_issue_on_unknown_id_raises_method_ruling_not_found_error(postgres_engine: Engine) -> None:
    service, _, _ = _service_for(postgres_engine)
    with pytest.raises(MethodRulingNotFoundError):
        service.issue(
            new_urn("method-ruling"),
            actor=new_urn("agent-role"),
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )
