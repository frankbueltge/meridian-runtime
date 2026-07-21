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

Acceptance-test mapping (task-packets/E6-T04.yaml, unit tier — ``record_response``):

- happy path accept/reject/defer ->
  ``test_record_response_happy_path_accept_records_response_and_event_no_edge``,
  ``test_record_response_happy_path_reject_or_defer_records_response_and_event_no_edge``.
- happy path adapt, single/multiple adaptations ->
  ``test_record_response_happy_path_adapt_single_adaptation_records_one_corrects_edge``,
  ``test_record_response_happy_path_adapt_multiple_adaptations_records_n_corrects_edges``.
- adversarial: a missing ``adapted_object_id`` aborts the whole call, even
  alongside a valid entry ->
  ``test_record_response_adversarial_missing_adapted_object_id_raises_and_persists_nothing``.
- adversarial: an ``adaptations[].notified_object_id`` not a member of
  ``notified_object_ids`` ->
  ``test_record_response_adversarial_adaptation_not_a_member_of_notified_object_ids_raises``.
- adversarial: duplicate response ->
  ``test_record_response_adversarial_duplicate_response_raises_before_any_write``.
- never mutates/consults the sender's CORRECTION_LIFECYCLE ->
  ``test_record_response_never_invokes_correction_lifecycle``.
- missing dependency guard ->
  ``test_record_response_without_record_revision_with_edges_dependency_raises_valueerror``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.contracts import Claim, CorrectionEvent
from mrr.contracts.correction_notification import CorrectionNotification
from mrr.contracts.correction_response import CorrectionResponseAdaptation
from mrr.contracts.node_message_envelope import NodeMessageEnvelope
from mrr.contracts.practice import Practice
from mrr.crypto.exceptions import SignatureVerificationError
from mrr.crypto.keys import derive_key_id, encode_public_key, generate_ed25519_keypair
from mrr.domain.envelope_transport import EnvelopeDeliveryOutcome, EnvelopeDeliveryRequest
from mrr.domain.exceptions import (
    CorrectionNotFoundError,
    CorrectionNotificationAlreadyProcessedError,
    CorrectionNotificationNotWithinValidityWindowError,
    CorrectionResponseAlreadyRecordedError,
    EnvelopeAlreadyProcessedError,
    InvalidTransitionError,
    ObjectNotFoundError,
    RevisionConflictError,
    UnknownEdgeTypeError,
)
from mrr.domain.hashing_policy import sign_object
from mrr.domain.identity import new_urn
from mrr.domain.lifecycles import CORRECTION_LIFECYCLE
from mrr.domain.manifest_trust import practice_key_ring
from mrr.domain.repositories import EDGE_VOCABULARY, StoredObject, TypedEdge
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.claim.service import ClaimService
from mrr.services.correction.service import CorrectionImpactService, NotificationRecipient
from pydantic import ValidationError

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


def _fake_record_revision_with_edges(
    object_repository: FakeObjectRepository,
    edge_repository: FakeEdgeRepository,
    event_log: FakeEventLog,
) -> Any:
    """DB-free fake of the E6-T04 ``bind_revision_with_edges_unit_of_work``
    shape: insert the object revision, every edge, and append the event —
    composed here without a real transaction (a DB-free fake has nothing to
    roll back), mirroring ``_fake_record``/``_fake_record_edge``'s own
    identical "compose the fakes in the same order the real bound closure
    would" convention.
    """

    def _record(
        obj: StoredObject,
        expected_current_revision: int | None,
        edges: list[TypedEdge],
        event: DomainEvent,
    ) -> tuple[StoredObject, list[TypedEdge], AppendedEvent]:
        stored = object_repository.insert_revision(obj, expected_current_revision)
        stored_edges = [edge_repository.add_edge(edge) for edge in edges]
        appended = event_log.append_for_test(event)
        return stored, stored_edges, appended

    return _record


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


def _services_with_notification() -> tuple[
    CorrectionImpactService,
    ClaimService,
    FakeObjectRepository,
    FakeEdgeRepository,
    FakeEventLog,
]:
    """Identical to :func:`_services` but additionally wires the OPTIONAL
    ``record_event`` dependency (task-packets/E6-T03.yaml) — needed by
    ``notify_affected_practices`` whenever more than one recipient's event
    must be appended per call. A DB-free fake of
    ``mrr.persistence.unit_of_work.record_event``'s shape: append the event,
    write no object revision.
    """
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

    def _record_event_only(event: DomainEvent) -> AppendedEvent:
        return event_log.append_for_test(event)

    correction_service = CorrectionImpactService(
        object_repository,
        edge_repository,
        claim_service,
        event_log,
        _fake_record(object_repository, event_log),
        _record_event_only,
    )
    return correction_service, claim_service, object_repository, edge_repository, event_log


