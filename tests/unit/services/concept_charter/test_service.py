"""Unit tests for ``mrr.services.concept_charter.service.ConceptCharterService``
(task-packets/K1-T04.yaml), run entirely DB-free against in-memory fakes —
mirrors ``tests/unit/services/question_model/test_service.py``'s own
fakes/fixture shape exactly.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from mrr.contracts import ConceptCharter
from mrr.domain.exceptions import (
    ConceptCharterNotFoundError,
    InvalidTransitionError,
    ObjectNotFoundError,
    RevisionConflictError,
)
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.concept_charter.service import ConceptCharterService, RecordRevisionWithEvent

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


def _service() -> tuple[ConceptCharterService, FakeObjectRepository, FakeEventLog]:
    object_repository = FakeObjectRepository()
    event_log = FakeEventLog()
    service = ConceptCharterService(
        object_repository, event_log, _fake_record(object_repository, event_log)
    )
    return service, object_repository, event_log


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


def _stored_object_from(concept_charter: ConceptCharter) -> StoredObject:
    body: dict[str, Any] = json.loads(concept_charter.model_dump_json(exclude_none=True))
    return StoredObject(
        id=concept_charter.id,
        api_version=concept_charter.api_version,
        kind=concept_charter.kind,
        practice_id=concept_charter.practice_id,
        revision=concept_charter.revision,
        created_at=concept_charter.created_at,
        created_by=concept_charter.created_by,
        content_hash=concept_charter.content_hash,
        supersedes=concept_charter.supersedes,
        labels=concept_charter.labels,
        body=body,
    )


def _seed(object_repository: FakeObjectRepository, concept_charter: ConceptCharter) -> StoredObject:
    return object_repository.insert_revision(
        _stored_object_from(concept_charter), expected_current_revision=None
    )


def test_propose_persists_revision_1_and_proposed_event() -> None:
    service, object_repository, event_log = _service()
    concept_charter = _concept_charter()
    correlation_id = new_urn("research-run")

    stored = service.propose(
        concept_charter, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    assert stored.revision == 1
    assert stored.body["status"] == "draft"
    assert object_repository.get_latest(concept_charter.id).id == concept_charter.id
    events = event_log.read_all()
    assert len(events) == 1
    assert events[0].event.event_type == "concept_charter.proposed"


def test_propose_rejects_non_draft_initial_status() -> None:
    service, object_repository, event_log = _service()
    concept_charter = _concept_charter(status="accepted")

    with pytest.raises(InvalidTransitionError):
        service.propose(
            concept_charter,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )
    assert object_repository.list_revisions(concept_charter.id) == []
    assert event_log.read_all() == []


def test_accept_succeeds_and_records_accepted_event() -> None:
    service, object_repository, event_log = _service()
    concept_charter = _concept_charter()
    _seed(object_repository, concept_charter)

    stored = service.accept(
        concept_charter.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=new_urn("research-run"),
    )

    assert stored.revision == 2
    assert stored.body["status"] == "accepted"
    events = event_log.read_all()
    assert len(events) == 1
    assert events[0].event.event_type == "concept_charter.accepted"


def test_illegal_transition_raises_and_persists_nothing() -> None:
    service, object_repository, event_log = _service()
    concept_charter = _concept_charter()
    _seed(object_repository, concept_charter)

    with pytest.raises(InvalidTransitionError) as excinfo:
        service._transition(
            concept_charter.id,
            "superseded",
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
            event_type="concept_charter.superseded",
        )
    assert excinfo.value.from_state == "draft"
    assert excinfo.value.to_state == "superseded"
    assert [rev.revision for rev in object_repository.list_revisions(concept_charter.id)] == [1]
    assert event_log.read_all() == []


def test_accept_missing_concept_charter_raises_not_found_error() -> None:
    service, _, _ = _service()
    concept_charter_id = new_urn("concept-charter")

    with pytest.raises(ConceptCharterNotFoundError) as excinfo:
        service.accept(
            concept_charter_id,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )
    assert excinfo.value.concept_charter_id == concept_charter_id


def test_duplicate_entry_id_rejected_by_contract_before_service_ever_sees_it() -> None:
    """ConceptCharter's own model_validator (K1-T01) rejects a duplicate
    entry_id at construction time — this service never has to guard against
    it itself.
    """
    with pytest.raises(ValueError, match="duplicate ConceptCharter entry_id"):
        _concept_charter(
            entries=[
                {"entry_id": "dup", "term": "a", "definition": "d1", "scope_note": None},
                {"entry_id": "dup", "term": "b", "definition": "d2", "scope_note": None},
            ]
        )
