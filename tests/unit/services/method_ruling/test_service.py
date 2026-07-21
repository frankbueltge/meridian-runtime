"""Unit tests for ``mrr.services.method_ruling.service.MethodRulingService``
(task-packets/K1-T03.yaml), run entirely DB-free against in-memory fakes —
mirrors ``tests/unit/services/evidence_matrix/test_service.py``'s own
fakes/fixture shape exactly.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from mrr.contracts import MethodRuling
from mrr.domain.exceptions import (
    InvalidTransitionError,
    MethodRulingNotFoundError,
    ObjectNotFoundError,
    RevisionConflictError,
)
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.method_ruling.service import MethodRulingService, RecordRevisionWithEvent

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


def _service() -> tuple[MethodRulingService, FakeObjectRepository, FakeEventLog]:
    object_repository = FakeObjectRepository()
    event_log = FakeEventLog()
    service = MethodRulingService(
        object_repository, event_log, _fake_record(object_repository, event_log)
    )
    return service, object_repository, event_log


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


def _stored_object_from_ruling(ruling: MethodRuling) -> StoredObject:
    body: dict[str, Any] = json.loads(ruling.model_dump_json(exclude_none=True))
    return StoredObject(
        id=ruling.id,
        api_version=ruling.api_version,
        kind=ruling.kind,
        practice_id=ruling.practice_id,
        revision=ruling.revision,
        created_at=ruling.created_at,
        created_by=ruling.created_by,
        content_hash=ruling.content_hash,
        supersedes=ruling.supersedes,
        labels=ruling.labels,
        body=body,
    )


def _seed(object_repository: FakeObjectRepository, ruling: MethodRuling) -> StoredObject:
    return object_repository.insert_revision(
        _stored_object_from_ruling(ruling), expected_current_revision=None
    )


_ACTOR = new_urn("agent-role")


def test_create_persists_revision_1_and_created_event() -> None:
    service, object_repository, event_log = _service()
    ruling = _ruling()

    stored = service.create(
        ruling, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=new_urn("research-run")
    )

    assert stored.revision == 1
    assert object_repository.get_latest(ruling.id).body["status"] == "pending"
    assert event_log.appended[0].event.event_type == "method_ruling.created"
    assert event_log.appended[0].event.payload["ruled_ceiling"] == "associational_unadjusted"


def test_create_rejects_non_pending_initial_status() -> None:
    service, _, _ = _service()
    ruling = _ruling(status="issued")

    with pytest.raises(InvalidTransitionError):
        service.create(
            ruling,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )


def test_issue_transitions_pending_to_issued() -> None:
    service, object_repository, event_log = _service()
    ruling = _ruling()
    _seed(object_repository, ruling)

    stored = service.issue(
        ruling.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=new_urn("research-run"),
    )

    assert stored.revision == 2
    assert stored.body["status"] == "issued"
    assert event_log.appended[-1].event.event_type == "method_ruling.issued"


def test_issue_on_unknown_id_raises_method_ruling_not_found_error() -> None:
    service, _, _ = _service()
    with pytest.raises(MethodRulingNotFoundError):
        service.issue(
            new_urn("method-ruling"),
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )


def test_non_applicability_conditions_required_above_descriptive_at_model_level() -> None:
    """MTH-017 is enforced at the contract level: this profile's fixed
    associational_unadjusted ceiling always sits above "descriptive" in
    CLAIM_CEILING_ORDER, so an empty non_applicability_conditions list must
    fail construction before this service ever sees the object.
    """
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="non_applicability_conditions"):
        _ruling(non_applicability_conditions=[])
