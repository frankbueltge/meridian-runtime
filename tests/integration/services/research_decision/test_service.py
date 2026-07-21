"""Integration tests for
``mrr.services.research_decision.service.ResearchDecisionService``
(task-packets/K1-T03.yaml), run against a real PostgreSQL via the
``postgres_engine`` fixture in tests/integration/conftest.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from mrr.contracts import ResearchDecision
from mrr.domain.identity import new_urn
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.persistence.tables import domain_events_table, objects_table
from mrr.services.research_decision.service import ResearchDecisionService, bind_unit_of_work
from sqlalchemy import Engine

_POLICY_VERSION = "policy-2026-07-01"


def _decision(*, id: str | None = None, **overrides: Any) -> ResearchDecision:
    data: dict[str, Any] = {
        "id": id or new_urn("research-decision"),
        "api_version": "mrr/v1alpha1",
        "kind": "ResearchDecision",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": datetime.now(UTC),
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "decision_type": "stop_insufficient_evidence",
        "protocol_id": new_urn("method-protocol"),
        "applies_to_analysis": "instantiation-vs-reference-classification",
        "rationale": "2 included source(s), below the declared minimum of 5",
        "status": "issued",
    }
    data.update(overrides)
    return ResearchDecision.model_validate(data)


def test_create_persists_one_revision_and_one_event_atomically(postgres_engine: Engine) -> None:
    object_repository = PostgresObjectRepository(postgres_engine)
    event_log = PostgresEventLog(postgres_engine)
    record = bind_unit_of_work(postgres_engine, object_repository, event_log)
    service = ResearchDecisionService(record)
    decision = _decision()
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")

    stored = service.create(
        decision, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    assert stored.revision == 1
    with postgres_engine.connect() as conn:
        object_rows = conn.execute(
            sa.select(objects_table).where(objects_table.c.id == decision.id)
        ).fetchall()
        event_rows = conn.execute(
            sa.select(domain_events_table).where(domain_events_table.c.object_id == decision.id)
        ).fetchall()

    assert len(object_rows) == 1
    assert len(event_rows) == 1
    assert event_rows[0].event_type == "research_decision.created"
    assert event_rows[0].actor == actor
    assert event_rows[0].correlation_id == correlation_id
    assert event_rows[0].causation_id is None
