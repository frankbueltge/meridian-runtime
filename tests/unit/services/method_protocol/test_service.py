"""Unit tests for ``mrr.services.method_protocol.service.MethodProtocolService``
(task-packets/K1-T04.yaml), run entirely DB-free against in-memory fakes.
Scoped to exactly the three transitions this service implements — ``create``/
``submit_for_review``/``lock`` — mirroring
``tests/unit/services/evidence_matrix/test_service.py``'s own fakes/fixture
shape.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from mrr.contracts import MethodProtocol
from mrr.domain.exceptions import (
    InvalidTransitionError,
    MethodProtocolNotFoundError,
    ObjectNotFoundError,
    RevisionConflictError,
)
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.method_protocol.service import MethodProtocolService, RecordRevisionWithEvent

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


def _service() -> tuple[MethodProtocolService, FakeObjectRepository, FakeEventLog]:
    object_repository = FakeObjectRepository()
    event_log = FakeEventLog()
    service = MethodProtocolService(
        object_repository, event_log, _fake_record(object_repository, event_log)
    )
    return service, object_repository, event_log


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


def _stored_object_from(protocol: MethodProtocol) -> StoredObject:
    body: dict[str, Any] = json.loads(protocol.model_dump_json(exclude_none=True))
    return StoredObject(
        id=protocol.id,
        api_version=protocol.api_version,
        kind=protocol.kind,
        practice_id=protocol.practice_id,
        revision=protocol.revision,
        created_at=protocol.created_at,
        created_by=protocol.created_by,
        content_hash=protocol.content_hash,
        supersedes=protocol.supersedes,
        labels=protocol.labels,
        body=body,
    )


def _seed(object_repository: FakeObjectRepository, protocol: MethodProtocol) -> StoredObject:
    return object_repository.insert_revision(
        _stored_object_from(protocol), expected_current_revision=None
    )


def test_create_persists_revision_1_and_created_event() -> None:
    service, object_repository, event_log = _service()
    protocol = _protocol()

    stored = service.create(
        protocol,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=new_urn("research-run"),
    )

    assert stored.revision == 1
    assert object_repository.get_latest(protocol.id).body["status"] == "draft"
    assert event_log.appended[0].event.event_type == "method_protocol.created"


def test_create_rejects_non_draft_initial_status() -> None:
    service, _, _ = _service()
    protocol = _protocol(
        status="reviewed", locked_at=None, locked_by=None
    )  # reviewed still requires locked_at/locked_by null, which this fixture satisfies

    with pytest.raises(InvalidTransitionError):
        service.create(
            protocol,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )


def test_create_rejects_non_one_revision() -> None:
    service, _, _ = _service()
    protocol = _protocol(revision=2)

    with pytest.raises(ValueError, match="revision"):
        service.create(
            protocol,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )


def test_submit_for_review_transitions_draft_to_reviewed() -> None:
    service, object_repository, event_log = _service()
    protocol = _protocol()
    _seed(object_repository, protocol)

    stored = service.submit_for_review(
        protocol.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=new_urn("research-run"),
    )

    assert stored.revision == 2
    assert stored.body["status"] == "reviewed"
    assert stored.body.get("locked_at") is None
    assert stored.body.get("locked_by") is None
    assert event_log.appended[-1].event.event_type == "method_protocol.reviewed"
    # Round-trips through the real MethodProtocol contract.
    MethodProtocol.model_validate(stored.body)


def test_lock_requires_reviewed_first() -> None:
    service, object_repository, _ = _service()
    protocol = _protocol()
    _seed(object_repository, protocol)

    with pytest.raises(InvalidTransitionError) as excinfo:
        service.lock(
            protocol.id,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )
    assert excinfo.value.from_state == "draft"
    assert excinfo.value.to_state == "locked"


def test_lock_sets_locked_at_and_locked_by_and_a_new_content_hash() -> None:
    service, object_repository, event_log = _service()
    protocol = _protocol()
    _seed(object_repository, protocol)
    correlation_id = new_urn("research-run")

    service.submit_for_review(
        protocol.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    stored = service.lock(
        protocol.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    assert stored.revision == 3
    assert stored.body["status"] == "locked"
    assert stored.body["locked_at"] is not None
    assert stored.body["locked_by"] == _ACTOR
    # The lock hash IS this revision's own content_hash (MRR-MTH-007) — a new
    # one, distinct from the draft/reviewed revisions' hashes.
    assert stored.content_hash == stored.body["content_hash"]
    assert stored.content_hash != protocol.content_hash

    # Round-trips through the real MethodProtocol contract's own
    # _lock_fields_match_status validator.
    validated = MethodProtocol.model_validate(stored.body)
    assert validated.locked_at is not None
    assert validated.locked_by == _ACTOR

    event_types = [appended.event.event_type for appended in event_log.appended]
    assert event_types == ["method_protocol.reviewed", "method_protocol.locked"]


def test_full_path_draft_reviewed_locked() -> None:
    service, object_repository, _ = _service()
    protocol = _protocol()
    _seed(object_repository, protocol)
    correlation_id = new_urn("research-run")

    service.submit_for_review(
        protocol.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    service.lock(
        protocol.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    statuses = [rev.body["status"] for rev in object_repository.list_revisions(protocol.id)]
    assert statuses == ["draft", "reviewed", "locked"]


def test_transition_on_unknown_id_raises_method_protocol_not_found_error() -> None:
    service, _, _ = _service()
    protocol_id = new_urn("method-protocol")

    with pytest.raises(MethodProtocolNotFoundError) as excinfo:
        service.submit_for_review(
            protocol_id,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )
    assert excinfo.value.method_protocol_id == protocol_id