def _services_with_response() -> tuple[
    CorrectionImpactService,
    ClaimService,
    FakeObjectRepository,
    FakeEdgeRepository,
    FakeEventLog,
]:
    """Identical to :func:`_services` but additionally wires the OPTIONAL
    ``record_revision_with_edges`` dependency (task-packets/E6-T04.yaml) —
    needed by ``record_response``.
    """
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
        record_revision_with_edges=_fake_record_revision_with_edges(
            object_repository, edge_repository, event_log
        ),
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


# ---------------------------------------------------------------------------
# E6-T03: cross-practice correction notification.
# ---------------------------------------------------------------------------

_NOTIFICATION_SENT_AT = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)
_NOTIFICATION_EXPIRES_AT = _NOTIFICATION_SENT_AT + timedelta(minutes=5)


class FakeEnvelopeTransport:
    """A test-only ``EnvelopeTransport`` fake — one delivery outcome per
    ``recipient_endpoint`` (fixed ahead of time, unlike ``message_id``,
    which is freshly minted per envelope) — mirrors
    ``mrr.domain.envelope_transport``'s own "tests use only an in-test fake"
    precedent.
    """

    def __init__(self, status_by_endpoint: dict[str, str]) -> None:
        self._status_by_endpoint = status_by_endpoint
        self.sent_requests: list[EnvelopeDeliveryRequest] = []

    def send(self, request: EnvelopeDeliveryRequest) -> EnvelopeDeliveryOutcome:
        self.sent_requests.append(request)
        status = self._status_by_endpoint[request.recipient_endpoint]
        return EnvelopeDeliveryOutcome(status=status, message_id=request.envelope.message_id)  # type: ignore[arg-type]


def _notifying_practice_fixture() -> tuple[Practice, Ed25519PrivateKey, str]:
    """A fresh notifying-practice Practice + its one active signing key."""
    private_key, public_key = generate_ed25519_keypair()
    practice_id = new_urn("practice")
    kid = derive_key_id(public_key)
    practice = Practice.model_validate(
        {
            "id": practice_id,
            "api_version": "mrr/v1alpha1",
            "kind": "Practice",
            "practice_id": practice_id,
            "revision": 1,
            "created_at": _NOTIFICATION_SENT_AT,
            "created_by": new_urn("agent-role"),
            "content_hash": "sha256:" + "a" * 64,
            "name": "Fixture Notifying Practice",
            "description": "Fixture practice for E6-T03 correction notification tests.",
            "keys": [
                {
                    "kid": kid,
                    "algorithm": "Ed25519",
                    "encoded_public_key": encode_public_key(public_key),
                    "valid_from": _NOTIFICATION_SENT_AT - timedelta(days=1),
                    "valid_until": _NOTIFICATION_SENT_AT + timedelta(days=365),
                    "state": "active",
                }
            ],
            "governance_contacts": ["mailto:governance@fixture.invalid"],
            "supported_policy_versions": ["policy-2026-07-01"],
            "disclosure": {"max_disclosure": "PUBLIC", "trust_statement": "fixture"},
        }
    )
    return practice, private_key, kid


def _build_signed_notification(
    *,
    notifying_practice_id: str,
    key_id: str,
    private_key: Ed25519PrivateKey,
    recipient_practice_id: str,
    notified_object_ids: list[str],
    **overrides: Any,
) -> CorrectionNotification:
    data: dict[str, Any] = {
        "notification_id": new_urn("correction-notification"),
        "correction_id": new_urn("correction"),
        "correction_revision": 1,
        "notifying_practice_id": notifying_practice_id,
        "recipient_practice_id": recipient_practice_id,
        "notified_object_ids": notified_object_ids,
        "correction_type": "numeric_error",
        "severity": "material",
        "reason": "Fixture reason: the denominator was later shown to be wrong.",
        "requested_action": "Mark dependent claims review_required and recompute.",
        "replacement_object_id": None,
        "content_hash": "sha256:" + "3" * 64,
        "nonce": "n" * 16,
        "sent_at": _NOTIFICATION_SENT_AT,
        "expires_at": _NOTIFICATION_EXPIRES_AT,
        "signature": {
            "signer_practice_id": notifying_practice_id,
            "key_id": key_id,
            "algorithm": "Ed25519",
            "signed_at": _NOTIFICATION_SENT_AT,
            "value": "0" * 44,
        },
    }
    data.update(overrides)
    draft = CorrectionNotification.model_validate(data)
    signature_value = sign_object(private_key, json.loads(draft.model_dump_json(exclude_none=True)))
    return draft.model_copy(
        update={"signature": draft.signature.model_copy(update={"value": signature_value})}
    )


