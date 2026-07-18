"""Unit tests for ``mrr.services.evidence.service.SourceRecordService`` and
``EvidenceAnchorService`` (task-packets/E3-T01.yaml) — entirely DB-free, no
PostgreSQL, no ``sqlalchemy.Engine``: neither service reads prior state (see
the module's own docstring for why), so the fake unit-of-work below is the
same shape as
``tests/unit/services/node_runtime/test_run_manifest.py``'s own
``_FakeUnitOfWork`` (no revision bookkeeping needed — every call is a
brand-new object at revision 1).

Acceptance-test mapping (task-packets/E3-T01.yaml):

- "creating a source record and an anchor each persists one revision + one
  event atomically" (unit-level; the packet's own duplicate against real
  PostgreSQL is the integration tier) ->
  ``test_source_record_create_persists_revision_one_and_one_event``,
  ``test_evidence_anchor_create_persists_revision_one_and_one_event``.
- "persisting a record or anchor writes exactly one domain event with full
  NFR-001 provenance, atomically with the revision" ->
  ``test_source_record_event_provenance_is_complete_and_causation_is_root``,
  ``test_evidence_anchor_event_provenance_is_complete_and_causation_is_root``.
- "No update/mutate beyond append-only revisions" ->
  ``test_source_record_service_exposes_no_mutate_method``,
  ``test_evidence_anchor_service_exposes_no_mutate_method``.
- "the exact-or-explicit rule is enforced at the model level" ->
  ``test_evidence_anchor_model_rejects_neither_resolution_nor_reason``,
  ``test_evidence_anchor_model_accepts_computational_resolution``,
  ``test_evidence_anchor_model_accepts_explicit_unavailable_reason``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from mrr.contracts import EvidenceAnchor, SourceRecord
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.evidence.service import EvidenceAnchorService, SourceRecordService
from pydantic import ValidationError

_POLICY_VERSION = "policy-2026-07-01"


# ---------------------------------------------------------------------------
# Fake unit-of-work: neither service ever reads prior state (every call
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
            "SourceRecordService/EvidenceAnchorService always write a brand-new object "
            "at revision 1"
        )
        self.stored.append(obj)
        self.events.append(event)
        appended = AppendedEvent(
            event=event,
            sequence=len(self.events),
            content_hash=f"sha256:{'b' * 64}",
            prev_hash=None,
        )
        return obj, appended


def _source_record_service() -> tuple[SourceRecordService, _FakeUnitOfWork]:
    uow = _FakeUnitOfWork()
    return SourceRecordService(uow), uow


def _evidence_anchor_service() -> tuple[EvidenceAnchorService, _FakeUnitOfWork]:
    uow = _FakeUnitOfWork()
    return EvidenceAnchorService(uow), uow


# ---------------------------------------------------------------------------
# Fixture factories.
# ---------------------------------------------------------------------------


def _source_record(*, revision: int = 1, **overrides: Any) -> SourceRecord:
    now = datetime.now(UTC)
    data: dict[str, Any] = {
        "id": new_urn("source-record"),
        "api_version": "mrr/v1alpha1",
        "kind": "SourceRecord",
        "practice_id": new_urn("practice"),
        "revision": revision,
        "created_at": now,
        "created_by": new_urn("agent"),
        "content_hash": "sha256:" + "a" * 64,
        "identifiers": {"doi": "10.1234/example.42"},
        "title": "Benchmark fixture percentage tables",
        "creators": ["Example Research Collective"],
        "publication_date": "2026-01-15",
        "version": "1.0",
        "retrieval_timestamp": now,
        "retrieval_method": "HTTP GET, publisher API",
        "snapshot_artifact_hash": "sha256:" + "6" * 64,
        "source_type": "journal-article",
        "primary_secondary_derived": "primary",
        "source_family_id": None,
        "derivation_evidence": None,
        "accessibility": {"access_type": "open_access"},
        "licensing": {"license_id": "CC-BY-4.0"},
    }
    data.update(overrides)
    return SourceRecord.model_validate(data)


def _evidence_anchor(*, revision: int = 1, **overrides: Any) -> EvidenceAnchor:
    now = datetime.now(UTC)
    data: dict[str, Any] = {
        "id": new_urn("evidence-anchor"),
        "api_version": "mrr/v1alpha1",
        "kind": "EvidenceAnchor",
        "practice_id": new_urn("practice"),
        "revision": revision,
        "created_at": now,
        "created_by": new_urn("agent"),
        "content_hash": "sha256:" + "8" * 64,
        "relation": "supports",
        "anchor_kind": "text",
        "extraction_method": "manual quotation with paragraph locator",
        "extractor_id": new_urn("agent"),
        "anchor_validation_status": "validated",
        "anchor_unavailable_reason": None,
        "source_record_id": new_urn("source-record"),
        "snapshot_hash": "sha256:" + "6" * 64,
        "locator": {"page": 4, "section": "Results", "paragraph": 2},
        "quoted_fragment_hash": "sha256:" + "5" * 64,
        "run_id": None,
        "output_artifact": None,
        "selector": None,
        "transformation_chain": [],
        "recomputation_status": None,
    }
    data.update(overrides)
    return EvidenceAnchor.model_validate(data)


# ---------------------------------------------------------------------------
# SourceRecordService
# ---------------------------------------------------------------------------


def test_source_record_create_persists_revision_one_and_one_event() -> None:
    service, uow = _source_record_service()
    source_record = _source_record()

    stored = service.create(
        source_record,
        actor=new_urn("agent"),
        policy_version=_POLICY_VERSION,
        correlation_id=new_urn("research-run"),
    )

    assert stored.revision == 1
    assert stored.id == source_record.id
    assert stored.body["source_type"] == "journal-article"
    assert stored.body["primary_secondary_derived"] == "primary"
    assert len(uow.stored) == 1
    assert len(uow.events) == 1


def test_source_record_create_rejects_non_one_revision() -> None:
    service, _ = _source_record_service()
    source_record = _source_record(revision=2)

    with pytest.raises(ValueError, match="revision"):
        service.create(
            source_record,
            actor=new_urn("agent"),
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )


def test_source_record_event_provenance_is_complete_and_causation_is_root() -> None:
    service, uow = _source_record_service()
    source_record = _source_record()
    actor = new_urn("agent")
    correlation_id = new_urn("research-run")

    stored = service.create(
        source_record, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    assert len(uow.events) == 1
    event = uow.events[0]
    assert event.event_type == "source_record.created"
    assert event.actor == actor
    assert event.policy_version == _POLICY_VERSION
    assert event.correlation_id == correlation_id
    assert event.causation_id is None  # a brand-new source identity has no prior event
    assert event.object_id == stored.id
    assert event.object_revision == 1
    assert event.occurred_at.tzinfo is not None
    assert event.payload["source_type"] == "journal-article"


def test_source_record_service_exposes_no_mutate_method() -> None:
    """SourceRecord is append-only (docs/spec/02_DOMAIN_MODEL.md section
    2.8) — this class's only public callable is ``create``.
    """
    public_methods = {
        name
        for name in dir(SourceRecordService)
        if not name.startswith("_") and callable(getattr(SourceRecordService, name))
    }
    assert public_methods == {"create"}


# ---------------------------------------------------------------------------
# EvidenceAnchorService
# ---------------------------------------------------------------------------


def test_evidence_anchor_create_persists_revision_one_and_one_event() -> None:
    service, uow = _evidence_anchor_service()
    anchor = _evidence_anchor()

    stored = service.create(
        anchor,
        actor=new_urn("agent"),
        policy_version=_POLICY_VERSION,
        correlation_id=new_urn("research-run"),
    )

    assert stored.revision == 1
    assert stored.id == anchor.id
    assert stored.body["relation"] == "supports"
    assert stored.body["anchor_kind"] == "text"
    assert len(uow.stored) == 1
    assert len(uow.events) == 1


def test_evidence_anchor_create_rejects_non_one_revision() -> None:
    service, _ = _evidence_anchor_service()
    anchor = _evidence_anchor(revision=2)

    with pytest.raises(ValueError, match="revision"):
        service.create(
            anchor,
            actor=new_urn("agent"),
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )


def test_evidence_anchor_event_provenance_is_complete_and_causation_is_root() -> None:
    service, uow = _evidence_anchor_service()
    anchor = _evidence_anchor()
    actor = new_urn("agent")
    correlation_id = new_urn("research-run")

    stored = service.create(
        anchor, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    assert len(uow.events) == 1
    event = uow.events[0]
    assert event.event_type == "evidence_anchor.created"
    assert event.actor == actor
    assert event.policy_version == _POLICY_VERSION
    assert event.correlation_id == correlation_id
    assert event.causation_id is None
    assert event.object_id == stored.id
    assert event.object_revision == 1
    assert event.occurred_at.tzinfo is not None
    assert event.payload["relation"] == "supports"
    assert event.payload["anchor_kind"] == "text"


def test_evidence_anchor_service_exposes_no_mutate_method() -> None:
    """EvidenceAnchor is append-only (docs/spec/02_DOMAIN_MODEL.md section
    2.9) — this class's only public callable is ``create``.
    """
    public_methods = {
        name
        for name in dir(EvidenceAnchorService)
        if not name.startswith("_") and callable(getattr(EvidenceAnchorService, name))
    }
    assert public_methods == {"create"}


def test_computational_anchor_creates_successfully() -> None:
    """A computational anchor (run_id + recomputation_status, no text
    fields) is just as valid an input to ``EvidenceAnchorService.create`` as
    a text anchor.
    """
    service, uow = _evidence_anchor_service()
    anchor = _evidence_anchor(
        anchor_kind="computational",
        source_record_id=None,
        snapshot_hash=None,
        locator=None,
        quoted_fragment_hash=None,
        run_id=new_urn("run"),
        recomputation_status="reproduced",
    )

    stored = service.create(
        anchor,
        actor=new_urn("executor"),
        policy_version=_POLICY_VERSION,
        correlation_id=new_urn("research-run"),
    )

    assert stored.body["anchor_kind"] == "computational"
    assert len(uow.stored) == 1


# ---------------------------------------------------------------------------
# The exact-resolution-or-explicit-reason invariant, enforced at the model
# level (mrr.contracts.evidence_anchor.EvidenceAnchor), not by the service.
# ---------------------------------------------------------------------------


def test_evidence_anchor_model_rejects_neither_resolution_nor_reason() -> None:
    with pytest.raises(ValidationError, match="anchor_unavailable_reason"):
        _evidence_anchor(
            snapshot_hash=None,
            quoted_fragment_hash=None,
            anchor_unavailable_reason=None,
        )


def test_evidence_anchor_model_rejects_computational_with_run_id_but_no_recomputation_status() -> (
    None
):
    """A bare run_id with no recorded recomputation outcome is NOT yet an
    exact resolution (task-packets/E3-T01.yaml: "computational -> run_id +
    recomputation reference").
    """
    with pytest.raises(ValidationError, match="anchor_unavailable_reason"):
        _evidence_anchor(
            anchor_kind="computational",
            source_record_id=None,
            snapshot_hash=None,
            locator=None,
            quoted_fragment_hash=None,
            run_id=new_urn("run"),
            recomputation_status=None,
            anchor_unavailable_reason=None,
        )


def test_evidence_anchor_model_accepts_computational_resolution() -> None:
    anchor = _evidence_anchor(
        anchor_kind="computational",
        source_record_id=None,
        snapshot_hash=None,
        locator=None,
        quoted_fragment_hash=None,
        run_id=new_urn("run"),
        recomputation_status="reproduced",
    )
    assert anchor.anchor_kind == "computational"


def test_evidence_anchor_model_accepts_explicit_unavailable_reason() -> None:
    anchor = _evidence_anchor(
        snapshot_hash=None,
        quoted_fragment_hash=None,
        anchor_unavailable_reason="Source expired before a snapshot could be captured.",
    )
    assert anchor.anchor_unavailable_reason is not None
