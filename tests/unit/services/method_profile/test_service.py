"""Unit tests for ``mrr.services.method_profile.service.MethodProfileService``
(task-packets/K0-T01.yaml), run entirely DB-free against in-memory fakes of
``mrr.domain.repositories.ObjectRepository`` and the event-log read surface
the service depends on — no PostgreSQL, no ``sqlalchemy.Engine``. Mirrors
``tests/unit/services/research_score/test_service.py``'s own fakes/fixture
shape exactly (the packet names that module as the pattern to reuse).

Acceptance-test mapping (task-packets/K0-T01.yaml):

- "creating a MethodProfile (draft) persists it as revision 1 plus a
  method_profile.proposed event" -> ``test_propose_persists_revision_1_and_proposed_event``.
- "draft -> accepted succeeds via MethodProfileService and records a
  method_profile.accepted event, atomically with the new revision
  (MRR-MTH-019)" -> ``test_accept_succeeds_and_records_accepted_event``.
- "accepted -> superseded succeeds and records a method_profile.superseded
  event" -> ``test_supersede_succeeds_and_records_superseded_event``.
- "an illegal transition (e.g. attempting superseded -> accepted, or
  creating a fresh object directly as accepted/superseded) raises
  InvalidTransitionError and persists nothing" ->
  ``test_illegal_transition_raises_and_persists_nothing``,
  ``test_propose_rejects_non_draft_initial_status``.
- "find_accepted_by_capability returns only currently-accepted profiles
  declaring the queried capability name, excluding a profile whose current
  status has since moved to superseded" (framed as an integration test in
  the packet; also covered here DB-free, cheaply, mirroring
  ``ResearchScoreService.revise``'s own "also covered at the unit tier"
  precedent) -> ``test_find_accepted_by_capability_*``.
- event provenance completeness (MRR-NFR-001) ->
  ``test_transition_event_carries_complete_provenance``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from mrr.contracts import MethodProfile
from mrr.domain.exceptions import (
    InvalidTransitionError,
    MethodProfileNotFoundError,
    ObjectNotFoundError,
    RevisionConflictError,
)
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.method_profile.service import MethodProfileService, RecordRevisionWithEvent

# ---------------------------------------------------------------------------
# In-memory fakes (ObjectRepository protocol conformance + a minimal event
# journal), and a fake "unit of work" combining them — identical shape to
# tests/unit/services/research_score/test_service.py's own fakes.
# ---------------------------------------------------------------------------


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
        expected_new_revision = (
            1 if expected_current_revision is None else expected_current_revision + 1
        )
        if obj.revision != expected_new_revision:
            raise ValueError(
                f"obj.revision ({obj.revision!r}) does not match the revision implied by "
                f"expected_current_revision ({expected_current_revision!r}): expected "
                f"{expected_new_revision!r}"
            )
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
        obj: StoredObject,
        expected_current_revision: int | None,
        event: DomainEvent,
    ) -> tuple[StoredObject, AppendedEvent]:
        stored = object_repository.insert_revision(obj, expected_current_revision)
        appended = event_log.append_for_test(event)
        return stored, appended

    return _record


def _service() -> tuple[MethodProfileService, FakeObjectRepository, FakeEventLog]:
    object_repository = FakeObjectRepository()
    event_log = FakeEventLog()
    service = MethodProfileService(
        object_repository, event_log, _fake_record(object_repository, event_log)
    )
    return service, object_repository, event_log


# ---------------------------------------------------------------------------
# MethodProfile fixture factory and the matching StoredObject converter.
# ---------------------------------------------------------------------------


def _profile(*, id: str | None = None, **overrides: Any) -> MethodProfile:
    data: dict[str, Any] = {
        "id": id or new_urn("method-profile"),
        "api_version": "mrr/v1alpha1",
        "kind": "MethodProfile",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": datetime.now(UTC),
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "profile_key": "systematic_evidence_synthesis",
        "version": "1.0.0",
        "claim_types": ["observational", "interpretive"],
        "max_claim_ceiling": "associational_unadjusted",
        "protocol_form": "synthesis_protocol",
        "executor_task_family": ["mrr.method.systematic_evidence_synthesis/1"],
        "executor_steps": [
            {"name": "snapshot_loading", "kind": "deterministic"},
            {"name": "extraction", "kind": "model_assisted"},
        ],
        "inappropriate_uses": ["causal claims beyond associational_unadjusted"],
        "status": "draft",
    }
    data.update(overrides)
    return MethodProfile.model_validate(data)


def _stored_object_from_profile(profile: MethodProfile) -> StoredObject:
    body: dict[str, Any] = json.loads(profile.model_dump_json(exclude_none=True))
    return StoredObject(
        id=profile.id,
        api_version=profile.api_version,
        kind=profile.kind,
        practice_id=profile.practice_id,
        revision=profile.revision,
        created_at=profile.created_at,
        created_by=profile.created_by,
        content_hash=profile.content_hash,
        supersedes=profile.supersedes,
        labels=profile.labels,
        body=body,
    )


def _seed(object_repository: FakeObjectRepository, profile: MethodProfile) -> StoredObject:
    return object_repository.insert_revision(
        _stored_object_from_profile(profile), expected_current_revision=None
    )


_ACTOR = new_urn("agent-role")
_POLICY_VERSION = "policy-2026-07-01"


def _correlation_id() -> str:
    return new_urn("research-run")


# ---------------------------------------------------------------------------
# propose(): creation.
# ---------------------------------------------------------------------------


def test_propose_persists_revision_1_and_proposed_event() -> None:
    service, object_repository, event_log = _service()
    profile = _profile(status="draft")
    correlation_id = _correlation_id()

    stored = service.propose(
        profile, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    assert stored.revision == 1
    assert stored.body["status"] == "draft"
    assert object_repository.get_latest(profile.id).id == profile.id

    events = event_log.read_all()
    assert len(events) == 1
    assert events[0].event.event_type == "method_profile.proposed"
    assert events[0].event.causation_id is None
    assert events[0].event.correlation_id == correlation_id


def test_propose_rejects_non_draft_initial_status() -> None:
    service, object_repository, event_log = _service()
    profile = _profile(status="accepted")

    with pytest.raises(InvalidTransitionError) as excinfo:
        service.propose(
            profile, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
        )

    assert excinfo.value.to_state == "accepted"
    assert object_repository.list_revisions(profile.id) == []
    assert event_log.read_all() == []


def test_propose_rejects_wrong_initial_revision_number() -> None:
    service, _, _ = _service()
    profile = _profile(status="draft", revision=2)

    with pytest.raises(ValueError, match="revision must be 1"):
        service.propose(
            profile, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
        )


# ---------------------------------------------------------------------------
# Lifecycle transitions.
# ---------------------------------------------------------------------------


def test_accept_succeeds_and_records_accepted_event() -> None:
    service, object_repository, event_log = _service()
    profile = _profile(status="draft")
    _seed(object_repository, profile)

    stored = service.accept(
        profile.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    assert stored.revision == 2
    assert stored.body["status"] == "accepted"
    events = event_log.read_all()
    assert len(events) == 1
    assert events[0].event.event_type == "method_profile.accepted"
    assert events[0].event.object_revision == 2


def test_supersede_succeeds_and_records_superseded_event() -> None:
    service, object_repository, event_log = _service()
    profile = _profile(status="draft")
    _seed(object_repository, profile)
    service.accept(
        profile.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    stored = service.supersede(
        profile.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    assert stored.revision == 3
    assert stored.body["status"] == "superseded"
    events = [e for e in event_log.read_all() if e.event.event_type == "method_profile.superseded"]
    assert len(events) == 1


def test_illegal_transition_raises_and_persists_nothing() -> None:
    service, object_repository, event_log = _service()
    profile = _profile(status="draft")
    _seed(object_repository, profile)

    # draft -> superseded is not a drawn METHOD_PROFILE_LIFECYCLE edge (skips
    # the required draft -> accepted step).
    with pytest.raises(InvalidTransitionError) as excinfo:
        service.supersede(
            profile.id,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )

    assert excinfo.value.machine == "MethodProfile"
    assert excinfo.value.from_state == "draft"
    assert excinfo.value.to_state == "superseded"
    assert [rev.revision for rev in object_repository.list_revisions(profile.id)] == [1]
    assert event_log.read_all() == []


def test_superseded_to_accepted_raises_and_persists_nothing() -> None:
    service, object_repository, event_log = _service()
    profile = _profile(status="draft")
    _seed(object_repository, profile)
    service.accept(
        profile.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    service.supersede(
        profile.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    with pytest.raises(InvalidTransitionError) as excinfo:
        service.accept(
            profile.id,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )

    assert excinfo.value.from_state == "superseded"
    assert excinfo.value.to_state == "accepted"
    assert [rev.revision for rev in object_repository.list_revisions(profile.id)] == [1, 2, 3]


def test_accept_missing_profile_raises_method_profile_not_found() -> None:
    service, _, _ = _service()
    profile_id = new_urn("method-profile")

    with pytest.raises(MethodProfileNotFoundError) as excinfo:
        service.accept(
            profile_id,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )
    assert excinfo.value.profile_id == profile_id


# ---------------------------------------------------------------------------
# Event provenance completeness (MRR-NFR-001) and the causation chain.
# ---------------------------------------------------------------------------


def test_transition_event_carries_complete_provenance() -> None:
    service, _, event_log = _service()
    profile = _profile(status="draft")
    correlation_id = _correlation_id()

    service.propose(
        profile, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    stored = service.accept(
        profile.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
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
        assert event.object_id == profile.id
        assert event.occurred_at.tzinfo is not None

    assert proposed_event.object_revision == 1
    assert accepted_event.object_revision == 2
    assert stored.revision == 2
    assert stored.body["status"] == "accepted"


# ---------------------------------------------------------------------------
# find_accepted_by_capability.
# ---------------------------------------------------------------------------


def test_find_accepted_by_capability_returns_accepted_profile_declaring_it() -> None:
    service, object_repository, _ = _service()
    profile = _profile(status="draft", executor_task_family=["mrr.method.foo/1"])
    _seed(object_repository, profile)
    service.accept(
        profile.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    assert service.find_accepted_by_capability("mrr.method.foo/1") == [profile.id]


def test_find_accepted_by_capability_excludes_a_profile_not_declaring_it() -> None:
    service, object_repository, _ = _service()
    profile = _profile(status="draft", executor_task_family=["mrr.method.foo/1"])
    _seed(object_repository, profile)
    service.accept(
        profile.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    assert service.find_accepted_by_capability("mrr.method.bar/1") == []


def test_find_accepted_by_capability_excludes_a_since_superseded_profile() -> None:
    service, object_repository, _ = _service()
    profile = _profile(status="draft", executor_task_family=["mrr.method.foo/1"])
    _seed(object_repository, profile)
    service.accept(
        profile.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    assert service.find_accepted_by_capability("mrr.method.foo/1") == [profile.id]

    service.supersede(
        profile.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    assert service.find_accepted_by_capability("mrr.method.foo/1") == []


def test_find_accepted_by_capability_excludes_a_profile_never_accepted() -> None:
    service, object_repository, _ = _service()
    profile = _profile(status="draft", executor_task_family=["mrr.method.foo/1"])
    _seed(object_repository, profile)

    assert service.find_accepted_by_capability("mrr.method.foo/1") == []