def _build_signed_envelope(
    notification: CorrectionNotification,
    *,
    sender_practice_id: str,
    key_id: str,
    private_key: Ed25519PrivateKey,
    recipient_node_id: str,
    **overrides: Any,
) -> NodeMessageEnvelope:
    data: dict[str, Any] = {
        "message_id": new_urn("node-message-envelope"),
        "sender_node_id": new_urn("node"),
        "sender_practice_id": sender_practice_id,
        "recipient_node_id": recipient_node_id,
        "sent_at": _NOTIFICATION_SENT_AT,
        "expires_at": _NOTIFICATION_EXPIRES_AT,
        "payload_kind": "CorrectionNotification",
        "payload_content_hash": notification.content_hash,
        "payload": json.loads(notification.model_dump_json(exclude_none=True)),
        "signature": {
            "signer_practice_id": sender_practice_id,
            "key_id": key_id,
            "algorithm": "Ed25519",
            "signed_at": _NOTIFICATION_SENT_AT,
            "value": "0" * 44,
        },
    }
    data.update(overrides)
    draft = NodeMessageEnvelope.model_validate(data)
    signature_value = sign_object(private_key, json.loads(draft.model_dump_json(exclude_none=True)))
    return draft.model_copy(
        update={"signature": draft.signature.model_copy(update={"value": signature_value})}
    )


def _never_processed(_: str) -> bool:
    return False


# ---------------------------------------------------------------------------
# notify_affected_practices — outbound.
# ---------------------------------------------------------------------------


