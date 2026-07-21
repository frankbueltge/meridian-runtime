"""Unit tests for ``mrr.services.claim.service.ClaimService``
(task-packets/E3-T02.yaml), run entirely DB-free against in-memory fakes of
``mrr.domain.repositories.ObjectRepository``/``EdgeRepository`` and the
event-log read surface the service depends on — no PostgreSQL, no
``sqlalchemy.Engine``. The atomic "write a revision/edge + an event
together" steps are stood in for by small fake "unit of work" functions
(``_fake_record``/``_fake_record_edge``) with the same
``RecordRevisionWithEvent``/``RecordEdgeWithEvent`` shapes
``bind_unit_of_work``/``bind_edge_unit_of_work`` produce in production —
mirroring ``tests/unit/services/research_score/test_service.py``'s own
``_fake_record`` pattern.

Acceptance-test mapping (task-packets/E3-T02.yaml, unit tier):

- "a supported claim without evidence or without verification is rejected
  (the packet's headline gate)" ->
  ``test_to_supported_without_evidence_relations_raises_and_persists_nothing``,
  ``test_to_supported_without_verification_ids_raises_and_persists_nothing``.
- "creating a claim persists it (draft) with one event; a legal transition
  to under_review then supported (with evidence + verification refs +
  support edges) succeeds" -> ``test_create_persists_revision_1_and_created_event``,
  ``test_full_legal_lifecycle_draft_to_under_review_to_supported_succeeds``.
- "an illegal CLAIM_LIFECYCLE transition persists nothing" ->
  ``test_illegal_transition_raises_and_persists_nothing``.
- "evidence relations are stored as typed edges of a valid vocabulary type;
  an invalid edge type is rejected" ->
  ``test_add_evidence_edge_succeeds_with_a_valid_vocabulary_type``,
  ``test_add_evidence_edge_invalid_type_rejected_and_persists_nothing``.
- "two materially different claims remain separate objects linked by a
  typed edge, not merged" ->
  ``test_link_related_claim_keeps_two_claims_separate_objects``.
- event provenance -> ``test_transition_event_carries_complete_provenance``.

The "transitions and edge writes persist atomically with their events" test
is at the integration tier (real PostgreSQL) —
tests/integration/services/claim/test_service.py.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from mrr.contracts import Claim
from mrr.domain.exceptions import (
    ClaimCeilingExceededError,
    ClaimNotFoundError,
    GatedEdgeTypeError,
    InvalidKillDecisionError,
    InvalidTransitionError,
    MissingSupportEdgeError,
    ObjectNotFoundError,
    RevisionConflictError,
    UnknownEdgeTypeError,
)
from mrr.domain.identity import new_urn
from mrr.domain.repositories import EDGE_VOCABULARY, StoredObject, TypedEdge
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.claim.service import (
    GATED_EDGE_TYPES,
    ClaimService,
    RecordEdgeWithEvent,
    RecordRevisionWithEvent,
)
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# In-memory fakes (ObjectRepository/EdgeRepository protocol conformance plus
# a minimal event journal), and fake "units of work" combining them.
# ---------------------------------------------------------------------------


class FakeObjectRepository:
    """In-memory stand-in for ``mrr.domain.repositories.ObjectRepository`` —
    identical in spirit to every other service test's own fake (e.g.
    ``tests/unit/services/research_score/test_service.py``): enforces the
    same optimistic-concurrency contract ``PostgresObjectRepository`` does.
    """

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
    """In-memory stand-in for ``mrr.domain.repositories.EdgeRepository``.
    ``add_edge`` enforces the same ``EDGE_VOCABULARY`` fail-closed check
    ``PostgresEdgeRepository`` does, so a service bug that skips the
    vocabulary check fails the test loudly instead of silently succeeding
    against a lenient fake.
    """

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
    """In-memory stand-in for the ``read_all``-only event journal the
    service depends on (``mrr.services.claim.service._EventJournal``).
    ``append_for_test`` is not part of that protocol — it is only what the
    fake units-of-work below call to record an event.
    """

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


def _fake_record_edge(
    edge_repository: FakeEdgeRepository, event_log: FakeEventLog
) -> RecordEdgeWithEvent:
    """A "lightweight fake unit-of-work" for edges — the same
    ``RecordEdgeWithEvent`` shape ``bind_edge_unit_of_work`` produces over a
    real Postgres transaction, but backed by the two in-memory fakes above.
    """

    def _record_edge(edge: TypedEdge, event: DomainEvent) -> tuple[TypedEdge, AppendedEvent]:
        stored_edge = edge_repository.add_edge(edge)
        appended = event_log.append_for_test(event)
        return stored_edge, appended

    return _record_edge


def _service() -> tuple[ClaimService, FakeObjectRepository, FakeEventLog, FakeEdgeRepository]:
    object_repository = FakeObjectRepository()
    event_log = FakeEventLog()
    edge_repository = FakeEdgeRepository()
    service = ClaimService(
        object_repository,
        event_log,
        edge_repository,
        _fake_record(object_repository, event_log),
        _fake_record_edge(edge_repository, event_log),
    )
    return service, object_repository, event_log, edge_repository


# ---------------------------------------------------------------------------
# Claim fixture factory and the matching StoredObject converter (mirrors
# mrr.services.claim.service._claim_to_stored_object, duplicated locally
# rather than imported since it is a private module helper — tests seed the
# fake repository the same way the service itself would persist a claim,
# without going through service.create(), so tests can seed arbitrary
# statuses directly).
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


def _stored_object_from_claim(claim: Claim) -> StoredObject:
    body: dict[str, Any] = json.loads(claim.model_dump_json(exclude_none=True))
    return StoredObject(
        id=claim.id,
        api_version=claim.api_version,
        kind=claim.kind,
        practice_id=claim.practice_id,
        revision=claim.revision,
        created_at=claim.created_at,
        created_by=claim.created_by,
        content_hash=claim.content_hash,
        supersedes=claim.supersedes,
        labels=claim.labels,
        body=body,
    )


def _seed(object_repository: FakeObjectRepository, claim: Claim) -> StoredObject:
    return object_repository.insert_revision(
        _stored_object_from_claim(claim), expected_current_revision=None
    )


# ---------------------------------------------------------------------------
# K1-T02: MethodRuling/MethodProtocol/MethodProfile/ResearchDecision seed
# helpers. This packet introduces no MethodProtocolService/MethodRulingService
# /ResearchDecisionService (deliberately deferred to task-packets/K1-T03.yaml)
# — every prerequisite object these tests need is constructed directly and
# inserted via the generic FakeObjectRepository, bypassing any service,
# mirroring how this codebase's own tests already seed cross-referenced
# prerequisite objects wherever the object under test is not the System
# Under Test. Bodies below carry only the fields ClaimService's own K1-T02
# code paths actually read (`ruled_ceiling`/`protocol_id`,
# `profile_id`/`max_claim_ceiling`, `decision_type`) — the repository layer
# itself never schema-validates a StoredObject.body.
# ---------------------------------------------------------------------------


def _seed_generic(
    object_repository: FakeObjectRepository, *, kind: str, body: dict[str, Any]
) -> str:
    object_id = new_urn(kind.lower())
    obj = StoredObject(
        id=object_id,
        api_version="mrr/v1alpha1",
        kind=kind,
        practice_id=new_urn("practice"),
        revision=1,
        created_at=datetime.now(UTC),
        created_by=new_urn("agent-role"),
        content_hash="sha256:" + "c" * 64,
        supersedes=None,
        labels=None,
        body={"id": object_id, **body},
    )
    object_repository.insert_revision(obj, expected_current_revision=None)
    return object_id


def _seed_method_profile(object_repository: FakeObjectRepository, *, max_claim_ceiling: str) -> str:
    return _seed_generic(
        object_repository,
        kind="MethodProfile",
        body={"max_claim_ceiling": max_claim_ceiling},
    )


def _seed_method_protocol(object_repository: FakeObjectRepository, *, profile_id: str) -> str:
    return _seed_generic(
        object_repository,
        kind="MethodProtocol",
        body={"profile_id": profile_id},
    )


def _seed_method_ruling(
    object_repository: FakeObjectRepository, *, protocol_id: str, ruled_ceiling: str
) -> str:
    return _seed_generic(
        object_repository,
        kind="MethodRuling",
        body={"protocol_id": protocol_id, "ruled_ceiling": ruled_ceiling},
    )


def _seed_ruling_under_profile(
    object_repository: FakeObjectRepository, *, ruled_ceiling: str, profile_max_ceiling: str
) -> str:
    """Seed a full MethodProfile -> MethodProtocol -> MethodRuling chain and
    return the MethodRuling's own id — the one identifier
    ``ClaimService.attach_ruling``/``apply_kill_condition`` callers need.
    """
    profile_id = _seed_method_profile(object_repository, max_claim_ceiling=profile_max_ceiling)
    protocol_id = _seed_method_protocol(object_repository, profile_id=profile_id)
    return _seed_method_ruling(
        object_repository, protocol_id=protocol_id, ruled_ceiling=ruled_ceiling
    )


def _seed_research_decision(object_repository: FakeObjectRepository, *, decision_type: str) -> str:
    return _seed_generic(
        object_repository,
        kind="ResearchDecision",
        body={"decision_type": decision_type},
    )


def _reseed_method_ruling_revision(
    object_repository: FakeObjectRepository,
    ruling_id: str,
    *,
    protocol_id: str,
    ruled_ceiling: str,
) -> None:
    """Insert a SECOND revision for an EXISTING MethodRuling id — simulates
    test-only "drift" (task-packets/K1-T02.yaml acceptance_tests: "a Claim
    with an already-attached ruled_by edge whose target MethodRuling is
    later re-seeded (test-only, simulating drift) to violate the
    profile-max check"). Real MethodRuling objects are revisioned too, so
    this mirrors an ordinary later revision rather than mutating the
    existing row in place.
    """
    existing = object_repository.get_latest(ruling_id)
    obj = StoredObject(
        id=ruling_id,
        api_version=existing.api_version,
        kind=existing.kind,
        practice_id=existing.practice_id,
        revision=existing.revision + 1,
        created_at=datetime.now(UTC),
        created_by=new_urn("agent-role"),
        content_hash="sha256:" + "d" * 64,
        supersedes=None,
        labels=None,
        body={"id": ruling_id, "protocol_id": protocol_id, "ruled_ceiling": ruled_ceiling},
    )
    object_repository.insert_revision(obj, expected_current_revision=existing.revision)


_ACTOR = new_urn("agent-role")
_POLICY_VERSION = "policy-2026-07-01"


def _correlation_id() -> str:
    return new_urn("claim-run")


# ---------------------------------------------------------------------------
# create(): initial-status/revision guardrails and the happy path.
# ---------------------------------------------------------------------------


def test_create_rejects_non_draft_initial_status() -> None:
    service, object_repository, event_log, _ = _service()
    claim = _claim(status="under_review")

    with pytest.raises(InvalidTransitionError) as excinfo:
        service.create(
            claim, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
        )

    assert excinfo.value.to_state == "under_review"
    assert object_repository.list_revisions(claim.id) == []
    assert event_log.read_all() == []


def test_create_rejects_wrong_initial_revision_number() -> None:
    service, _, _, _ = _service()
    claim = _claim(status="draft", revision=2)

    with pytest.raises(ValueError, match="revision must be 1"):
        service.create(
            claim, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
        )


def test_create_persists_revision_1_and_created_event() -> None:
    service, object_repository, event_log, _ = _service()
    claim = _claim(status="draft")
    correlation_id = _correlation_id()

    stored = service.create(
        claim, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    assert stored.revision == 1
    assert stored.body["status"] == "draft"
    assert object_repository.get_latest(claim.id).id == claim.id

    events = event_log.read_all()
    assert len(events) == 1
    assert events[0].event.event_type == "claim.created"
    assert events[0].event.causation_id is None
    assert events[0].event.correlation_id == correlation_id


# ---------------------------------------------------------------------------
# Illegal transitions fail closed and persist nothing.
# ---------------------------------------------------------------------------


def test_illegal_transition_raises_and_persists_nothing() -> None:
    service, object_repository, event_log, _ = _service()
    claim = _claim(status="draft")
    _seed(object_repository, claim)

    # draft -> supported skips under_review; not a drawn CLAIM_LIFECYCLE edge.
    with pytest.raises(InvalidTransitionError) as excinfo:
        service.to_supported(
            claim.id,
            evidence_relations=[new_urn("evidence-anchor")],
            verification_ids=[new_urn("verification")],
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )

    assert excinfo.value.machine == "Claim"
    assert excinfo.value.from_state == "draft"
    assert excinfo.value.to_state == "supported"
    assert [rev.revision for rev in object_repository.list_revisions(claim.id)] == [1]
    assert event_log.read_all() == []


def test_illegal_transition_from_a_terminal_status_raises() -> None:
    service, object_repository, event_log, _ = _service()
    claim = _claim(status="withdrawn")
    _seed(object_repository, claim)

    with pytest.raises(InvalidTransitionError):
        service.require_review(
            claim.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
        )
    assert [rev.revision for rev in object_repository.list_revisions(claim.id)] == [1]
    assert event_log.read_all() == []


def test_transition_on_a_missing_claim_raises_claim_not_found_error() -> None:
    service, _, _, _ = _service()
    missing_id = new_urn("claim")

    with pytest.raises(ClaimNotFoundError) as excinfo:
        service.submit_for_review(
            missing_id,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )
    assert excinfo.value.claim_id == missing_id


# ---------------------------------------------------------------------------
# The headline gate: to_supported requires evidence AND verification, AND a
# matching typed 'supports' edge for every evidence_relations URN.
# ---------------------------------------------------------------------------


def test_to_supported_without_evidence_relations_raises_and_persists_nothing() -> None:
    service, object_repository, event_log, _ = _service()
    claim = _claim(status="under_review")
    _seed(object_repository, claim)

    with pytest.raises(ValidationError, match="evidence_relations"):
        service.to_supported(
            claim.id,
            evidence_relations=[],
            verification_ids=[new_urn("verification")],
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )
    assert [rev.revision for rev in object_repository.list_revisions(claim.id)] == [1]
    assert event_log.read_all() == []


def test_to_supported_without_verification_ids_raises_and_persists_nothing() -> None:
    service, object_repository, event_log, edge_repository = _service()
    claim = _claim(status="under_review")
    _seed(object_repository, claim)
    anchor_id = new_urn("evidence-anchor")
    service.add_evidence_edge(
        claim.id,
        anchor_id,
        "supports",
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    events_before = list(event_log.read_all())

    with pytest.raises(ValidationError, match="verification_ids"):
        service.to_supported(
            claim.id,
            evidence_relations=[anchor_id],
            verification_ids=[],
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )
    # Only the prior evidence-edge revision/event exist; nothing new landed.
    assert [rev.revision for rev in object_repository.list_revisions(claim.id)] == [1]
    assert event_log.read_all() == events_before
    assert len(edge_repository.edges_from(claim.id, "supports")) == 1


def test_to_supported_missing_support_edge_raises_and_persists_nothing() -> None:
    service, object_repository, event_log, _ = _service()
    claim = _claim(status="under_review")
    _seed(object_repository, claim)
    unbacked_anchor_id = new_urn("evidence-anchor")

    # No add_evidence_edge call for unbacked_anchor_id — no 'supports' edge exists.
    with pytest.raises(MissingSupportEdgeError) as excinfo:
        service.to_supported(
            claim.id,
            evidence_relations=[unbacked_anchor_id],
            verification_ids=[new_urn("verification")],
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )
    assert excinfo.value.claim_id == claim.id
    assert excinfo.value.missing_targets == [unbacked_anchor_id]
    assert [rev.revision for rev in object_repository.list_revisions(claim.id)] == [1]
    assert event_log.read_all() == []


def test_full_legal_lifecycle_draft_to_under_review_to_supported_succeeds() -> None:
    service, object_repository, event_log, edge_repository = _service()
    claim = _claim(status="draft")
    correlation_id = _correlation_id()
    anchor_id = new_urn("evidence-anchor")
    verification_id = new_urn("verification")

    service.create(
        claim, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    service.submit_for_review(
        claim.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    service.add_evidence_edge(
        claim.id,
        anchor_id,
        "supports",
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )
    stored = service.to_supported(
        claim.id,
        evidence_relations=[anchor_id],
        verification_ids=[verification_id],
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    assert stored.revision == 3
    assert stored.body["status"] == "supported"
    assert stored.body["evidence_relations"] == [anchor_id]
    assert stored.body["verification_ids"] == [verification_id]
    # The resulting revision is itself a fully valid Claim (contract re-check).
    Claim.model_validate(stored.body)

    assert [rev.body["status"] for rev in object_repository.list_revisions(claim.id)] == [
        "draft",
        "under_review",
        "supported",
    ]
    support_edges = edge_repository.edges_from(claim.id, "supports")
    assert [e.target_id for e in support_edges] == [anchor_id]

    events = [appended.event.event_type for appended in event_log.read_all()]
    assert events == [
        "claim.created",
        "claim.submitted_for_review",
        "claim.evidence_edge_added",
        "claim.supported",
    ]


@pytest.mark.parametrize(
    ("method_name", "expected_status", "expected_event_type"),
    [
        ("to_contested", "contested", "claim.contested"),
        ("to_contradicted", "contradicted", "claim.contradicted"),
        ("to_unresolved", "unresolved", "claim.unresolved"),
        ("to_unsupported", "unsupported", "claim.unsupported"),
    ],
)
def test_under_review_terminal_transitions(
    method_name: str, expected_status: str, expected_event_type: str
) -> None:
    service, object_repository, event_log, _ = _service()
    claim = _claim(status="under_review")
    _seed(object_repository, claim)

    method = getattr(service, method_name)
    stored = method(
        claim.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    assert stored.revision == 2
    assert stored.body["status"] == expected_status
    events = event_log.read_all()
    assert len(events) == 1
    assert events[0].event.event_type == expected_event_type


@pytest.mark.parametrize(
    ("method_name", "expected_status"),
    [
        ("require_review", "review_required"),
        ("withdraw", "withdrawn"),
        ("supersede", "superseded"),
    ],
)
@pytest.mark.parametrize(
    "from_status",
    ["draft", "under_review", "contested", "contradicted", "unresolved", "unsupported"],
)
def test_universal_rules_from_any_nonterminal_status(
    method_name: str, expected_status: str, from_status: str
) -> None:
    service, object_repository, _, _ = _service()
    claim = _claim(status=from_status)
    _seed(object_repository, claim)

    method = getattr(service, method_name)
    stored = method(
        claim.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    assert stored.body["status"] == expected_status


# ---------------------------------------------------------------------------
# Typed edges: valid vocabulary type succeeds; invalid type is rejected.
# ---------------------------------------------------------------------------


def test_add_evidence_edge_succeeds_with_a_valid_vocabulary_type() -> None:
    service, object_repository, event_log, edge_repository = _service()
    claim = _claim(status="under_review")
    _seed(object_repository, claim)
    target_id = new_urn("evidence-anchor")

    edge = service.add_evidence_edge(
        claim.id,
        target_id,
        "supports",
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert edge.source_id == claim.id
    assert edge.target_id == target_id
    assert edge.edge_type == "supports"
    assert edge_repository.edges_from(claim.id) == [edge]
    events = event_log.read_all()
    assert len(events) == 1
    assert events[0].event.event_type == "claim.evidence_edge_added"
    assert events[0].event.object_revision == 1  # no claim revision was bumped


def test_add_evidence_edge_invalid_type_rejected_and_persists_nothing() -> None:
    service, object_repository, event_log, edge_repository = _service()
    claim = _claim(status="under_review")
    _seed(object_repository, claim)

    with pytest.raises(UnknownEdgeTypeError) as excinfo:
        service.add_evidence_edge(
            claim.id,
            new_urn("evidence-anchor"),
            "not-a-real-edge-type",
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )
    assert excinfo.value.edge_type == "not-a-real-edge-type"
    assert edge_repository.edges_from(claim.id) == []
    assert event_log.read_all() == []


def test_add_evidence_edge_invalid_type_checked_before_claim_existence() -> None:
    """An invalid edge_type is rejected even for a claim_id that does not
    exist — the vocabulary check happens first (see
    ``ClaimService._write_edge``'s docstring)."""
    service, _, _, _ = _service()
    missing_claim_id = new_urn("claim")

    with pytest.raises(UnknownEdgeTypeError):
        service.add_evidence_edge(
            missing_claim_id,
            new_urn("evidence-anchor"),
            "not-a-real-edge-type",
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )


def test_add_evidence_edge_on_missing_claim_raises_claim_not_found_error() -> None:
    service, _, _, _ = _service()
    missing_claim_id = new_urn("claim")

    with pytest.raises(ClaimNotFoundError) as excinfo:
        service.add_evidence_edge(
            missing_claim_id,
            new_urn("evidence-anchor"),
            "supports",
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )
    assert excinfo.value.claim_id == missing_claim_id


@pytest.mark.parametrize("gated_edge_type", sorted(GATED_EDGE_TYPES))
def test_add_evidence_edge_rejects_gated_edge_types_and_persists_nothing(
    gated_edge_type: str,
) -> None:
    """K1-T02 review follow-up: `ruled_by`/`decided_by` are ordinary
    EDGE_VOCABULARY members now, so without this gate this generic writer
    would silently accept either one directly, bypassing attach_ruling's
    ceiling check / apply_kill_condition's kill-decision licensing check
    entirely.
    """
    service, object_repository, event_log, edge_repository = _service()
    claim = _claim(status="under_review")
    _seed(object_repository, claim)

    with pytest.raises(GatedEdgeTypeError) as excinfo:
        service.add_evidence_edge(
            claim.id,
            new_urn("some-target"),
            gated_edge_type,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )
    assert excinfo.value.edge_type == gated_edge_type
    assert edge_repository.edges_from(claim.id) == []
    assert event_log.read_all() == []


def test_add_evidence_edge_rejects_gated_edge_type_before_claim_existence() -> None:
    """Mirrors test_add_evidence_edge_invalid_type_checked_before_claim_existence:
    the gated-edge-type check happens even for a claim_id that does not
    exist — it is checked before claim_id is ever resolved.
    """
    service, _, _, _ = _service()
    missing_claim_id = new_urn("claim")

    with pytest.raises(GatedEdgeTypeError):
        service.add_evidence_edge(
            missing_claim_id,
            new_urn("method-ruling"),
            "ruled_by",
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )


def test_add_counterevidence_edge_uses_contradicts_type() -> None:
    service, object_repository, _, edge_repository = _service()
    claim = _claim(status="under_review")
    _seed(object_repository, claim)
    target_id = new_urn("evidence-anchor")

    edge = service.add_counterevidence_edge(
        claim.id,
        target_id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    assert edge.edge_type == "contradicts"
    assert edge_repository.edges_from(claim.id, "contradicts") == [edge]


def test_add_dependency_edge_uses_depends_on_type() -> None:
    service, object_repository, _, edge_repository = _service()
    claim = _claim(status="under_review")
    _seed(object_repository, claim)
    target_id = new_urn("claim")

    edge = service.add_dependency_edge(
        claim.id,
        target_id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    assert edge.edge_type == "depends_on"
    assert edge_repository.edges_from(claim.id, "depends_on") == [edge]


# ---------------------------------------------------------------------------
# MRR-FR-066: two materially different claims stay separate objects, linked
# by a typed edge rather than merged.
# ---------------------------------------------------------------------------


def test_link_related_claim_keeps_two_claims_separate_objects() -> None:
    service, object_repository, event_log, edge_repository = _service()
    claim_a = _claim(status="under_review", assertion="Population A shows effect X at time T1.")
    claim_b = _claim(
        status="under_review", assertion="Population B shows effect X at a different time T2."
    )
    _seed(object_repository, claim_a)
    _seed(object_repository, claim_b)

    edge = service.link_related_claim(
        claim_a.id,
        claim_b.id,
        "qualifies",
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert edge.source_id == claim_a.id
    assert edge.target_id == claim_b.id
    assert edge.edge_type == "qualifies"

    # Both claims remain fully independent, unmerged, unmodified objects.
    stored_a = object_repository.get_latest(claim_a.id)
    stored_b = object_repository.get_latest(claim_b.id)
    assert stored_a.id != stored_b.id
    assert stored_a.revision == 1
    assert stored_b.revision == 1
    assert stored_a.body["assertion"] == claim_a.assertion
    assert stored_b.body["assertion"] == claim_b.assertion

    assert edge_repository.edges_from(claim_a.id) == [edge]
    events = event_log.read_all()
    assert len(events) == 1
    assert events[0].event.event_type == "claim.related_claim_linked"


@pytest.mark.parametrize("gated_edge_type", sorted(GATED_EDGE_TYPES))
def test_link_related_claim_rejects_gated_edge_types_and_persists_nothing(
    gated_edge_type: str,
) -> None:
    """K1-T02 review follow-up: see
    test_add_evidence_edge_rejects_gated_edge_types_and_persists_nothing's
    identical rationale — link_related_claim is the OTHER generic,
    arbitrary-edge_type entry point this gate closes.
    """
    service, object_repository, event_log, edge_repository = _service()
    claim_a = _claim(status="under_review")
    claim_b = _claim(status="under_review")
    _seed(object_repository, claim_a)
    _seed(object_repository, claim_b)

    with pytest.raises(GatedEdgeTypeError) as excinfo:
        service.link_related_claim(
            claim_a.id,
            claim_b.id,
            gated_edge_type,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )
    assert excinfo.value.edge_type == gated_edge_type
    assert edge_repository.edges_from(claim_a.id) == []
    assert event_log.read_all() == []


# ---------------------------------------------------------------------------
# Event provenance completeness (MRR-NFR-001) and the causation chain.
# ---------------------------------------------------------------------------


def test_transition_event_carries_complete_provenance() -> None:
    service, _, event_log, _ = _service()
    claim = _claim(status="draft")
    correlation_id = _correlation_id()

    service.create(
        claim, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    stored = service.submit_for_review(
        claim.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    events = [appended.event for appended in event_log.read_all()]
    assert len(events) == 2
    created_event, submitted_event = events

    assert created_event.causation_id is None
    assert submitted_event.causation_id == created_event.id

    for event in (created_event, submitted_event):
        assert event.actor == _ACTOR
        assert event.policy_version == _POLICY_VERSION
        assert event.correlation_id == correlation_id
        assert event.object_id == claim.id
        assert event.occurred_at.tzinfo is not None

    assert created_event.object_revision == 1
    assert submitted_event.object_revision == 2
    assert stored.revision == 2
    assert stored.body["status"] == "under_review"


# ---------------------------------------------------------------------------
# K1-T02: the claim-ceiling gate — attach_ruling (MRR-MTH-004/005/006).
# ---------------------------------------------------------------------------


def test_attach_ruling_causal_claim_below_causal_tier_raises_and_persists_nothing() -> None:
    """[re-pins method-causal-claim-gate.feature, MRR-MTH-004/006]"""
    service, object_repository, event_log, edge_repository = _service()
    claim = _claim(status="under_review", claim_type="causal")
    _seed(object_repository, claim)
    ruling_id = _seed_ruling_under_profile(
        object_repository,
        ruled_ceiling="associational_adjusted",
        profile_max_ceiling="causal_bounded",
    )

    with pytest.raises(ClaimCeilingExceededError) as excinfo:
        service.attach_ruling(
            claim.id,
            ruling_id,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )

    assert excinfo.value.error_code == "CLAIM_CEILING_EXCEEDED"
    assert excinfo.value.claim_id == claim.id
    assert edge_repository.edges_from(claim.id, "ruled_by") == []
    assert event_log.read_all() == []


def test_attach_ruling_causal_claim_at_causal_bounded_succeeds() -> None:
    service, object_repository, event_log, edge_repository = _service()
    claim = _claim(status="under_review", claim_type="causal")
    _seed(object_repository, claim)
    ruling_id = _seed_ruling_under_profile(
        object_repository, ruled_ceiling="causal_bounded", profile_max_ceiling="causal_bounded"
    )

    edge = service.attach_ruling(
        claim.id,
        ruling_id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert edge.source_id == claim.id
    assert edge.target_id == ruling_id
    assert edge.edge_type == "ruled_by"
    assert edge_repository.edges_from(claim.id, "ruled_by") == [edge]
    events = event_log.read_all()
    assert len(events) == 1
    assert events[0].event.event_type == "claim.ruling_attached"


def test_attach_ruling_ruling_exceeding_profile_max_raises_but_a_licensed_ruling_under_same_profile_succeeds() -> (  # noqa: E501
    None
):
    """[re-pins MRR-MTH-006 for the first real profile]"""
    service, object_repository, _, _ = _service()
    claim = _claim(status="under_review", claim_type="observational")
    _seed(object_repository, claim)
    violating_ruling_id = _seed_ruling_under_profile(
        object_repository,
        ruled_ceiling="causal_bounded",
        profile_max_ceiling="associational_unadjusted",
    )

    with pytest.raises(ClaimCeilingExceededError):
        service.attach_ruling(
            claim.id,
            violating_ruling_id,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )

    licensed_ruling_id = _seed_ruling_under_profile(
        object_repository,
        ruled_ceiling="descriptive",
        profile_max_ceiling="associational_unadjusted",
    )
    edge = service.attach_ruling(
        claim.id,
        licensed_ruling_id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    assert edge.target_id == licensed_ruling_id


def test_attach_ruling_on_a_missing_claim_raises_claim_not_found_error() -> None:
    service, object_repository, _, _ = _service()
    ruling_id = _seed_ruling_under_profile(
        object_repository, ruled_ceiling="descriptive", profile_max_ceiling="descriptive"
    )
    missing_claim_id = new_urn("claim")

    with pytest.raises(ClaimNotFoundError):
        service.attach_ruling(
            missing_claim_id,
            ruling_id,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )


# ---------------------------------------------------------------------------
# K1-T02: the _transition ceiling re-check (defense in depth).
# ---------------------------------------------------------------------------


def test_transition_ceiling_recheck_catches_a_ruling_re_seeded_to_violate() -> None:
    """[defense-in-depth re-check]"""
    service, object_repository, _, _ = _service()
    claim = _claim(status="under_review", claim_type="observational")
    _seed(object_repository, claim)
    profile_id = _seed_method_profile(
        object_repository, max_claim_ceiling="associational_unadjusted"
    )
    protocol_id = _seed_method_protocol(object_repository, profile_id=profile_id)
    ruling_id = _seed_method_ruling(
        object_repository, protocol_id=protocol_id, ruled_ceiling="descriptive"
    )

    service.attach_ruling(
        claim.id,
        ruling_id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    # Test-only drift: the SAME ruling id is later re-seeded (a new
    # revision) to a ceiling that now exceeds the profile's own max.
    _reseed_method_ruling_revision(
        object_repository, ruling_id, protocol_id=protocol_id, ruled_ceiling="causal_bounded"
    )

    with pytest.raises(ClaimCeilingExceededError):
        service.to_contested(
            claim.id,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )
    # attach_ruling never bumps the claim's own revision (edge-only write);
    # the rejected to_contested call persisted nothing on top of that.
    assert [rev.revision for rev in object_repository.list_revisions(claim.id)] == [1]


def test_transition_with_no_ruled_by_edge_is_unaffected_by_the_ceiling_recheck() -> None:
    """Explicit regression check (not merely "unchanged by omission"): the
    IDENTICAL call against a claim with NO ruled_by edge at all transitions
    exactly as every existing E3-T02 test already proves.
    """
    service, object_repository, _, _ = _service()
    claim = _claim(status="under_review")
    _seed(object_repository, claim)

    stored = service.to_contested(
        claim.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    assert stored.body["status"] == "contested"


# ---------------------------------------------------------------------------
# K1-T02: kill-condition transition plumbing (MRR-MTH-010).
# ---------------------------------------------------------------------------


def test_apply_kill_condition_transitions_to_withdrawn_and_writes_decided_by_edge() -> None:
    """[re-pins method-kill-condition-propagation.feature, MRR-MTH-010]"""
    service, object_repository, event_log, edge_repository = _service()
    claim = _claim(status="under_review")
    _seed(object_repository, claim)
    decision_id = _seed_research_decision(object_repository, decision_type="kill_branch")

    stored, edge = service.apply_kill_condition(
        claim.id,
        decision_id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert stored.body["status"] == "withdrawn"
    assert edge.source_id == claim.id
    assert edge.target_id == decision_id
    assert edge.edge_type == "decided_by"

    events = [appended.event for appended in event_log.read_all()]
    kill_event = next(e for e in events if e.event_type == "claim.kill_condition_triggered")
    assert kill_event.payload["code"] == "KILL_CONDITION_TRIGGERED"
    assert kill_event.payload["research_decision_id"] == decision_id
    assert kill_event.payload["from_status"] == "under_review"
    assert kill_event.payload["to_status"] == "withdrawn"

    decision_event = next(e for e in events if e.event_type == "claim.kill_decision_recorded")
    assert decision_event.object_id == claim.id

    assert edge_repository.edges_from(claim.id, "decided_by") == [edge]
    # "Killed branches remain addressable and inspectable" (MRR-MTH-010).
    assert object_repository.get_latest(claim.id).body["status"] == "withdrawn"
    assert [rev.revision for rev in object_repository.list_revisions(claim.id)] == [1, 2]
    assert object_repository.get_revision(claim.id, 1).body["status"] == "under_review"


def test_apply_kill_condition_against_wrong_object_kind_raises_and_persists_nothing() -> None:
    service, object_repository, event_log, edge_repository = _service()
    claim = _claim(status="under_review")
    _seed(object_repository, claim)
    not_a_decision = _seed(object_repository, _claim(status="draft"))

    with pytest.raises(InvalidKillDecisionError) as excinfo:
        service.apply_kill_condition(
            claim.id,
            not_a_decision.id,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )
    assert excinfo.value.actual_kind == "Claim"
    assert [rev.revision for rev in object_repository.list_revisions(claim.id)] == [1]
    assert edge_repository.edges_from(claim.id, "decided_by") == []
    assert event_log.read_all() == []


@pytest.mark.parametrize(
    "decision_type",
    ["continue", "revise", "narrow_scope", "replicate", "escalate_human_review"],
)
def test_apply_kill_condition_against_a_non_kill_branch_decision_type_raises(
    decision_type: str,
) -> None:
    service, object_repository, event_log, _ = _service()
    claim = _claim(status="under_review")
    _seed(object_repository, claim)
    decision_id = _seed_research_decision(object_repository, decision_type=decision_type)

    with pytest.raises(InvalidKillDecisionError):
        service.apply_kill_condition(
            claim.id,
            decision_id,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )
    assert [rev.revision for rev in object_repository.list_revisions(claim.id)] == [1]
    assert event_log.read_all() == []


def test_apply_kill_condition_against_an_already_terminal_claim_raises_invalid_transition_error() -> (  # noqa: E501
    None
):
    service, object_repository, event_log, _ = _service()
    claim = _claim(status="withdrawn")
    _seed(object_repository, claim)
    decision_id = _seed_research_decision(object_repository, decision_type="kill_branch")

    with pytest.raises(InvalidTransitionError):
        service.apply_kill_condition(
            claim.id,
            decision_id,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )
    assert [rev.revision for rev in object_repository.list_revisions(claim.id)] == [1]
    assert event_log.read_all() == []


# ---------------------------------------------------------------------------
# K1-T02 review follow-up: gating GATED_EDGE_TYPES on the two generic
# writers must NOT affect the dedicated gated methods themselves — both
# call the internal, unrestricted `_write_edge` helper directly.
# ---------------------------------------------------------------------------


def test_gated_methods_still_write_their_own_edge_types_unaffected_by_the_generic_edge_gate() -> (
    None
):
    """Explicit regression check (not merely "unchanged by omission"):
    attach_ruling still writes a real `ruled_by` edge and apply_kill_condition
    still writes a real `decided_by` edge — the GATED_EDGE_TYPES restriction
    added to add_evidence_edge/link_related_claim does not reach the
    internal _write_edge helper those two gated methods call directly.
    """
    service, object_repository, _, edge_repository = _service()
    claim = _claim(status="under_review", claim_type="causal")
    _seed(object_repository, claim)
    ruling_id = _seed_ruling_under_profile(
        object_repository, ruled_ceiling="causal_bounded", profile_max_ceiling="causal_bounded"
    )

    ruled_by_edge = service.attach_ruling(
        claim.id,
        ruling_id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    assert ruled_by_edge.edge_type == "ruled_by"
    assert edge_repository.edges_from(claim.id, "ruled_by") == [ruled_by_edge]

    decision_id = _seed_research_decision(object_repository, decision_type="kill_branch")
    _, decided_by_edge = service.apply_kill_condition(
        claim.id,
        decision_id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    assert decided_by_edge.edge_type == "decided_by"
    assert edge_repository.edges_from(claim.id, "decided_by") == [decided_by_edge]
