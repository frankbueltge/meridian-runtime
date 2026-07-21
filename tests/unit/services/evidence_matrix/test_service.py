"""Unit tests for ``mrr.services.evidence_matrix.service.EvidenceMatrixService``
(task-packets/K1-T03.yaml), run entirely DB-free against in-memory fakes of
``mrr.domain.repositories.ObjectRepository`` and the event-log read surface
the service depends on — mirrors
``tests/unit/services/method_profile/test_service.py``'s own fakes/fixture
shape exactly (the packet names that module as the template).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from mrr.contracts import EvidenceMatrix
from mrr.domain.exceptions import (
    EvidenceMatrixNotFoundError,
    InvalidTransitionError,
    ObjectNotFoundError,
    RevisionConflictError,
)
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.evidence_matrix.service import EvidenceMatrixService, RecordRevisionWithEvent

_POLICY_VERSION = "policy-2026-07-01"


class FakeObjectRepository:
    def __init__(self) -> None:
        self._revisions: dict[str, list[StoredObject]] = {}

    def insert_revision(
        self, obj: StoredObject, expected_current_revision: int | None
    ) -> StoredObject:
        current = self._revisions.get(obj.id, [])
        current_max = current[-1].revision if current else None
        if current_max != expected_current_revision:
            raise RevisionConflictError(obj.id, expected_current_revision, current_max)
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


class FakeEventLog:
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


def _fake_record(
    object_repository: FakeObjectRepository, event_log: FakeEventLog
) -> RecordRevisionWithEvent:
    def _record(
        obj: StoredObject, expected_current_revision: int | None, event: DomainEvent
    ) -> tuple[StoredObject, AppendedEvent]:
        stored = object_repository.insert_revision(obj, expected_current_revision)
        appended = event_log.append_for_test(event)
        return stored, appended

    return _record


def _service() -> tuple[EvidenceMatrixService, FakeObjectRepository, FakeEventLog]:
    object_repository = FakeObjectRepository()
    event_log = FakeEventLog()
    service = EvidenceMatrixService(
        object_repository, event_log, _fake_record(object_repository, event_log)
    )
    return service, object_repository, event_log


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
        "rows": [],
        "status": "draft",
    }
    data.update(overrides)
    return EvidenceMatrix.model_validate(data)


def _stored_object_from_matrix(matrix: EvidenceMatrix) -> StoredObject:
    body: dict[str, Any] = json.loads(matrix.model_dump_json(exclude_none=True))
    return StoredObject(
        id=matrix.id,
        api_version=matrix.api_version,
        kind=matrix.kind,
        practice_id=matrix.practice_id,
        revision=matrix.revision,
        created_at=matrix.created_at,
        created_by=matrix.created_by,
        content_hash=matrix.content_hash,
        supersedes=matrix.supersedes,
        labels=matrix.labels,
        body=body,
    )


def _seed(object_repository: FakeObjectRepository, matrix: EvidenceMatrix) -> StoredObject:
    return object_repository.insert_revision(
        _stored_object_from_matrix(matrix), expected_current_revision=None
    )


_ACTOR = new_urn("agent-role")


def test_create_persists_revision_1_and_created_event() -> None:
    service, object_repository, event_log = _service()
    matrix = _matrix()

    stored = service.create(
        matrix, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=new_urn("research-run")
    )

    assert stored.revision == 1
    assert object_repository.get_latest(matrix.id).body["status"] == "draft"
    assert event_log.appended[0].event.event_type == "evidence_matrix.created"


def test_create_rejects_non_draft_initial_status() -> None:
    service, _, _ = _service()
    matrix = _matrix(status="active")

    with pytest.raises(InvalidTransitionError):
        service.create(
            matrix,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )


def test_create_rejects_non_one_revision() -> None:
    service, _, _ = _service()
    matrix = _matrix(revision=2)

    with pytest.raises(ValueError, match="revision"):
        service.create(
            matrix,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )


def test_activate_transitions_draft_to_active_and_records_event() -> None:
    service, object_repository, event_log = _service()
    matrix = _matrix()
    _seed(object_repository, matrix)

    stored = service.activate(
        matrix.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=new_urn("research-run"),
    )

    assert stored.revision == 2
    assert stored.body["status"] == "active"
    assert event_log.appended[-1].event.event_type == "evidence_matrix.activated"
    assert event_log.appended[-1].event.causation_id is None  # no prior event for this fresh id


def test_freeze_requires_active_first() -> None:
    service, object_repository, _ = _service()
    matrix = _matrix()
    _seed(object_repository, matrix)

    with pytest.raises(InvalidTransitionError):
        service.freeze(
            matrix.id,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )


def test_full_path_draft_active_frozen() -> None:
    service, object_repository, event_log = _service()
    matrix = _matrix()
    _seed(object_repository, matrix)
    correlation_id = new_urn("research-run")

    service.activate(
        matrix.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    stored = service.freeze(
        matrix.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    assert stored.revision == 3
    assert stored.body["status"] == "frozen"
    assert [rev.body["status"] for rev in object_repository.list_revisions(matrix.id)] == [
        "draft",
        "active",
        "frozen",
    ]
    event_types = [appended.event.event_type for appended in event_log.appended]
    assert event_types == ["evidence_matrix.activated", "evidence_matrix.frozen"]


def test_transition_on_unknown_id_raises_evidence_matrix_not_found_error() -> None:
    service, _, _ = _service()
    with pytest.raises(EvidenceMatrixNotFoundError):
        service.activate(
            new_urn("evidence-matrix"),
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )


def test_matrix_with_zero_rows_is_a_legitimate_record() -> None:
    """MRR-MTH-011: a matrix that legitimately found ZERO usable sources
    must remain constructible and addressable, not rejected as degenerate.
    """
    service, object_repository, _ = _service()
    matrix = _matrix(rows=[])

    stored = service.create(
        matrix, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=new_urn("research-run")
    )

    assert stored.body["rows"] == []
