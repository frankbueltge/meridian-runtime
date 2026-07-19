"""Unit tests for ``mrr.services.correction.service.CorrectionImpactService``
(task-packets/E3-T06.yaml), run entirely DB-free against in-memory fakes of
``mrr.domain.repositories.ObjectRepository``/``EdgeRepository`` and the
event-log read surface — no PostgreSQL, no ``sqlalchemy.Engine``. A REAL
``mrr.services.claim.service.ClaimService`` is constructed over the same
fakes (never reimplemented — the task packet's own instruction to "inject
it; do not delete/overwrite claim decisions"), matching
``tests/unit/services/verification/test_service.py``'s identical
"construct a real ClaimService over shared fakes" pattern.

Acceptance-test mapping (task-packets/E3-T06.yaml, unit tier):

- "an affected claim gains a review_required revision" (the DB-free half; the
  real-PostgreSQL half is tests/integration/services/correction/test_service.py)
  -> ``test_propagate_impact_marks_a_dependent_claim_review_required``,
  ``test_propagate_impact_leaves_the_seed_claim_untouched``.
- "already-reviewed object - idempotent, no duplicate obligation/revision" ->
  ``test_propagate_impact_is_idempotent_on_repeated_calls``,
  ``test_propagate_impact_skips_a_claim_already_review_required``,
  ``test_propagate_impact_skips_a_terminal_claim``.
- "only impact edge types propagate; contradicts/reviews edges do not" ->
  ``test_propagate_impact_does_not_mark_a_claim_linked_only_by_a_non_impact_edge``.
- multi-hop closure driven through the service's own query-based
  ``EdgeRepository`` (not just the pure module) ->
  ``test_propagate_impact_follows_a_multi_hop_chain``.
- non-claim / dangling impacted ids are skipped without error ->
  ``test_propagate_impact_skips_a_non_claim_impacted_object``,
  ``test_propagate_impact_skips_an_impacted_id_with_no_stored_object``.
- ``record()``'s own guardrails -> ``test_record_rejects_non_open_initial_status``,
  ``test_record_rejects_wrong_initial_revision_number``,
  ``test_record_persists_revision_1_and_recorded_event``.
- a missing correction -> ``test_propagate_impact_on_missing_correction_raises``.
- event provenance -> ``test_impact_computed_event_carries_complete_provenance``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from mrr.contracts import Claim, CorrectionEvent
from mrr.domain.exceptions import (
    CorrectionNotFoundError,
    InvalidTransitionError,
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

# ---------------------------------------------------------------------------
# In-memory fakes — identical in spirit to
# tests/unit/services/verification/test_service.py's own fakes, shared here
# by BOTH the ClaimService and the CorrectionImpactService under test.
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
    CorrectionImpactService,
    ClaimService,
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
    return correction_service, claim_service, object_repository, edge_repository, event_log


# ---------------------------------------------------------------------------
# Fixture factories.
# ---------------------------------------------------------------------------

_ACTOR = new_urn("agent-role")
_POLICY_VERSION = "policy-2026-07-01"


def _correlation_id() -> str:
    return new_urn("correction-run")


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
        "correction_type": "numeric_error",
        "severity": "material",
        "reason": "Fixture reason: the denominator was later shown to be wrong.",
        "evidence_refs": [new_urn("evidence-anchor")],
        "originator_id": new_urn("person"),
        "requested_action": "Mark dependent claims review_required and recompute.",
        "replacement_object_id": None,
        "status": "OPEN",
        "impact_objects": [],
    }
    data.update(overrides)
    return CorrectionEvent.model_validate(data)


def _stored_object_from_model(model: Any) -> StoredObject:
    body: dict[str, Any] = json.loads(model.model_dump_json(exclude_none=True))
    return StoredObject(
        id=model.id,
        api_version=model.api_version,
        kind=model.kind,
        practice_id=model.practice_id,
        revision=model.revision,
        created_at=model.created_at,
        created_by=model.created_by,
        content_hash=model.content_hash,
        supersedes=model.supersedes,
        labels=model.labels,
        body=body,
    )


def _seed(object_repository: FakeObjectRepository, model: Any) -> StoredObject:
    return object_repository.insert_revision(
        _stored_object_from_model(model), expected_current_revision=None
    )


def _seed_dependency_edge(
    edge_repository: FakeEdgeRepository,
    *,
    dependent_id: str,
    dependency_id: str,
    edge_type: str = "depends_on",
) -> TypedEdge:
    edge = TypedEdge(
        id=new_urn("edge"),
        source_id=dependent_id,
        target_id=dependency_id,
        edge_type=edge_type,
        created_at=datetime.now(UTC),
        created_by=_ACTOR,
        scope=None,
        status="active",
        practice_id=new_urn("practice"),
    )
    return edge_repository.add_edge(edge)


# ---------------------------------------------------------------------------
# record(): initial-status/revision guardrails and the happy path.
# ---------------------------------------------------------------------------


def test_record_rejects_non_open_initial_status() -> None:
    service, _, object_repository, _, event_log = _services()
    correction = _correction(affected_object_ids=[new_urn("claim")], status="RESOLVED")

    with pytest.raises(InvalidTransitionError) as excinfo:
        service.record(
            correction,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )

    assert excinfo.value.to_state == "RESOLVED"
    assert object_repository.list_revisions(correction.id) == []
    assert event_log.read_all() == []


def test_record_rejects_wrong_initial_revision_number() -> None:
    service, _, _, _, _ = _services()
    correction = _correction(affected_object_ids=[new_urn("claim")], revision=2)

    with pytest.raises(ValueError, match="revision must be 1"):
        service.record(
            correction,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )


def test_record_persists_revision_1_and_recorded_event() -> None:
    service, _, object_repository, _, event_log = _services()
    correction = _correction(affected_object_ids=[new_urn("claim")])
    correlation_id = _correlation_id()

    stored = service.record(
        correction, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    assert stored.revision == 1
    assert stored.body["status"] == "OPEN"
    assert object_repository.get_latest(correction.id).id == correction.id

    events = event_log.read_all()
    assert len(events) == 1
    assert events[0].event.event_type == "correction.recorded"
    assert events[0].event.causation_id is None
    assert events[0].event.correlation_id == correlation_id


# ---------------------------------------------------------------------------
# propagate_impact(): the core impact-propagation behavior.
# ---------------------------------------------------------------------------


def test_propagate_impact_on_missing_correction_raises() -> None:
    service, _, _, _, _ = _services()
    missing_id = new_urn("correction")

    with pytest.raises(CorrectionNotFoundError) as excinfo:
        service.propagate_impact(
            missing_id,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )
    assert excinfo.value.correction_id == missing_id


def test_propagate_impact_marks_a_dependent_claim_review_required() -> None:
    service, claim_service, object_repository, edge_repository, event_log = _services()
    root = _claim(status="under_review")
    dependent = _claim(status="under_review")
    _seed(object_repository, root)
    _seed(object_repository, dependent)
    _seed_dependency_edge(edge_repository, dependent_id=dependent.id, dependency_id=root.id)

    correction = _correction(affected_object_ids=[root.id])
    service.record(
        correction, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    stored_correction = service.propagate_impact(
        correction.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert stored_correction.body["impact_objects"] == [dependent.id]
    assert object_repository.get_latest(dependent.id).body["status"] == "review_required"

    event_types = [appended.event.event_type for appended in event_log.read_all()]
    assert "correction.impact_computed" in event_types
    assert "claim.review_required" in event_types


def test_propagate_impact_leaves_the_seed_claim_untouched() -> None:
    """The directly-corrected seed claim is NOT auto-transitioned — only the
    computed downstream impact_objects are (see the service module
    docstring's "What counts as affected" section).
    """
    service, _, object_repository, edge_repository, _ = _services()
    root = _claim(status="under_review")
    dependent = _claim(status="under_review")
    _seed(object_repository, root)
    _seed(object_repository, dependent)
    _seed_dependency_edge(edge_repository, dependent_id=dependent.id, dependency_id=root.id)

    correction = _correction(affected_object_ids=[root.id])
    service.record(
        correction, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    service.propagate_impact(
        correction.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert object_repository.get_latest(root.id).body["status"] == "under_review"
    assert [rev.revision for rev in object_repository.list_revisions(root.id)] == [1]


def test_propagate_impact_follows_a_multi_hop_chain() -> None:
    """Exercises the SERVICE's own query-driven ``_gather_impact_edges`` BFS
    across more than one hop — the pure ``compute_impact`` function's own
    multi-hop behavior is already covered DB-free in
    tests/unit/domain/test_correction_impact.py, but the service's
    EdgeRepository-querying traversal is new code, not shared with that
    module, and deserves its own multi-hop exercise.
    """
    service, _, object_repository, edge_repository, _ = _services()
    root = _claim(status="under_review")
    middle = _claim(status="under_review")
    leaf = _claim(status="under_review")
    for claim in (root, middle, leaf):
        _seed(object_repository, claim)
    _seed_dependency_edge(edge_repository, dependent_id=middle.id, dependency_id=root.id)
    _seed_dependency_edge(edge_repository, dependent_id=leaf.id, dependency_id=middle.id)

    correction = _correction(affected_object_ids=[root.id])
    service.record(
        correction, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    stored_correction = service.propagate_impact(
        correction.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert set(stored_correction.body["impact_objects"]) == {middle.id, leaf.id}
    assert object_repository.get_latest(middle.id).body["status"] == "review_required"
    assert object_repository.get_latest(leaf.id).body["status"] == "review_required"


def test_propagate_impact_does_not_mark_a_claim_linked_only_by_a_non_impact_edge() -> None:
    service, _, object_repository, edge_repository, _ = _services()
    root = _claim(status="under_review")
    critic = _claim(status="under_review")
    _seed(object_repository, root)
    _seed(object_repository, critic)
    _seed_dependency_edge(
        edge_repository, dependent_id=critic.id, dependency_id=root.id, edge_type="contradicts"
    )

    correction = _correction(affected_object_ids=[root.id])
    service.record(
        correction, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    stored_correction = service.propagate_impact(
        correction.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert stored_correction.body["impact_objects"] == []
    assert object_repository.get_latest(critic.id).body["status"] == "under_review"


def test_propagate_impact_skips_a_non_claim_impacted_object() -> None:
    service, _, object_repository, edge_repository, event_log = _services()
    root = _claim(status="under_review")
    _seed(object_repository, root)

    anchor_id = new_urn("evidence-anchor")
    anchor_stored_object = StoredObject(
        id=anchor_id,
        api_version="mrr/v1alpha1",
        kind="EvidenceAnchor",
        practice_id=new_urn("practice"),
        revision=1,
        created_at=datetime.now(UTC),
        created_by=_ACTOR,
        content_hash="sha256:" + "f" * 64,
        supersedes=None,
        labels=None,
        body={"kind": "EvidenceAnchor"},
    )
    object_repository.insert_revision(anchor_stored_object, expected_current_revision=None)
    _seed_dependency_edge(
        edge_repository, dependent_id=anchor_id, dependency_id=root.id, edge_type="uses_source"
    )

    correction = _correction(affected_object_ids=[root.id])
    service.record(
        correction, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    stored_correction = service.propagate_impact(
        correction.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert stored_correction.body["impact_objects"] == [anchor_id]
    # No claim.review_required event was emitted for the non-Claim object.
    assert "claim.review_required" not in [
        appended.event.event_type for appended in event_log.read_all()
    ]


def test_propagate_impact_skips_an_impacted_id_with_no_stored_object() -> None:
    """An edge may reference an id this ObjectRepository has never seen at
    all (there is no foreign key from edges to objects) — propagate_impact
    must not raise for it.
    """
    service, _, object_repository, edge_repository, _ = _services()
    root = _claim(status="under_review")
    _seed(object_repository, root)
    dangling_id = new_urn("claim")
    _seed_dependency_edge(edge_repository, dependent_id=dangling_id, dependency_id=root.id)

    correction = _correction(affected_object_ids=[root.id])
    service.record(
        correction, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    stored_correction = service.propagate_impact(
        correction.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    assert stored_correction.body["impact_objects"] == [dangling_id]


# ---------------------------------------------------------------------------
# Idempotency (MRR-FR-093): the headline invariant.
# ---------------------------------------------------------------------------


def test_propagate_impact_is_idempotent_on_repeated_calls() -> None:
    service, _, object_repository, edge_repository, event_log = _services()
    root = _claim(status="under_review")
    dependent = _claim(status="under_review")
    _seed(object_repository, root)
    _seed(object_repository, dependent)
    _seed_dependency_edge(edge_repository, dependent_id=dependent.id, dependency_id=root.id)

    correction = _correction(affected_object_ids=[root.id])
    service.record(
        correction, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    first = service.propagate_impact(
        correction.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    events_after_first = list(event_log.read_all())
    dependent_revisions_after_first = object_repository.list_revisions(dependent.id)

    second = service.propagate_impact(
        correction.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    # Same impact set both times; no new correction revision on the re-run.
    assert first.body["impact_objects"] == second.body["impact_objects"]
    assert first.revision == second.revision
    # No new revision was added to the dependent claim either.
    assert object_repository.list_revisions(dependent.id) == dependent_revisions_after_first
    # No new events were appended by the no-op second run.
    assert event_log.read_all() == events_after_first


def test_propagate_impact_skips_a_claim_already_review_required() -> None:
    service, _, object_repository, edge_repository, _ = _services()
    root = _claim(status="under_review")
    # Seeded directly at review_required, as if a prior process already
    # flagged it (e.g. an earlier, separate correction).
    dependent = _claim(status="review_required")
    _seed(object_repository, root)
    _seed(object_repository, dependent)
    _seed_dependency_edge(edge_repository, dependent_id=dependent.id, dependency_id=root.id)

    correction = _correction(affected_object_ids=[root.id])
    service.record(
        correction, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    # Must not raise InvalidTransitionError (review_required -> review_required
    # is not a legal CLAIM_LIFECYCLE edge) and must not add a new revision.
    service.propagate_impact(
        correction.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert [rev.revision for rev in object_repository.list_revisions(dependent.id)] == [1]


@pytest.mark.parametrize("terminal_status", ["withdrawn", "superseded"])
def test_propagate_impact_skips_a_terminal_claim(terminal_status: str) -> None:
    service, _, object_repository, edge_repository, _ = _services()
    root = _claim(status="under_review")
    dependent = _claim(status=terminal_status)
    _seed(object_repository, root)
    _seed(object_repository, dependent)
    _seed_dependency_edge(edge_repository, dependent_id=dependent.id, dependency_id=root.id)

    correction = _correction(affected_object_ids=[root.id])
    service.record(
        correction, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    # Must not raise (a terminal state has no outgoing CLAIM_LIFECYCLE edge
    # at all, including none to review_required).
    service.propagate_impact(
        correction.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert [rev.revision for rev in object_repository.list_revisions(dependent.id)] == [1]


# ---------------------------------------------------------------------------
# Event provenance completeness (MRR-NFR-001).
# ---------------------------------------------------------------------------


def test_impact_computed_event_carries_complete_provenance() -> None:
    service, _, object_repository, edge_repository, event_log = _services()
    root = _claim(status="under_review")
    dependent = _claim(status="under_review")
    _seed(object_repository, root)
    _seed(object_repository, dependent)
    _seed_dependency_edge(edge_repository, dependent_id=dependent.id, dependency_id=root.id)

    correction = _correction(affected_object_ids=[root.id])
    correlation_id = _correlation_id()
    service.record(
        correction, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    service.propagate_impact(
        correction.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    events = [
        appended.event
        for appended in event_log.read_all()
        if appended.event.object_id == correction.id
    ]
    assert len(events) == 2
    recorded_event, impact_event = events

    assert recorded_event.causation_id is None
    assert impact_event.causation_id == recorded_event.id

    for event in (recorded_event, impact_event):
        assert event.actor == _ACTOR
        assert event.policy_version == _POLICY_VERSION
        assert event.correlation_id == correlation_id
        assert event.object_id == correction.id
        assert event.occurred_at.tzinfo is not None