def test_notify_outbound_happy_path_two_recipients_delivered() -> None:
    service, _, object_repository, _, event_log = _services_with_notification()
    practice, private_key, key_id = _notifying_practice_fixture()

    correction = _correction(affected_object_ids=[new_urn("claim")])
    service.record(
        correction, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    recipient_a = NotificationRecipient(
        recipient_practice_id=new_urn("practice"),
        recipient_node_id=new_urn("node"),
        recipient_endpoint="endpoint-a",
        notified_object_ids=[correction.affected_objects[0].id],
    )
    recipient_b = NotificationRecipient(
        recipient_practice_id=new_urn("practice"),
        recipient_node_id=new_urn("node"),
        recipient_endpoint="endpoint-b",
        notified_object_ids=[correction.affected_objects[0].id],
    )
    transport = FakeEnvelopeTransport({"endpoint-a": "delivered", "endpoint-b": "delivered"})

    stored = service.notify_affected_practices(
        correction.id,
        recipients=[recipient_a, recipient_b],
        transport=transport,
        sender_node_id=new_urn("node"),
        notifying_practice_id=practice.id,
        signing_key=private_key,
        signing_key_id=key_id,
        sent_at=_NOTIFICATION_SENT_AT,
        expires_at=_NOTIFICATION_EXPIRES_AT,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert stored.body["status"] == "AWAITING_RESPONSES"
    assert [rev.body["status"] for rev in object_repository.list_revisions(correction.id)] == [
        "OPEN",
        "AWAITING_RESPONSES",
    ]
    assert len(transport.sent_requests) == 2

    sent_events = [
        appended.event
        for appended in event_log.read_all()
        if appended.event.object_id == correction.id
        and appended.event.event_type == "correction.notification_sent"
    ]
    assert len(sent_events) == 2
    assert {event.payload["delivery_status"] for event in sent_events} == {"sent"}
    assert {event.payload["recipient_practice_id"] for event in sent_events} == {
        recipient_a.recipient_practice_id,
        recipient_b.recipient_practice_id,
    }


def test_notify_outbound_partial_failure_reaches_delivery_pending() -> None:
    service, _, _, _, event_log = _services_with_notification()
    practice, private_key, key_id = _notifying_practice_fixture()

    correction = _correction(affected_object_ids=[new_urn("claim")])
    service.record(
        correction, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    recipient_ok = NotificationRecipient(
        recipient_practice_id=new_urn("practice"),
        recipient_node_id=new_urn("node"),
        recipient_endpoint="endpoint-ok",
        notified_object_ids=[correction.affected_objects[0].id],
    )
    recipient_fails = NotificationRecipient(
        recipient_practice_id=new_urn("practice"),
        recipient_node_id=new_urn("node"),
        recipient_endpoint="endpoint-fails",
        notified_object_ids=[correction.affected_objects[0].id],
    )
    transport = FakeEnvelopeTransport({"endpoint-ok": "delivered", "endpoint-fails": "failed"})

    stored = service.notify_affected_practices(
        correction.id,
        recipients=[recipient_ok, recipient_fails],
        transport=transport,
        sender_node_id=new_urn("node"),
        notifying_practice_id=practice.id,
        signing_key=private_key,
        signing_key_id=key_id,
        sent_at=_NOTIFICATION_SENT_AT,
        expires_at=_NOTIFICATION_EXPIRES_AT,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert stored.body["status"] == "DELIVERY_PENDING"

    sent_events = {
        appended.event.payload["recipient_practice_id"]: appended.event.payload["delivery_status"]
        for appended in event_log.read_all()
        if appended.event.object_id == correction.id
        and appended.event.event_type == "correction.notification_sent"
    }
    assert sent_events[recipient_ok.recipient_practice_id] == "sent"
    assert sent_events[recipient_fails.recipient_practice_id] == "pending"


def test_notify_outbound_idempotency_no_duplicate_events_or_revision() -> None:
    service, _, object_repository, _, event_log = _services_with_notification()
    practice, private_key, key_id = _notifying_practice_fixture()

    correction = _correction(affected_object_ids=[new_urn("claim")])
    service.record(
        correction, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    recipient = NotificationRecipient(
        recipient_practice_id=new_urn("practice"),
        recipient_node_id=new_urn("node"),
        recipient_endpoint="endpoint-a",
        notified_object_ids=[correction.affected_objects[0].id],
    )
    transport = FakeEnvelopeTransport({"endpoint-a": "delivered"})

    kwargs: dict[str, Any] = {
        "recipients": [recipient],
        "transport": transport,
        "sender_node_id": new_urn("node"),
        "notifying_practice_id": practice.id,
        "signing_key": private_key,
        "signing_key_id": key_id,
        "sent_at": _NOTIFICATION_SENT_AT,
        "expires_at": _NOTIFICATION_EXPIRES_AT,
        "actor": _ACTOR,
        "policy_version": _POLICY_VERSION,
    }

    first = service.notify_affected_practices(
        correction.id, correlation_id=_correlation_id(), **kwargs
    )
    revisions_after_first = object_repository.list_revisions(correction.id)
    events_after_first = list(event_log.read_all())
    requests_after_first = list(transport.sent_requests)

    second = service.notify_affected_practices(
        correction.id, correlation_id=_correlation_id(), **kwargs
    )

    assert first.revision == second.revision
    assert object_repository.list_revisions(correction.id) == revisions_after_first
    assert event_log.read_all() == events_after_first
    # No second delivery attempt was made for the already-"sent" recipient.
    assert transport.sent_requests == requests_after_first


def test_notify_without_record_event_dependency_raises_value_error_when_needed() -> None:
    """``_services()`` (unlike ``_services_with_notification()``) does not
    wire ``record_event`` — calling ``notify_affected_practices`` with two
    recipients (which needs it, since only one event can be bundled with
    the single new revision) must fail with a clear error rather than
    silently dropping the second recipient's event.
    """
    service, _, _, _, _ = _services()
    practice, private_key, key_id = _notifying_practice_fixture()

    correction = _correction(affected_object_ids=[new_urn("claim")])
    service.record(
        correction, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    recipients = [
        NotificationRecipient(
            recipient_practice_id=new_urn("practice"),
            recipient_node_id=new_urn("node"),
            recipient_endpoint=endpoint,
            notified_object_ids=[correction.affected_objects[0].id],
        )
        for endpoint in ("endpoint-a", "endpoint-b")
    ]
    transport = FakeEnvelopeTransport({"endpoint-a": "delivered", "endpoint-b": "delivered"})

    with pytest.raises(ValueError, match="record_event"):
        service.notify_affected_practices(
            correction.id,
            recipients=recipients,
            transport=transport,
            sender_node_id=new_urn("node"),
            notifying_practice_id=practice.id,
            signing_key=private_key,
            signing_key_id=key_id,
            sent_at=_NOTIFICATION_SENT_AT,
            expires_at=_NOTIFICATION_EXPIRES_AT,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )


# ---------------------------------------------------------------------------
# CORRECTION_LIFECYCLE conformance (adversarial) — guards that no new
# illegal edge was accidentally introduced by this task's own hop-chain
# logic (task-packets/E6-T03.yaml acceptance test).
# ---------------------------------------------------------------------------


def test_correction_lifecycle_forbids_direct_notifying_to_delivery_pending() -> None:
    with pytest.raises(InvalidTransitionError):
        CORRECTION_LIFECYCLE.assert_transition("NOTIFYING", "DELIVERY_PENDING")


def test_correction_lifecycle_forbids_open_to_awaiting_responses_skipping_hops() -> None:
    with pytest.raises(InvalidTransitionError):
        CORRECTION_LIFECYCLE.assert_transition("OPEN", "AWAITING_RESPONSES")


# ---------------------------------------------------------------------------
# receive_correction_notification — inbound.
# ---------------------------------------------------------------------------


def test_receive_inbound_happy_path_marks_local_claims_review_required() -> None:
    service, _, object_repository, edge_repository, _ = _services()
    practice, private_key, key_id = _notifying_practice_fixture()
    this_node_id = new_urn("node")
    ring = practice_key_ring(practice)

    notified_object_id = new_urn("claim")
    local_dependent = _claim(status="under_review")
    local_unrelated = _claim(status="under_review")
    _seed(object_repository, local_dependent)
    _seed(object_repository, local_unrelated)
    _seed_dependency_edge(
        edge_repository, dependent_id=local_dependent.id, dependency_id=notified_object_id
    )

    notification = _build_signed_notification(
        notifying_practice_id=practice.id,
        key_id=key_id,
        private_key=private_key,
        recipient_practice_id=new_urn("practice"),
        notified_object_ids=[notified_object_id],
    )
    envelope = _build_signed_envelope(
        notification,
        sender_practice_id=practice.id,
        key_id=key_id,
        private_key=private_key,
        recipient_node_id=this_node_id,
    )

    impact = service.receive_correction_notification(
        envelope,
        this_node_id=this_node_id,
        trusted_notifying_practice_id=practice.id,
        ring=ring,
        already_processed_envelope=_never_processed,
        already_processed_notification=_never_processed,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
        at=_NOTIFICATION_SENT_AT,
    )

    assert impact.notification_id == notification.notification_id
    assert impact.locally_impacted_object_ids == frozenset({local_dependent.id})
    assert object_repository.get_latest(local_dependent.id).body["status"] == "review_required"
    assert object_repository.get_latest(local_unrelated.id).body["status"] == "under_review"


def test_receive_adversarial_tampered_notification_is_rejected() -> None:
    service, _, object_repository, edge_repository, _ = _services()
    practice, private_key, key_id = _notifying_practice_fixture()
    this_node_id = new_urn("node")
    ring = practice_key_ring(practice)

    notified_object_id = new_urn("claim")
    local_dependent = _claim(status="under_review")
    _seed(object_repository, local_dependent)
    _seed_dependency_edge(
        edge_repository, dependent_id=local_dependent.id, dependency_id=notified_object_id
    )

    notification = _build_signed_notification(
        notifying_practice_id=practice.id,
        key_id=key_id,
        private_key=private_key,
        recipient_practice_id=new_urn("practice"),
        notified_object_ids=[notified_object_id],
    )
    tampered_notification = notification.model_copy(update={"severity": "critical"})
    envelope = _build_signed_envelope(
        tampered_notification,
        sender_practice_id=practice.id,
        key_id=key_id,
        private_key=private_key,
        recipient_node_id=this_node_id,
        payload_content_hash=tampered_notification.content_hash,
    )

    with pytest.raises(SignatureVerificationError):
        service.receive_correction_notification(
            envelope,
            this_node_id=this_node_id,
            trusted_notifying_practice_id=practice.id,
            ring=ring,
            already_processed_envelope=_never_processed,
            already_processed_notification=_never_processed,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
            at=_NOTIFICATION_SENT_AT,
        )

    assert object_repository.get_latest(local_dependent.id).body["status"] == "under_review"


def test_receive_adversarial_replayed_notification_is_rejected() -> None:
    service, _, object_repository, edge_repository, _ = _services()
    practice, private_key, key_id = _notifying_practice_fixture()
    this_node_id = new_urn("node")
    ring = practice_key_ring(practice)

    notified_object_id = new_urn("claim")
    local_dependent = _claim(status="under_review")
    _seed(object_repository, local_dependent)
    _seed_dependency_edge(
        edge_repository, dependent_id=local_dependent.id, dependency_id=notified_object_id
    )

    notification = _build_signed_notification(
        notifying_practice_id=practice.id,
        key_id=key_id,
        private_key=private_key,
        recipient_practice_id=new_urn("practice"),
        notified_object_ids=[notified_object_id],
    )
    envelope = _build_signed_envelope(
        notification,
        sender_practice_id=practice.id,
        key_id=key_id,
        private_key=private_key,
        recipient_node_id=this_node_id,
    )

    def _already_seen(notification_id: str) -> bool:
        assert notification_id == notification.notification_id
        return True

    with pytest.raises(CorrectionNotificationAlreadyProcessedError) as excinfo:
        service.receive_correction_notification(
            envelope,
            this_node_id=this_node_id,
            trusted_notifying_practice_id=practice.id,
            ring=ring,
            already_processed_envelope=_never_processed,
            already_processed_notification=_already_seen,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
            at=_NOTIFICATION_SENT_AT,
        )
    assert excinfo.value.notification_id == notification.notification_id
    assert object_repository.get_latest(local_dependent.id).body["status"] == "under_review"


def test_receive_adversarial_envelope_level_replay_is_rejected_before_notification_checks() -> None:
    """The ENVELOPE's own ``message_id`` replay check (existing,
    UNCHANGED ``validate_inbound_envelope`` — the FIRST of the two
    independent replay layers task-packets/E6-T03.yaml's own invariant
    names) rejects a CorrectionNotification-carrying envelope exactly as it
    would any other payload kind, and does so BEFORE the notification-level
    ``already_processed_notification`` predicate is ever consulted — a
    replayed envelope never reaches the notification's own signature/
    window/replay checks at all, so no local impact recomputation runs.
    """
    service, _, object_repository, edge_repository, _ = _services()
    practice, private_key, key_id = _notifying_practice_fixture()
    this_node_id = new_urn("node")
    ring = practice_key_ring(practice)

    notified_object_id = new_urn("claim")
    local_dependent = _claim(status="under_review")
    _seed(object_repository, local_dependent)
    _seed_dependency_edge(
        edge_repository, dependent_id=local_dependent.id, dependency_id=notified_object_id
    )

    notification = _build_signed_notification(
        notifying_practice_id=practice.id,
        key_id=key_id,
        private_key=private_key,
        recipient_practice_id=new_urn("practice"),
        notified_object_ids=[notified_object_id],
    )
    envelope = _build_signed_envelope(
        notification,
        sender_practice_id=practice.id,
        key_id=key_id,
        private_key=private_key,
        recipient_node_id=this_node_id,
    )

    def _envelope_already_seen(message_id: str) -> bool:
        assert message_id == envelope.message_id
        return True

    def _notification_predicate_must_not_be_called(notification_id: str) -> bool:
        raise AssertionError(
            "already_processed_notification must not be consulted once the envelope's "
            "own replay check has already rejected the message"
        )

    with pytest.raises(EnvelopeAlreadyProcessedError) as excinfo:
        service.receive_correction_notification(
            envelope,
            this_node_id=this_node_id,
            trusted_notifying_practice_id=practice.id,
            ring=ring,
            already_processed_envelope=_envelope_already_seen,
            already_processed_notification=_notification_predicate_must_not_be_called,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
            at=_NOTIFICATION_SENT_AT,
        )
    assert excinfo.value.message_id == envelope.message_id
    # No impact recomputation side effect: the locally-dependent claim's
    # status is exactly what it was before this call.
    assert object_repository.get_latest(local_dependent.id).body["status"] == "under_review"


def test_receive_adversarial_notification_outside_its_own_validity_window_is_rejected() -> None:
    """The notification's OWN ``[sent_at, expires_at)`` window is a SECOND,
    independent validity check from the wrapping envelope's own (task-
    packets/E6-T03.yaml derived_decisions (b)) — evaluating at an instant
    at/after the notification's own ``expires_at`` (but still within the
    envelope's own, separately-set, later window) must fail closed.
    """
    service, _, object_repository, edge_repository, _ = _services()
    practice, private_key, key_id = _notifying_practice_fixture()
    this_node_id = new_urn("node")
    ring = practice_key_ring(practice)

    notified_object_id = new_urn("claim")
    local_dependent = _claim(status="under_review")
    _seed(object_repository, local_dependent)
    _seed_dependency_edge(
        edge_repository, dependent_id=local_dependent.id, dependency_id=notified_object_id
    )

    notification = _build_signed_notification(
        notifying_practice_id=practice.id,
        key_id=key_id,
        private_key=private_key,
        recipient_practice_id=new_urn("practice"),
        notified_object_ids=[notified_object_id],
    )
    # The envelope's OWN window is set independently, and later, so only the
    # notification's own window check is what fails here.
    envelope_expires_at = _NOTIFICATION_EXPIRES_AT + timedelta(minutes=30)
    envelope = _build_signed_envelope(
        notification,
        sender_practice_id=practice.id,
        key_id=key_id,
        private_key=private_key,
        recipient_node_id=this_node_id,
        expires_at=envelope_expires_at,
    )

    with pytest.raises(CorrectionNotificationNotWithinValidityWindowError) as excinfo:
        service.receive_correction_notification(
            envelope,
            this_node_id=this_node_id,
            trusted_notifying_practice_id=practice.id,
            ring=ring,
            already_processed_envelope=_never_processed,
            already_processed_notification=_never_processed,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
            at=_NOTIFICATION_EXPIRES_AT,
        )
    assert excinfo.value.notification_id == notification.notification_id
    assert object_repository.get_latest(local_dependent.id).body["status"] == "under_review"


def test_receive_adversarial_notification_about_unknown_object_yields_empty_impact() -> None:
    """A notified_object_id with zero local edges_to entries of any kind
    yields an explicit, empty locally-impacted set and zero claim
    transitions — a legitimate outcome, not an error and not a crash
    (MRR-NFR-012).
    """
    service, _, object_repository, edge_repository, _ = _services()
    practice, private_key, key_id = _notifying_practice_fixture()
    this_node_id = new_urn("node")
    ring = practice_key_ring(practice)

    unknown_object_id = new_urn("claim")  # never locally stored, no edges reference it

    notification = _build_signed_notification(
        notifying_practice_id=practice.id,
        key_id=key_id,
        private_key=private_key,
        recipient_practice_id=new_urn("practice"),
        notified_object_ids=[unknown_object_id],
    )
    envelope = _build_signed_envelope(
        notification,
        sender_practice_id=practice.id,
        key_id=key_id,
        private_key=private_key,
        recipient_node_id=this_node_id,
    )

    impact = service.receive_correction_notification(
        envelope,
        this_node_id=this_node_id,
        trusted_notifying_practice_id=practice.id,
        ring=ring,
        already_processed_envelope=_never_processed,
        already_processed_notification=_never_processed,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
        at=_NOTIFICATION_SENT_AT,
    )

    assert impact.locally_impacted_object_ids == frozenset()


# ---------------------------------------------------------------------------
# record_response() (task-packets/E6-T04.yaml): the RECEIVING practice's own
# local accept/adapt/reject/defer disposition toward an already-received
# CorrectionNotification.
# ---------------------------------------------------------------------------


def _never_responded(_correction_notification_id: str) -> bool:
    return False


def _response_kwargs(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "correction_notification_id": new_urn("correction-notification"),
        "notifying_practice_id": new_urn("practice"),
        "origin_correction_event_id": new_urn("correction"),
        "origin_correction_event_revision": 1,
        "notified_object_ids": [new_urn("claim")],
        "responding_practice_id": new_urn("practice"),
        "decision": "accept",
        "already_responded": _never_responded,
        "actor": _ACTOR,
        "policy_version": _POLICY_VERSION,
        "correlation_id": _correlation_id(),
    }
    data.update(overrides)
    return data


def test_record_response_happy_path_accept_records_response_and_event_no_edge() -> None:
    service, _, object_repository, edge_repository, event_log = _services_with_response()

    stored = service.record_response(**_response_kwargs(decision="accept"))

    assert stored.revision == 1
    assert stored.body["decision"] == "accept"
    assert "reason" not in stored.body or stored.body["reason"] is None
    assert object_repository.get_latest(stored.id).body["kind"] == "CorrectionResponse"
    assert edge_repository.edges_from(stored.id) == []

    events = [e.event for e in event_log.read_all() if e.event.object_id == stored.id]
    assert [e.event_type for e in events] == ["correction.response_recorded"]
    assert events[0].causation_id is None


@pytest.mark.parametrize("decision", ["reject", "defer"])
def test_record_response_happy_path_reject_or_defer_records_response_and_event_no_edge(
    decision: str,
) -> None:
    service, _, object_repository, edge_repository, event_log = _services_with_response()

    stored = service.record_response(
        **_response_kwargs(decision=decision, reason="Fixture reason for rejecting/deferring.")
    )

    assert stored.revision == 1
    assert stored.body["decision"] == decision
    assert stored.body["reason"] == "Fixture reason for rejecting/deferring."
    assert edge_repository.edges_from(stored.id) == []
    events = [e.event for e in event_log.read_all() if e.event.object_id == stored.id]
    assert [e.event_type for e in events] == ["correction.response_recorded"]


def test_record_response_happy_path_adapt_single_adaptation_records_one_corrects_edge() -> None:
    service, _, object_repository, edge_repository, event_log = _services_with_response()
    adapted = _claim()
    _seed(object_repository, adapted)
    notified_object_id = new_urn("claim")

    stored = service.record_response(
        **_response_kwargs(
            decision="adapt",
            notified_object_ids=[notified_object_id],
            adaptations=[
                CorrectionResponseAdaptation(
                    adapted_object_id=adapted.id, notified_object_id=notified_object_id
                )
            ],
        )
    )

    assert stored.revision == 1
    assert stored.body["decision"] == "adapt"
    edges = edge_repository.edges_from(adapted.id, "corrects")
    assert len(edges) == 1
    assert edges[0].target_id == notified_object_id
    assert edges[0].edge_type == "corrects"

    events = [e.event for e in event_log.read_all() if e.event.object_id == stored.id]
    assert [e.event_type for e in events] == ["correction.response_recorded"]


def test_record_response_happy_path_adapt_multiple_adaptations_records_n_corrects_edges() -> None:
    service, _, object_repository, edge_repository, event_log = _services_with_response()
    adapted_one = _claim()
    adapted_two = _claim()
    _seed(object_repository, adapted_one)
    _seed(object_repository, adapted_two)
    notified_one = new_urn("claim")
    notified_two = new_urn("claim")

    stored = service.record_response(
        **_response_kwargs(
            decision="adapt",
            notified_object_ids=[notified_one, notified_two],
            adaptations=[
                CorrectionResponseAdaptation(
                    adapted_object_id=adapted_one.id, notified_object_id=notified_one
                ),
                CorrectionResponseAdaptation(
                    adapted_object_id=adapted_two.id, notified_object_id=notified_two
                ),
            ],
        )
    )

    assert stored.revision == 1
    assert len(edge_repository.edges_from(adapted_one.id, "corrects")) == 1
    assert len(edge_repository.edges_from(adapted_two.id, "corrects")) == 1
    events = [
        e.event
        for e in event_log.read_all()
        if e.event.event_type == "correction.response_recorded"
    ]
    assert len(events) == 1


def test_record_response_adversarial_missing_adapted_object_id_raises_and_persists_nothing() -> (
    None
):
    """A missing adapted_object_id aborts the whole call — no
    CorrectionResponse, no edge, and no event are persisted — even when
    another entry in the same adaptations list references a valid object.
    """
    service, _, object_repository, edge_repository, event_log = _services_with_response()
    valid_adapted = _claim()
    _seed(object_repository, valid_adapted)
    missing_adapted_id = new_urn("claim")  # never seeded
    notified_one = new_urn("claim")
    notified_two = new_urn("claim")

    with pytest.raises(ObjectNotFoundError):
        service.record_response(
            **_response_kwargs(
                decision="adapt",
                notified_object_ids=[notified_one, notified_two],
                adaptations=[
                    CorrectionResponseAdaptation(
                        adapted_object_id=valid_adapted.id, notified_object_id=notified_one
                    ),
                    CorrectionResponseAdaptation(
                        adapted_object_id=missing_adapted_id, notified_object_id=notified_two
                    ),
                ],
            )
        )

    assert edge_repository.edges_from(valid_adapted.id, "corrects") == []
    assert event_log.read_all() == []


def test_record_response_adversarial_adaptation_not_a_member_of_notified_object_ids_raises() -> (
    None
):
    service, _, object_repository, edge_repository, event_log = _services_with_response()
    adapted = _claim()
    _seed(object_repository, adapted)
    notified_object_id = new_urn("claim")
    unrelated_object_id = new_urn("claim")  # NOT in notified_object_ids

    with pytest.raises(ValidationError):
        service.record_response(
            **_response_kwargs(
                decision="adapt",
                notified_object_ids=[notified_object_id],
                adaptations=[
                    CorrectionResponseAdaptation(
                        adapted_object_id=adapted.id, notified_object_id=unrelated_object_id
                    )
                ],
            )
        )

    assert edge_repository.edges_from(adapted.id, "corrects") == []
    assert event_log.read_all() == []


def test_record_response_adversarial_duplicate_response_raises_before_any_write() -> None:
    service, _, object_repository, edge_repository, event_log = _services_with_response()
    notification_id = new_urn("correction-notification")
    other_notification_id = new_urn("correction-notification")

    def _already_responded(candidate_id: str) -> bool:
        return candidate_id == notification_id

    with pytest.raises(CorrectionResponseAlreadyRecordedError) as excinfo:
        service.record_response(
            **_response_kwargs(
                correction_notification_id=notification_id,
                decision="accept",
                already_responded=_already_responded,
            )
        )
    assert excinfo.value.correction_notification_id == notification_id
    assert event_log.read_all() == []

    # A call for a DIFFERENT correction_notification_id is unaffected.
    stored = service.record_response(
        **_response_kwargs(
            correction_notification_id=other_notification_id,
            decision="accept",
            already_responded=_already_responded,
        )
    )
    assert stored.body["correction_notification_id"] == other_notification_id


def test_record_response_never_invokes_correction_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After any ``record_response`` call, no ``CorrectionEvent`` object
    anywhere in the local object store changes revision (there is none to
    change — the receiving practice never stores one), and
    ``CORRECTION_LIFECYCLE.can_transition``/``assert_transition`` is never
    invoked by this task's own code path — verified here by monkeypatching
    both to raise if called at all, since there is no CorrectionEvent
    instance locally to assert a revision count against.
    """
    service, _, object_repository, _, _ = _services_with_response()

    def _must_not_be_called(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("CORRECTION_LIFECYCLE must not be consulted by record_response")

    # CORRECTION_LIFECYCLE is a frozen, slotted dataclass instance — patch
    # the shared StateMachine CLASS's methods (reverted automatically at the
    # end of this test) rather than the instance, which has no per-instance
    # __dict__/slot to override a method through.
    monkeypatch.setattr(type(CORRECTION_LIFECYCLE), "can_transition", _must_not_be_called)
    monkeypatch.setattr(type(CORRECTION_LIFECYCLE), "assert_transition", _must_not_be_called)

    stored = service.record_response(**_response_kwargs(decision="accept"))

    assert stored.revision == 1
    # No CorrectionEvent was ever created, read, or mutated by this call.
    with pytest.raises(ObjectNotFoundError):
        object_repository.get_latest(new_urn("correction"))


def test_record_response_without_record_revision_with_edges_dependency_raises_valueerror() -> None:
    service, _, _, _, event_log = _services()  # no record_revision_with_edges wired

    with pytest.raises(ValueError, match="record_revision_with_edges"):
        service.record_response(**_response_kwargs(decision="accept"))

    assert event_log.read_all() == []
