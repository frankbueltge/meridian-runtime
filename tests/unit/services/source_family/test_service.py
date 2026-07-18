"""Unit tests for ``mrr.services.source_family.service.SourceFamilyService``
(task-packets/E3-T03.yaml) — entirely DB-free, no PostgreSQL, no
``sqlalchemy.Engine``: like ``SourceRecordService``/``EvidenceAnchorService``
(E3-T01), this service never reads prior state, so the fake unit-of-work
below is the same shape as
``tests/unit/services/evidence/test_service.py``'s own ``_FakeUnitOfWork``
(no revision bookkeeping needed — every call is a brand-new object at
revision 1).

Acceptance-test mapping (task-packets/E3-T03.yaml):

- "creating a family persists one revision + one event atomically"
  (unit-level; the packet's own duplicate against real PostgreSQL is the
  integration tier) ->
  ``test_create_persists_revision_one_and_one_event``.
- "persisting a family writes exactly one domain event with full NFR-001
  provenance, atomically with the revision" ->
  ``test_event_provenance_is_complete_and_causation_is_root``.
- "No mutate beyond append-only revisions" ->
  ``test_service_exposes_no_mutate_method``.
- "an unknown relationship_type value fails ... at the model level" ->
  ``test_unknown_relationship_type_rejected_at_model_level``.
- "a family referencing member source urns leaves those SourceRecords
  untouched (additive — no deletion)" ->
  ``test_create_never_touches_any_repository_for_member_sources``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from mrr.contracts import SourceFamily
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.source_family.service import SourceFamilyService
from pydantic import ValidationError

_POLICY_VERSION = "policy-2026-07-01"


# ---------------------------------------------------------------------------
# Fake unit-of-work: SourceFamilyService never reads prior state (every call
# writes a brand-new object at revision 1), so this fake only needs to
# record what it was called with.
# ---------------------------------------------------------------------------


class _FakeUnitOfWork:
    def __init__(self) -> None:
        self.stored: list[StoredObject] = []
        self.events: list[DomainEvent] = []

    def __call__(
        self,
        obj: StoredObject,
        expected_current_revision: int | None,
        event: DomainEvent,
    ) -> tuple[StoredObject, AppendedEvent]:
        assert expected_current_revision is None, (
            "SourceFamilyService always writes a brand-new object at revision 1"
        )
        self.stored.append(obj)
        self.events.append(event)
        appended = AppendedEvent(
            event=event,
            sequence=len(self.events),
            content_hash=f"sha256:{'c' * 64}",
            prev_hash=None,
        )
        return obj, appended


def _service() -> tuple[SourceFamilyService, _FakeUnitOfWork]:
    uow = _FakeUnitOfWork()
    return SourceFamilyService(uow), uow


def _source_family(*, revision: int = 1, **overrides: Any) -> SourceFamily:
    now = datetime.now(UTC)
    data: dict[str, Any] = {
        "id": new_urn("source-family"),
        "api_version": "mrr/v1alpha1",
        "kind": "SourceFamily",
        "practice_id": new_urn("practice"),
        "revision": revision,
        "created_at": now,
        "created_by": new_urn("agent"),
        "content_hash": "sha256:" + "7" * 64,
        "origin_ref": "Benchmark fixture percentage tables dataset, 2026 edition",
        "member_source_ids": [new_urn("source-record"), new_urn("source-record")],
        "relationship_type": "shared_dataset",
        "confidence": 0.87,
        "rationale": (
            "Both records reproduce the same percentage table verbatim, including an "
            "identical rounding artifact, consistent with one shared upstream dataset."
        ),
        "detecting_method": "Automated text-similarity comparison (token_sort_ratio >= 0.92)",
        "reviewer_id": new_urn("person"),
    }
    data.update(overrides)
    return SourceFamily.model_validate(data)


def test_create_persists_revision_one_and_one_event() -> None:
    service, uow = _service()
    source_family = _source_family()

    stored = service.create(
        source_family,
        actor=new_urn("agent"),
        policy_version=_POLICY_VERSION,
        correlation_id=new_urn("research-run"),
    )

    assert stored.revision == 1
    assert stored.id == source_family.id
    assert stored.body["relationship_type"] == "shared_dataset"
    assert stored.body["member_source_ids"] == source_family.member_source_ids
    assert len(uow.stored) == 1
    assert len(uow.events) == 1


def test_create_rejects_non_one_revision() -> None:
    service, _ = _service()
    source_family = _source_family(revision=2)

    with pytest.raises(ValueError, match="revision"):
        service.create(
            source_family,
            actor=new_urn("agent"),
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )


def test_event_provenance_is_complete_and_causation_is_root() -> None:
    service, uow = _service()
    source_family = _source_family()
    actor = new_urn("agent")
    correlation_id = new_urn("research-run")

    stored = service.create(
        source_family, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    assert len(uow.events) == 1
    event = uow.events[0]
    assert event.event_type == "source_family.created"
    assert event.actor == actor
    assert event.policy_version == _POLICY_VERSION
    assert event.correlation_id == correlation_id
    assert event.causation_id is None  # a brand-new family identity has no prior event
    assert event.object_id == stored.id
    assert event.object_revision == 1
    assert event.occurred_at.tzinfo is not None
    assert event.payload["relationship_type"] == "shared_dataset"
    assert event.payload["member_source_count"] == 2


def test_service_exposes_no_mutate_method() -> None:
    """SourceFamily representation is additive-only (task-packets/
    E3-T03.yaml invariant) — this class's only public callable is
    ``create``.
    """
    public_methods = {
        name
        for name in dir(SourceFamilyService)
        if not name.startswith("_") and callable(getattr(SourceFamilyService, name))
    }
    assert public_methods == {"create"}


def test_create_never_touches_any_repository_for_member_sources() -> None:
    """The fake unit-of-work is the ONLY collaborator ``SourceFamilyService``
    is constructed with — there is no object-repository dependency it could
    use to read or write a member ``SourceRecord`` even if it wanted to.
    ``member_source_ids`` is carried purely as data inside the persisted
    ``SourceFamily`` body (additive representation, task-packets/E3-T03.yaml
    invariant).
    """
    service, uow = _service()
    member_ids = [new_urn("source-record"), new_urn("source-record"), new_urn("source-record")]
    source_family = _source_family(member_source_ids=member_ids)

    stored = service.create(
        source_family,
        actor=new_urn("agent"),
        policy_version=_POLICY_VERSION,
        correlation_id=new_urn("research-run"),
    )

    assert stored.body["member_source_ids"] == member_ids
    # Exactly one object was written — the family itself, never a member source.
    assert len(uow.stored) == 1
    assert uow.stored[0].id == source_family.id


@pytest.mark.parametrize(
    "relationship_type",
    [
        "copy",
        "syndication",
        "shared_dataset",
        "shared_press_release",
        "direct_derivation",
        "uncertain",
    ],
)
def test_create_accepts_every_relationship_type(relationship_type: str) -> None:
    service, uow = _service()
    source_family = _source_family(relationship_type=relationship_type)

    stored = service.create(
        source_family,
        actor=new_urn("agent"),
        policy_version=_POLICY_VERSION,
        correlation_id=new_urn("research-run"),
    )

    assert stored.body["relationship_type"] == relationship_type
    assert len(uow.stored) == 1


def test_unknown_relationship_type_rejected_at_model_level() -> None:
    with pytest.raises(ValidationError, match="relationship_type"):
        _source_family(relationship_type="coincidental_similarity")


def test_empty_member_source_ids_rejected_at_model_level() -> None:
    with pytest.raises(ValidationError, match="member_source_ids"):
        _source_family(member_source_ids=[])
