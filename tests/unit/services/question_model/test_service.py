"""Unit tests for ``mrr.services.question_model.service.QuestionModelService``
(task-packets/K1-T04.yaml), run entirely DB-free against in-memory fakes —
mirrors ``tests/unit/services/method_profile/test_service.py``'s own
fakes/fixture shape exactly (the packet names that module as its own
template).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from mrr.contracts import QuestionModel
from mrr.domain.exceptions import (
    InvalidTransitionError,
    ObjectNotFoundError,
    QuestionModelNotFoundError,
    RevisionConflictError,
)
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.question_model.service import QuestionModelService, RecordRevisionWithEvent

_POLICY_VERSION = "policy-2026-07-21"
_ACTOR = new_urn("agent-role")


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


def _service() -> tuple[QuestionModelService, FakeObjectRepository, FakeEventLog]:
    object_repository = FakeObjectRepository()
    event_log = FakeEventLog()
    service = QuestionModelService(
        object_repository, event_log, _fake_record(object_repository, event_log)
    )
    return service, object_repository, event_log


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
        "load_bearing_terms": ["model-collapse mechanism", "instantiate", "reference"],
        "status": "draft",
    }
    data.update(overrides)
    return QuestionModel.model_validate(data)


def _stored_object_from(question_model: QuestionModel) -> StoredObject:
    body: dict[str, Any] = json.loads(question_model.model_dump_json(exclude_none=True))
    return StoredObject(
        id=question_model.id,
        api_version=question_model.api_version,
        kind=question_model.kind,
        practice_id=question_model.practice_id,
        revision=question_model.revision,
        created_at=question_model.created_at,
        created_by=question_model.created_by,
        content_hash=question_model.content_hash,
        supersedes=question_model.supersedes,
        labels=question_model.labels,
        body=body,
    )


def _seed(object_repository: FakeObjectRepository, question_model: QuestionModel) -> StoredObject:
    return object_repository.insert_revision(
        _stored_object_from(question_model), expected_current_revision=None
    )


def test_propose_persists_revision_1_and_proposed_event() -> None:
    service, object_repository, event_log = _service()
    question_model = _question_model()
    correlation_id = new_urn("research-run")

    stored = service.propose(
        question_model, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    assert stored.revision == 1
    assert stored.body["status"] == "draft"
    assert object_repository.get_latest(question_model.id).id == question_model.id
    events = event_log.read_all()
    assert len(events) == 1
    assert events[0].event.event_type == "question_model.proposed"
    assert events[0].event.causation_id is None
    assert events[0].event.correlation_id == correlation_id


def test_propose_rejects_non_draft_initial_status() -> None:
    service, object_repository, event_log = _service()
    question_model = _question_model(status="accepted")

    with pytest.raises(InvalidTransitionError) as excinfo:
        service.propose(
            question_model,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )

    assert excinfo.value.to_state == "accepted"
    assert object_repository.list_revisions(question_model.id) == []
    assert event_log.read_all() == []


def test_propose_rejects_wrong_initial_revision_number() -> None:
    service, _, _ = _service()
    question_model = _question_model(revision=2)

    with pytest.raises(ValueError, match="revision must be 1"):
        service.propose(
            question_model,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )


def test_accept_succeeds_and_records_accepted_event() -> None:
    service, object_repository, event_log = _service()
    question_model = _question_model()
    _seed(object_repository, question_model)

    stored = service.accept(
        question_model.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=new_urn("research-run"),
    )

    assert stored.revision == 2
    assert stored.body["status"] == "accepted"
    events = event_log.read_all()
    assert len(events) == 1
    assert events[0].event.event_type == "question_model.accepted"
    assert events[0].event.object_revision == 2


def test_illegal_transition_raises_and_persists_nothing() -> None:
    """QUESTION_MODEL_LIFECYCLE has no draft -> superseded edge (skips accepted)."""
    service, object_repository, event_log = _service()
    question_model = _question_model()
    _seed(object_repository, question_model)

    with pytest.raises(InvalidTransitionError) as excinfo:
        service._transition(
            question_model.id,
            "superseded",
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
            event_type="question_model.superseded",
        )

    assert excinfo.value.machine == "QuestionModel"
    assert excinfo.value.from_state == "draft"
    assert excinfo.value.to_state == "superseded"
    assert [rev.revision for rev in object_repository.list_revisions(question_model.id)] == [1]
    assert event_log.read_all() == []


def test_accept_missing_question_model_raises_not_found_error() -> None:
    service, _, _ = _service()
    question_model_id = new_urn("question-model")

    with pytest.raises(QuestionModelNotFoundError) as excinfo:
        service.accept(
            question_model_id,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )
    assert excinfo.value.question_model_id == question_model_id


def test_transition_event_carries_complete_provenance() -> None:
    service, _, event_log = _service()
    question_model = _question_model()
    correlation_id = new_urn("research-run")

    service.propose(
        question_model, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    stored = service.accept(
        question_model.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    events = [appended.event for appended in event_log.read_all()]
    assert len(events) == 2
    proposed_event, accepted_event = events

    assert proposed_event.causation_id is None
    assert accepted_event.causation_id == proposed_event.id
    for event in (proposed_event, accepted_event):
        assert event.actor == _ACTOR
        assert event.policy_version == _POLICY_VERSION
        assert event.correlation_id == correlation_id
        assert event.object_id == question_model.id
        assert event.occurred_at.tzinfo is not None
    assert stored.revision == 2
    assert stored.body["status"] == "accepted"
