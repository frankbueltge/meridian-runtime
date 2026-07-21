"""Integration tests for
``mrr.services.concept_charter.service.ConceptCharterService``
(task-packets/K1-T04.yaml), run against a real PostgreSQL via the
``postgres_engine`` fixture.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from mrr.contracts import ConceptCharter
from mrr.domain.exceptions import ConceptCharterNotFoundError, InvalidTransitionError
from mrr.domain.identity import new_urn
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.persistence.tables import domain_events_table, objects_table
from mrr.services.concept_charter.service import ConceptCharterService, bind_unit_of_work
from sqlalchemy import Engine

_POLICY_VERSION = "policy-2026-07-21"


def _concept_charter(*, id: str | None = None, **overrides: Any) -> ConceptCharter:
    data: dict[str, Any] = {
        "id": id or new_urn("concept-charter"),
        "api_version": "mrr/v1alpha1",
        "kind": "ConceptCharter",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": datetime.now(UTC),
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "entries": [
            {
                "entry_id": "instantiate-vs-reference-v1",
                "term": "instantiate",
                "definition": "test definition",
                "scope_note": None,
            }
        ],
        "status": "draft",
    }
    data.update(overrides)
    return ConceptCharter.model_validate(data)


def _service_for(
    engine: Engine,
) -> tuple[ConceptCharterService, PostgresObjectRepository, PostgresEventLog]:
    object_repository = PostgresObjectRepository(engine)
    event_log = PostgresEventLog(engine)
    record = bind_unit_of_work(engine, object_repository, event_log)
    service = ConceptCharterService(object_repository, event_log, record)
    return service, object_repository, event_log


def test_propose_persists_one_revision_and_one_event_atomically(postgres_engine: Engine) -> None:
    service, _, _ = _service_for(postgres_engine)
    concept_charter = _concept_charter()
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")

    stored = service.propose(
        concept_charter, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    assert stored.revision == 1
    with postgres_engine.connect() as conn:
        object_rows = conn.execute(
            sa.select(objects_table).where(objects_table.c.id == concept_charter.id)
        ).fetchall()
        event_rows = conn.execute(
            sa.select(domain_events_table).where(
                domain_events_table.c.object_id == concept_charter.id
            )
        ).fetchall()
    assert len(object_rows) == 1
    assert len(event_rows) == 1
    assert event_rows[0].event_type == "concept_charter.proposed"


def test_accept_persists_revision_2(postgres_engine: Engine) -> None:
    service, object_repository, _ = _service_for(postgres_engine)
    concept_charter = _concept_charter()
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")
    service.propose(
        concept_charter, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    stored = service.accept(
        concept_charter.id,
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    assert stored.revision == 2
    assert stored.body["status"] == "accepted"
    revisions = object_repository.list_revisions(concept_charter.id)
    assert [rev.body["status"] for rev in revisions] == ["draft", "accepted"]


def test_illegal_transition_persists_nothing(postgres_engine: Engine) -> None:
    service, _, _ = _service_for(postgres_engine)
    concept_charter = _concept_charter()
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")
    service.propose(
        concept_charter, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    service.accept(
        concept_charter.id,
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    with pytest.raises(InvalidTransitionError):
        service.accept(
            concept_charter.id,
            actor=actor,
            policy_version=_POLICY_VERSION,
            correlation_id=correlation_id,
        )

    with postgres_engine.connect() as conn:
        object_rows = conn.execute(
            sa.select(objects_table).where(objects_table.c.id == concept_charter.id)
        ).fetchall()
    assert len(object_rows) == 2


def test_accept_on_unknown_id_raises_concept_charter_not_found_error(
    postgres_engine: Engine,
) -> None:
    service, _, _ = _service_for(postgres_engine)
    with pytest.raises(ConceptCharterNotFoundError):
        service.accept(
            new_urn("concept-charter"),
            actor=new_urn("agent-role"),
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )
