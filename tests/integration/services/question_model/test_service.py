"""Integration tests for
``mrr.services.question_model.service.QuestionModelService``
(task-packets/K1-T04.yaml), run against a real PostgreSQL via the
``postgres_engine`` fixture — mirrors
``tests/integration/services/method_profile/test_service.py``'s own wiring
shape exactly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from mrr.contracts import QuestionModel
from mrr.domain.exceptions import InvalidTransitionError, QuestionModelNotFoundError
from mrr.domain.identity import new_urn
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.persistence.tables import domain_events_table, objects_table
from mrr.services.question_model.service import QuestionModelService, bind_unit_of_work
from sqlalchemy import Engine

_POLICY_VERSION = "policy-2026-07-21"


def _question_model(*, id: str | None = None, **overrides: Any) -> QuestionModel:
    data: dict[str, Any] = {
        "id": id or new_urn("question-model"),
        "api_version": "mrr/v1alpha1",
        "kind": "QuestionModel",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": datetime.now(UTC),
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "raw_question": "Do works instantiate the mechanism or just reference it?",
        "claim_type_sought": "interpretive",
        "scope": {"population": "test works", "conditions": []},
        "load_bearing_terms": ["model-collapse mechanism"],
        "status": "draft",
    }
    data.update(overrides)
    return QuestionModel.model_validate(data)


def _service_for(
    engine: Engine,
) -> tuple[QuestionModelService, PostgresObjectRepository, PostgresEventLog]:
    object_repository = PostgresObjectRepository(engine)
    event_log = PostgresEventLog(engine)
    record = bind_unit_of_work(engine, object_repository, event_log)
    service = QuestionModelService(object_repository, event_log, record)
    return service, object_repository, event_log


def test_propose_persists_one_revision_and_one_event_atomically(postgres_engine: Engine) -> None:
    service, _, _ = _service_for(postgres_engine)
    question_model = _question_model()
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")

    stored = service.propose(
        question_model, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    assert stored.revision == 1
    with postgres_engine.connect() as conn:
        object_rows = conn.execute(
            sa.select(objects_table).where(objects_table.c.id == question_model.id)
        ).fetchall()
        event_rows = conn.execute(
            sa.select(domain_events_table).where(
                domain_events_table.c.object_id == question_model.id
            )
        ).fetchall()
    assert len(object_rows) == 1
    assert len(event_rows) == 1
    assert event_rows[0].event_type == "question_model.proposed"


def test_accept_persists_revision_2(postgres_engine: Engine) -> None:
    service, object_repository, _ = _service_for(postgres_engine)
    question_model = _question_model()
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")
    service.propose(
        question_model, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    stored = service.accept(
        question_model.id,
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    assert stored.revision == 2
    assert stored.body["status"] == "accepted"
    revisions = object_repository.list_revisions(question_model.id)
    assert [rev.body["status"] for rev in revisions] == ["draft", "accepted"]


def test_illegal_transition_persists_nothing(postgres_engine: Engine) -> None:
    service, _, _ = _service_for(postgres_engine)
    question_model = _question_model()
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")
    service.propose(
        question_model, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    service.accept(
        question_model.id,
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    with pytest.raises(InvalidTransitionError):
        service.accept(
            question_model.id,
            actor=actor,
            policy_version=_POLICY_VERSION,
            correlation_id=correlation_id,
        )

    with postgres_engine.connect() as conn:
        object_rows = conn.execute(
            sa.select(objects_table).where(objects_table.c.id == question_model.id)
        ).fetchall()
    assert len(object_rows) == 2


def test_accept_on_unknown_id_raises_question_model_not_found_error(
    postgres_engine: Engine,
) -> None:
    service, _, _ = _service_for(postgres_engine)
    with pytest.raises(QuestionModelNotFoundError):
        service.accept(
            new_urn("question-model"),
            actor=new_urn("agent-role"),
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )
