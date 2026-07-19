"""Unit tests for ``mrr.services.projection.service.ProjectionService``
(task-packets/E3-T07.yaml), run entirely DB-free against in-memory fakes of
``mrr.domain.repositories.ObjectRepository``/``EdgeRepository`` and the
event-log read surface — no PostgreSQL, no ``sqlalchemy.Engine``. Real
``mrr.services.claim.service.ClaimService``,
``mrr.services.correction.service.CorrectionImpactService``, and
``mrr.services.evidence.service.SourceRecordService``/``EvidenceAnchorService``
are constructed over the SAME fakes (never reimplemented), matching
``tests/unit/services/correction/test_service.py``'s identical "construct
real upstream services over shared fakes" pattern.

Acceptance-test mapping (task-packets/E3-T07.yaml, unit tier):

- "claim-table row reflects latest status" ->
  ``test_claim_table_row_reflects_latest_status_evidence_and_verification``.
- "a claim flagged by an unresolved critical correction shows flagged" ->
  ``test_claim_flagged_by_an_unresolved_critical_correction``,
  ``test_downstream_dependent_flagged_via_impact_objects_after_propagation``.
- "once resolved, not flagged" ->
  ``test_claim_not_flagged_once_the_correction_is_resolved``.
- "the projection invents nothing (every value traces to a source id)" ->
  ``test_a_correction_naming_a_different_claim_does_not_leak_into_this_rows_ids``,
  ``test_provenance_map_excludes_a_dangling_edge_target``.
- "rebuild determinism" -> ``test_build_claim_table_is_deterministic_on_rebuild``,
  ``test_build_provenance_map_is_deterministic_on_rebuild``.
- provenance map traces edges AND EvidenceAnchor field references ->
  ``test_provenance_map_traces_edge_to_anchor_and_field_to_source_record``,
  ``test_provenance_map_traces_field_reference_to_a_run``.
- a missing claim -> ``test_provenance_map_on_missing_claim_raises``.
- cycle safety -> ``test_provenance_map_terminates_on_a_cyclic_graph``.
- writes nothing -> ``test_building_a_projection_writes_no_new_revision_or_edge``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from mrr.contracts import Claim, CorrectionEvent, EvidenceAnchor, SourceRecord
from mrr.domain.exceptions import (
    ClaimNotFoundError,
    ObjectNotFoundError,
    RevisionConflictError,
    UnknownEdgeTypeError,
)
from mrr.domain.identity import new_urn
from mrr.domain.repositories import EDGE_VOCABULARY, StoredObject, TypedEdge
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.claim.service import ClaimService
from mrr.services.correction.service import CorrectionImpactService
from mrr.services.evidence.service import EvidenceAnchorService, SourceRecordService
from mrr.services.projection.service import ProjectionService

_ACTOR = new_urn("agent-role")
_POLICY_VERSION = "policy-2026-07-01"


def _correlation_id() -> str:
    return new_urn("projection-run")


# ---------------------------------------------------------------------------
# In-memory fakes — identical in spirit to
# tests/unit/services/correction/test_service.py's own fakes.
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


class FakeEdgeRepository:
    def __init__(self) -> None:
        self._edges: list[TypedEdge] = []

    def add_edge(self, edge: TypedEdge) -> TypedEdge:
        if edge.edge_type not in EDGE_VOCABULARY:
            raise UnknownEdgeTypeError(edge.edge_type)
        self._edges.append(edge)
        return edge

    def edges_from(self, id: str, edge_type: str | None = None) -> list[TypedEdge]:
        return [
            e
            for e in self._edges
            if e.source_id == id and (edge_type is None or e.edge_type == edge_type)
        ]

    def edges_to(self, id: str, edge_type: str | None = None) -> list[TypedEdge]:
        return [
            e
            for e in self._edges
            if e.target_id == id and (edge_type is None or e.edge_type == edge_type)
        ]


class FakeEventLog:
    def __init__(self) -> None:
        self.appended: list[AppendedEvent] = []

    def append_for_test(self, event: DomainEvent) -> AppendedEvent:
        appended = AppendedEvent(
            event=event,
            sequence=len(self.appended) + 1,
            content_hash=f"sha256:{'c' * 64}",
            prev_hash=self.appended[-1].content_hash if self.appended else None,
        )
        self.appended.append(appended)
        return appended

    def read_all(self) -> list[AppendedEvent]:
        return list(self.appended)


def _fake_record(object_repository: FakeObjectRepository, event_log: FakeEventLog) -> Any:
    def _record(
        obj: StoredObject,
        expected_current_revision: int | None,
        event: DomainEvent,
    ) -> tuple[StoredObject, AppendedEvent]:
        stored = object_repository.insert_revision(obj, expected_current_revision)
        appended = event_log.append_for_test(event)
        return stored, appended

    return _record


def _fake_record_edge(edge_repository: FakeEdgeRepository, event_log: FakeEventLog) -> Any:
    def _record_edge(edge: TypedEdge, event: DomainEvent) -> tuple[TypedEdge, AppendedEvent]:
        stored_edge = edge_repository.add_edge(edge)
        appended = event_log.append_for_test(event)
        return stored_edge, appended

    return _record_edge


def _services() -> tuple[
    ProjectionService,
    ClaimService,
    CorrectionImpactService,
    SourceRecordService,
    EvidenceAnchorService,
    FakeObjectRepository,
    FakeEdgeRepository,
    FakeEventLog,
]:
    object_repository = FakeObjectRepository()
    event_log = FakeEventLog()
    edge_repository = FakeEdgeRepository()

    claim_service = ClaimService(
        object_repository,
        event_log,
        edge_repository,
        _fake_record(object_repository, event_log),
        _fake_record_edge(edge_repository, event_log),
    )
    correction_service = CorrectionImpactService(
        object_repository,
        edge_repository,
        claim_service,
        event_log,
        _fake_record(object_repository, event_log),
    )
    evidence_record = _fake_record(object_repository, event_log)
    source_record_service = SourceRecordService(evidence_record)
    evidence_anchor_service = EvidenceAnchorService(evidence_record)
    projection_service = ProjectionService(object_repository, edge_repository, event_log)

    return (
        projection_service,
        claim_service,
        correction_service,
        source_record_service,
        evidence_anchor_service,
        object_repository,
        edge_repository,
        event_log,
    )


# ---------------------------------------------------------------------------
# Fixture factories.
# ---------------------------------------------------------------------------


def _claim(*, id: str | None = None, **overrides: Any) -> Claim:
    data: dict[str, Any] = {
        "id": id or new_urn("claim"),
        "api_version": "mrr/v1alpha1",
        "kind": "Claim",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": datetime.now(UTC),
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "assertion": "Does this fixture assertion satisfy the schema's minimum length rule?",
        "claim_type": "statistical",
        "scope": {},
        "status": "draft",
        "evidence_relations": [],
        "counterevidence_relations": [],
        "dependencies": [],
        "source_family_ids": [],
        "uncertainty": [],
        "known_unknowns": [],
        "proposer_id": new_urn("agent-role"),
        "verification_ids": [],
        "correction_ids": [],
    }
    data.update(overrides)
    return Claim.model_validate(data)


def _correction(
    *, id: str | None = None, affected_object_ids: list[str], **overrides: Any
) -> CorrectionEvent:
    data: dict[str, Any] = {
        "id": id or new_urn("correction"),
        "api_version": "mrr/v1alpha1",
        "kind": "CorrectionEvent",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": datetime.now(UTC),
        "created_by": new_urn("person"),
        "content_hash": "sha256:" + "b" * 64,
        "affected_objects": [
            {"id": object_id, "content_hash": "sha256:" + "e" * 64}
            for object_id in affected_object_ids
        ],
        "correction_type": "source_invalidated",
        "severity": "critical",
        "reason": "Fixture reason: the cited dataset was later withdrawn by its publisher.",
        "evidence_refs": [new_urn("evidence-anchor")],
        "originator_id": new_urn("person"),
        "requested_action": "Mark dependent claims review_required.",
        "replacement_object_id": None,
        "status": "OPEN",
        "impact_objects": [],
    }
    data.update(overrides)
    return CorrectionEvent.model_validate(data)


def _source_record(*, id: str | None = None, **overrides: Any) -> SourceRecord:
    data: dict[str, Any] = {
        "id": id or new_urn("source-record"),
        "api_version": "mrr/v1alpha1",
        "kind": "SourceRecord",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": datetime.now(UTC),
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "d" * 64,
        "identifiers": {"doi": "10.1234/fixture"},
        "title": "Fixture source record title",
        "creators": ["Fixture Creator"],
        "retrieval_timestamp": datetime.now(UTC),
        "retrieval_method": "http-get",
        "source_type": "dataset",
        "primary_secondary_derived": "primary",
    }
    data.update(overrides)
    return SourceRecord.model_validate(data)


def _text_evidence_anchor(
    *, id: str | None = None, source_record_id: str, **overrides: Any
) -> EvidenceAnchor:
    data: dict[str, Any] = {
        "id": id or new_urn("evidence-anchor"),
        "api_version": "mrr/v1alpha1",
        "kind": "EvidenceAnchor",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": datetime.now(UTC),
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "f" * 64,
        "relation": "supports",
        "anchor_kind": "text",
        "extraction_method": "manual-quote",
        "extractor_id": new_urn("agent-role"),
        "anchor_validation_status": "validated",
        "source_record_id": source_record_id,
        "snapshot_hash": "sha256:" + "1" * 64,
        "transformation_chain": [],
    }
    data.update(overrides)
    return EvidenceAnchor.model_validate(data)


def _computational_evidence_anchor(
    *, id: str | None = None, run_id: str, **overrides: Any
) -> EvidenceAnchor:
    data: dict[str, Any] = {
        "id": id or new_urn("evidence-anchor"),
        "api_version": "mrr/v1alpha1",
        "kind": "EvidenceAnchor",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": datetime.now(UTC),
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "9" * 64,
        "relation": "supports",
        "anchor_kind": "computational",
        "extraction_method": "notebook-recompute",
        "extractor_id": new_urn("agent-role"),
        "anchor_validation_status": "validated",
        "run_id": run_id,
        "recomputation_status": "reproduced",
        "transformation_chain": ["select", "aggregate"],
    }
    data.update(overrides)
    return EvidenceAnchor.model_validate(data)


def _seed_bare_object(
    object_repository: FakeObjectRepository, *, id: str, kind: str
) -> StoredObject:
    """Seed a minimal StoredObject of an arbitrary ``kind`` directly — used
    for a ``RunManifest`` stand-in (a full, schema-valid RunManifest is not
    needed to exercise ProjectionService's own field-reference-hop logic,
    which only ever reads ``StoredObject.kind``/``.id``, never re-validates
    the body against schemas/run-manifest.schema.json).
    """
    obj = StoredObject(
        id=id,
        api_version="mrr/v1alpha1",
        kind=kind,
        practice_id=new_urn("practice"),
        revision=1,
        created_at=datetime.now(UTC),
        created_by=_ACTOR,
        content_hash="sha256:" + "7" * 64,
        supersedes=None,
        labels=None,
        body={"id": id, "kind": kind},
    )
    return object_repository.insert_revision(obj, expected_current_revision=None)


def _reseal_with_status(
    object_repository: FakeObjectRepository, stored: StoredObject, *, status: str
) -> StoredObject:
    """Force a new revision carrying ``status`` directly onto the fake
    repository — simulating a future resolution transition
    (``CorrectionEvent``'s own ``AWAITING_RESPONSES -> RESOLVED`` edge, E6
    territory, has no service implementing it yet) purely to exercise
    ProjectionService's OWN "not flagged once resolved" read behavior, which
    depends only on the latest revision's ``status`` field, not on how it
    got there.
    """
    new_body = dict(stored.body)
    new_body["status"] = status
    new_revision = stored.revision + 1
    obj = StoredObject(
        id=stored.id,
        api_version=stored.api_version,
        kind=stored.kind,
        practice_id=stored.practice_id,
        revision=new_revision,
        created_at=stored.created_at,
        created_by=stored.created_by,
        content_hash=stored.content_hash,
        supersedes=stored.supersedes,
        labels=stored.labels,
        body=new_body,
    )
    return object_repository.insert_revision(obj, expected_current_revision=stored.revision)


# ---------------------------------------------------------------------------
# build_claim_table.
# ---------------------------------------------------------------------------


def test_claim_table_row_reflects_latest_status_evidence_and_verification() -> None:
    (
        projection_service,
        claim_service,
        _correction_service,
        _source_record_service,
        _evidence_anchor_service,
        _object_repository,
        _edge_repository,
        _event_log,
    ) = _services()
    claim = _claim(status="draft")
    anchor_id = new_urn("evidence-anchor")
    verification_id = new_urn("verification")

    claim_service.create(
        claim, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    claim_service.submit_for_review(
        claim.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    claim_service.add_evidence_edge(
        claim.id,
        anchor_id,
        "supports",
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    claim_service.to_supported(
        claim.id,
        evidence_relations=[anchor_id],
        verification_ids=[verification_id],
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    rows = {row.claim_id: row for row in projection_service.build_claim_table()}

    assert claim.id in rows
    row = rows[claim.id]
    assert row.status == "supported"
    assert row.assertion == claim.assertion
    assert row.evidence_relations == (anchor_id,)
    assert row.verification_ids == (verification_id,)
    assert row.flagged is False
    assert row.unresolved_correction_ids == ()


def test_claim_flagged_by_an_unresolved_critical_correction() -> None:
    (
        projection_service,
        claim_service,
        correction_service,
        _source_record_service,
        _evidence_anchor_service,
        _object_repository,
        _edge_repository,
        _event_log,
    ) = _services()
    claim = _claim(status="draft")
    claim_service.create(
        claim, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    correction = _correction(affected_object_ids=[claim.id])
    correction_service.record(
        correction, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    rows = {row.claim_id: row for row in projection_service.build_claim_table()}

    assert rows[claim.id].flagged is True
    assert rows[claim.id].unresolved_correction_ids == (correction.id,)


def test_claim_not_flagged_once_the_correction_is_resolved() -> None:
    (
        projection_service,
        claim_service,
        correction_service,
        _source_record_service,
        _evidence_anchor_service,
        object_repository,
        _edge_repository,
        _event_log,
    ) = _services()
    claim = _claim(status="draft")
    claim_service.create(
        claim, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    correction = _correction(affected_object_ids=[claim.id])
    stored_correction = correction_service.record(
        correction, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    rows_before = {row.claim_id: row for row in projection_service.build_claim_table()}
    assert rows_before[claim.id].flagged is True

    _reseal_with_status(object_repository, stored_correction, status="RESOLVED")
    rows_after = {row.claim_id: row for row in projection_service.build_claim_table()}

    assert rows_after[claim.id].flagged is False
    assert rows_after[claim.id].unresolved_correction_ids == ()


def test_downstream_dependent_flagged_via_impact_objects_after_propagation() -> None:
    (
        projection_service,
        claim_service,
        correction_service,
        _source_record_service,
        _evidence_anchor_service,
        _object_repository,
        _edge_repository,
        _event_log,
    ) = _services()
    root = _claim(status="draft")
    dependent = _claim(status="draft")
    claim_service.create(
        root, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    claim_service.create(
        dependent, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    claim_service.add_dependency_edge(
        dependent.id,
        root.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    correction = _correction(affected_object_ids=[root.id])
    correction_service.record(
        correction, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    correction_service.propagate_impact(
        correction.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    rows = {row.claim_id: row for row in projection_service.build_claim_table()}

    # The downstream dependent is both status-transitioned AND flagged.
    assert rows[dependent.id].status == "review_required"
    assert rows[dependent.id].flagged is True
    assert rows[dependent.id].unresolved_correction_ids == (correction.id,)

    # The correction's own directly-named seed (root) is flagged too, even
    # though CorrectionImpactService never transitions a seed's own status —
    # see mrr.domain.projection's "What flags a claim" docstring section.
    assert rows[root.id].status == "draft"
    assert rows[root.id].flagged is True
    assert rows[root.id].unresolved_correction_ids == (correction.id,)


def test_a_correction_naming_a_different_claim_does_not_leak_into_this_rows_ids() -> None:
    (
        projection_service,
        claim_service,
        correction_service,
        _source_record_service,
        _evidence_anchor_service,
        _object_repository,
        _edge_repository,
        _event_log,
    ) = _services()
    watched = _claim(status="draft")
    other = _claim(status="draft")
    claim_service.create(
        watched, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    claim_service.create(
        other, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    correction = _correction(affected_object_ids=[other.id])
    correction_service.record(
        correction, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    rows = {row.claim_id: row for row in projection_service.build_claim_table()}

    assert rows[watched.id].flagged is False
    assert rows[watched.id].unresolved_correction_ids == ()
    assert rows[other.id].flagged is True


def test_build_claim_table_is_deterministic_on_rebuild() -> None:
    (
        projection_service,
        claim_service,
        correction_service,
        _source_record_service,
        _evidence_anchor_service,
        _object_repository,
        _edge_repository,
        _event_log,
    ) = _services()
    root = _claim(status="draft")
    dependent = _claim(status="draft")
    claim_service.create(
        root, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    claim_service.create(
        dependent, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    claim_service.add_dependency_edge(
        dependent.id,
        root.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    correction = _correction(affected_object_ids=[root.id])
    correction_service.record(
        correction, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    correction_service.propagate_impact(
        correction.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    first = projection_service.build_claim_table()
    second = projection_service.build_claim_table()

    assert first == second


def test_building_a_projection_writes_no_new_revision_or_edge() -> None:
    (
        projection_service,
        claim_service,
        correction_service,
        _source_record_service,
        _evidence_anchor_service,
        object_repository,
        edge_repository,
        event_log,
    ) = _services()
    root = _claim(status="draft")
    dependent = _claim(status="draft")
    claim_service.create(
        root, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    claim_service.create(
        dependent, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    claim_service.add_dependency_edge(
        dependent.id,
        root.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    correction = _correction(affected_object_ids=[root.id])
    correction_service.record(
        correction, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    revisions_before = {
        object_id: list(revisions) for object_id, revisions in object_repository._revisions.items()
    }
    edges_before = list(edge_repository._edges)
    events_before = list(event_log.read_all())

    projection_service.build_claim_table()
    projection_service.build_provenance_map(root.id)

    assert object_repository._revisions == revisions_before
    assert edge_repository._edges == edges_before
    assert event_log.read_all() == events_before


# ---------------------------------------------------------------------------
# build_provenance_map.
# ---------------------------------------------------------------------------


def test_provenance_map_traces_edge_to_anchor_and_field_to_source_record() -> None:
    (
        projection_service,
        claim_service,
        _correction_service,
        source_record_service,
        evidence_anchor_service,
        _object_repository,
        _edge_repository,
        _event_log,
    ) = _services()
    claim = _claim(status="draft")
    claim_service.create(
        claim, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    source_record = _source_record()
    source_record_service.create(
        source_record,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    anchor = _text_evidence_anchor(source_record_id=source_record.id)
    evidence_anchor_service.create(
        anchor, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    edge = claim_service.add_evidence_edge(
        claim.id,
        anchor.id,
        "supports",
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    provenance = projection_service.build_provenance_map(claim.id)

    assert provenance.claim_id == claim.id
    assert len(provenance.edges) == 2

    edge_hop = next(hop for hop in provenance.edges if hop.via == "edge")
    assert edge_hop.source_id == claim.id
    assert edge_hop.target_id == anchor.id
    assert edge_hop.target_kind == "EvidenceAnchor"
    assert edge_hop.relation == "supports"
    assert edge_hop.edge_id == edge.id

    field_hop = next(hop for hop in provenance.edges if hop.via == "field")
    assert field_hop.source_id == anchor.id
    assert field_hop.target_id == source_record.id
    assert field_hop.target_kind == "SourceRecord"
    assert field_hop.relation == "source_record_id"
    assert field_hop.edge_id is None


def test_provenance_map_traces_field_reference_to_a_run() -> None:
    (
        projection_service,
        claim_service,
        _correction_service,
        _source_record_service,
        evidence_anchor_service,
        object_repository,
        _edge_repository,
        _event_log,
    ) = _services()
    claim = _claim(status="draft")
    claim_service.create(
        claim, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    run_id = new_urn("run")
    _seed_bare_object(object_repository, id=run_id, kind="RunManifest")
    anchor = _computational_evidence_anchor(run_id=run_id)
    evidence_anchor_service.create(
        anchor, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    claim_service.add_evidence_edge(
        claim.id,
        anchor.id,
        "supports",
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    provenance = projection_service.build_provenance_map(claim.id)

    field_hop = next(hop for hop in provenance.edges if hop.via == "field")
    assert field_hop.source_id == anchor.id
    assert field_hop.target_id == run_id
    assert field_hop.target_kind == "RunManifest"
    assert field_hop.relation == "run_id"


def test_provenance_map_excludes_a_dangling_edge_target() -> None:
    (
        projection_service,
        claim_service,
        _correction_service,
        _source_record_service,
        _evidence_anchor_service,
        _object_repository,
        _edge_repository,
        _event_log,
    ) = _services()
    claim = _claim(status="draft")
    claim_service.create(
        claim, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    dangling_id = new_urn("evidence-anchor")
    claim_service.add_evidence_edge(
        claim.id,
        dangling_id,
        "supports",
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    provenance = projection_service.build_provenance_map(claim.id)

    assert provenance.edges == ()


def test_provenance_map_on_missing_claim_raises() -> None:
    (
        projection_service,
        _claim_service,
        _correction_service,
        _source_record_service,
        _evidence_anchor_service,
        _object_repository,
        _edge_repository,
        _event_log,
    ) = _services()
    missing_id = new_urn("claim")

    with pytest.raises(ClaimNotFoundError) as excinfo:
        projection_service.build_provenance_map(missing_id)
    assert excinfo.value.claim_id == missing_id


def test_provenance_map_terminates_on_a_cyclic_graph() -> None:
    (
        projection_service,
        claim_service,
        _correction_service,
        _source_record_service,
        _evidence_anchor_service,
        _object_repository,
        _edge_repository,
        _event_log,
    ) = _services()
    claim_a = _claim(status="draft")
    claim_b = _claim(status="draft")
    claim_service.create(
        claim_a, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    claim_service.create(
        claim_b, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    claim_service.link_related_claim(
        claim_a.id,
        claim_b.id,
        "qualifies",
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    claim_service.link_related_claim(
        claim_b.id,
        claim_a.id,
        "qualifies",
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    provenance = projection_service.build_provenance_map(claim_a.id)

    assert len(provenance.edges) == 2
    assert {(hop.source_id, hop.target_id) for hop in provenance.edges} == {
        (claim_a.id, claim_b.id),
        (claim_b.id, claim_a.id),
    }


def test_build_provenance_map_is_deterministic_on_rebuild() -> None:
    (
        projection_service,
        claim_service,
        _correction_service,
        source_record_service,
        evidence_anchor_service,
        _object_repository,
        _edge_repository,
        _event_log,
    ) = _services()
    claim = _claim(status="draft")
    claim_service.create(
        claim, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    source_record = _source_record()
    source_record_service.create(
        source_record,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    anchor = _text_evidence_anchor(source_record_id=source_record.id)
    evidence_anchor_service.create(
        anchor, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    claim_service.add_evidence_edge(
        claim.id,
        anchor.id,
        "supports",
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    first = projection_service.build_provenance_map(claim.id)
    second = projection_service.build_provenance_map(claim.id)

    assert first == second
