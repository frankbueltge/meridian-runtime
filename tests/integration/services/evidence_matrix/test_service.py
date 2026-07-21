"""Integration tests for
``mrr.services.evidence_matrix.service.EvidenceMatrixService``
(task-packets/K1-T03.yaml), run against a real PostgreSQL via the
``postgres_engine`` fixture in tests/integration/conftest.py — mirrors
``tests/integration/services/method_profile/test_service.py``'s own wiring
shape exactly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from mrr.contracts import EvidenceMatrix
from mrr.domain.exceptions import EvidenceMatrixNotFoundError, InvalidTransitionError
from mrr.domain.identity import new_urn
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.persistence.tables import domain_events_table, objects_table
from mrr.services.evidence_matrix.service import EvidenceMatrixService, bind_unit_of_work
from sqlalchemy import Engine

_POLICY_VERSION = "policy-2026-07-01"


def _matrix(*, id: str | None = None, **overrides: Any) -> EvidenceMatrix:
    data: dict[str, Any] = {
        "id": id or new_urn("evidence-matrix"),
        "api_version": "mrr/v1alpha1",
        "kind": "EvidenceMatrix",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": datetime.now(UTC),
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "protocol_id": new_urn("method-protocol"),
        "question_id": new_urn("question-model"),
        "rows": [
            {
                "row_id": "row-1",
                "source_record_id": new_urn("source-record"),
                "verification_status": "verified",
                "claim_relevant_finding": "A finding.",
                "extraction": {},
            }
        ],
        "status": "draft",
    }
    data.update(overrides)
    return EvidenceMatrix.model_validate(data)


def _service_for(
    engine: Engine,
) -> tuple[EvidenceMatrixService, PostgresObjectRepository, PostgresEventLog]:
    object_repository = PostgresObjectRepository(engine)
    event_log = PostgresEventLog(engine)
    record = bind_unit_of_work(engine, object_repository, event_log)
    service = EvidenceMatrixService(object_repository, event_log, record)
    return service, object_repository, event_log


def test_create_persists_one_revision_and_one_event_atomically(postgres_engine: Engine) -> None:
    service, _, _ = _service_for(postgres_engine)
    matrix = _matrix()
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")

    stored = service.create(
        matrix, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    assert stored.revision == 1
    with postgres_engine.connect() as conn:
        object_rows = conn.execute(
            sa.select(objects_table).where(objects_table.c.id == matrix.id)
        ).fetchall()
        event_rows = conn.execute(
            sa.select(domain_events_table).where(domain_events_table.c.object_id == matrix.id)
        ).fetchall()

    assert len(object_rows) == 1
    assert len(event_rows) == 1
    assert event_rows[0].event_type == "evidence_matrix.created"


def test_activate_then_freeze_persists_revisions_2_and_3(postgres_engine: Engine) -> None:
    service, object_repository, _ = _service_for(postgres_engine)
    matrix = _matrix()
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")
    service.create(
        matrix, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    service.activate(
        matrix.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    stored = service.freeze(
        matrix.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    assert stored.revision == 3
    assert stored.body["status"] == "frozen"
    revisions = object_repository.list_revisions(matrix.id)
    assert [rev.body["status"] for rev in revisions] == ["draft", "active", "frozen"]


def test_illegal_transition_persists_nothing(postgres_engine: Engine) -> None:
    service, _, _ = _service_for(postgres_engine)
    matrix = _matrix()
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")
    service.create(
        matrix, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    with pytest.raises(InvalidTransitionError):
        service.freeze(
            matrix.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
        )

    with postgres_engine.connect() as conn:
        object_rows = conn.execute(
            sa.select(objects_table).where(objects_table.c.id == matrix.id)
        ).fetchall()
    assert len(object_rows) == 1


def test_activate_on_unknown_id_raises_evidence_matrix_not_found_error(
    postgres_engine: Engine,
) -> None:
    service, _, _ = _service_for(postgres_engine)
    with pytest.raises(EvidenceMatrixNotFoundError):
        service.activate(
            new_urn("evidence-matrix"),
            actor=new_urn("agent-role"),
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )
