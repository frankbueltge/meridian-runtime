"""Unit tests for ``mrr.services.verification.service.VerificationService``
(task-packets/E3-T04.yaml), run entirely DB-free against in-memory fakes of
``mrr.domain.repositories.ObjectRepository``/``EdgeRepository`` and the
event-log read surface — no PostgreSQL, no ``sqlalchemy.Engine``. A REAL
``mrr.services.claim.service.ClaimService`` is constructed over the same
fakes (never reimplemented, per task-packets/E3-T04.yaml's own instruction
to "inject it; do not reimplement claim transitions") so the
failed-verification-to-claim-status tests exercise the actual
``CLAIM_LIFECYCLE`` transition logic, not a stub. This mirrors
``tests/unit/services/claim/test_service.py``'s own
``FakeObjectRepository``/``FakeEdgeRepository``/``FakeEventLog`` fakes
(duplicated here rather than imported, matching this codebase's own
"private module helper, not shared across test modules" precedent — see
e.g. ``mrr.contracts.source_family``'s docstring for the general norm).

Acceptance-test mapping (task-packets/E3-T04.yaml, unit tier):

- "a verification whose reviewer is the claim's proposer is rejected (self
  verification); likewise the run executor - the headline gate" ->
  ``test_self_verification_by_proposer_rejected_and_persists_nothing``,
  ``test_self_verification_by_run_executor_rejected_and_persists_nothing``.
- "an independent reviewer's verification records successfully with its
  independence profile" -> ``test_independent_reviewer_records_successfully``.
- "a failed verification drives the claim out of any supported/into a
  contested-or-review-required status by the deterministic rule" ->
  ``test_failed_verification_on_supported_claim_drives_to_review_required``,
  ``test_failed_verification_on_under_review_claim_drives_to_contested``.
- "two conflicting reviews are both preserved (no overwrite) with their
  adjudication rationale" -> ``test_two_conflicting_reviews_are_both_preserved``.
- event provenance -> ``test_event_provenance_is_complete_and_causation_is_root``.

The "recording persists one revision + one event atomically (integration,
real PostgreSQL)" test is at the integration tier —
tests/integration/services/verification/test_service.py.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from mrr.contracts import Claim, VerificationResult
from mrr.domain.exceptions import (
    ObjectNotFoundError,
    RevisionConflictError,
    SelfVerificationError,
    UnknownEdgeTypeError,
)
from mrr.domain.identity import new_urn
from mrr.domain.repositories import EDGE_VOCABULARY, StoredObject, TypedEdge
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.claim.service import ClaimService
from mrr.services.verification.service import VerificationService

# ---------------------------------------------------------------------------
# In-memory fakes — identical in spirit to
# tests/unit/services/claim/test_service.py's own fakes, shared here by BOTH
# the ClaimService and the VerificationService under test, matching how a
# single PostgresObjectRepository/PostgresEventLog serves every service in
# production (mrr.persistence.tables' one generic `objects` table).
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
            content_hash=f"sha256:{'d' * 64}",
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


def _services() -> tuple[VerificationService, ClaimService, FakeObjectRepository, FakeEventLog]:
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
    verification_service = VerificationService(
        _fake_record(object_repository, event_log), claim_service
    )
    return verification_service, claim_service, object_repository, event_log


# ---------------------------------------------------------------------------
# Fixture factories.
# ---------------------------------------------------------------------------

_ACTOR = new_urn("agent-role")
_POLICY_VERSION = "policy-2026-07-01"


def _correlation_id() -> str:
    return new_urn("verification-run")


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


def _seed_claim(object_repository: FakeObjectRepository, claim: Claim) -> StoredObject:
    body: dict[str, Any] = json.loads(claim.model_dump_json(exclude_none=True))
    obj = StoredObject(
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
    return object_repository.insert_revision(obj, expected_current_revision=None)


def _independence_profile(**overrides: Any) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "principal": new_urn("person"),
        "model_family": "human-reviewer (no model invoked)",
        "prompt_family": "n/a — manual review checklist v3",
        "retrieval_path": "independent re-fetch via publisher API, not the original crawl",
        "code_path": "independent recomputation script, not the original analysis notebook",
        "data_access_path": "read-only snapshot corpus, separate credential from the proposer's",
    }
    profile.update(overrides)
    return profile


def _verification(
    *, target_id: str, reviewer_id: str | None = None, **overrides: Any
) -> VerificationResult:
    data: dict[str, Any] = {
        "id": new_urn("verification"),
        "api_version": "mrr/v1alpha1",
        "kind": "VerificationResult",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": datetime.now(UTC),
        "created_by": reviewer_id or new_urn("person"),
        "content_hash": "sha256:" + "b" * 64,
        "target_id": target_id,
        "target_kind": "claim",
        "reviewer_id": reviewer_id or new_urn("person"),
        "reviewer_role": "independent reviewer",
        "independence_profile": _independence_profile(),
        "verification_type": "skeptic",
        "checks_performed": ["Searched for counterevidence and alternative explanations"],
        "evidence_inspected": [],
        "numeric_recomputation": None,
        "findings": [],
        "recommendation": "pass",
        "confidence": 0.8,
        "rationale": "Fixture rationale for a unit-level VerificationService check.",
        "conflicts_of_interest": [],
        "adjudication_relation": None,
    }
    data.update(overrides)
    return VerificationResult.model_validate(data)


# ---------------------------------------------------------------------------
# The headline gate: self-verification (proposer or run executor) is
# rejected, and rejection persists nothing.
# ---------------------------------------------------------------------------


def test_self_verification_by_proposer_rejected_and_persists_nothing() -> None:
    service, _claim_service, object_repository, event_log = _services()
    proposer_id = new_urn("agent-role")
    claim = _claim(status="under_review", proposer_id=proposer_id)
    _seed_claim(object_repository, claim)
    verification = _verification(target_id=claim.id, reviewer_id=proposer_id)

    with pytest.raises(SelfVerificationError) as excinfo:
        service.record(
            verification,
            claim,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )

    assert excinfo.value.violated == "proposer"
    assert excinfo.value.reviewer_id == proposer_id
    assert excinfo.value.proposer_id == proposer_id
    assert object_repository.list_revisions(verification.id) == []
    # Only the claim's own seeded revision exists — nothing new landed.
    assert [rev.revision for rev in object_repository.list_revisions(claim.id)] == [1]
    assert event_log.read_all() == []


def test_self_verification_by_run_executor_rejected_and_persists_nothing() -> None:
    service, _claim_service, object_repository, event_log = _services()
    claim = _claim(status="under_review")
    _seed_claim(object_repository, claim)
    executor_id = new_urn("executor")
    verification = _verification(target_id=claim.id, reviewer_id=executor_id)

    with pytest.raises(SelfVerificationError) as excinfo:
        service.record(
            verification,
            claim,
            run_executor_id=executor_id,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )

    assert excinfo.value.violated == "executor"
    assert excinfo.value.reviewer_id == executor_id
    assert excinfo.value.executor_id == executor_id
    assert object_repository.list_revisions(verification.id) == []
    assert event_log.read_all() == []


def test_self_verification_check_runs_even_when_recommendation_is_not_fail() -> None:
    """MRR-FR-070 has no "only when failing" qualifier — a proposer
    self-*passing* their own claim is prohibited exactly like self-failing
    it."""
    service, _claim_service, object_repository, _event_log = _services()
    proposer_id = new_urn("agent-role")
    claim = _claim(status="under_review", proposer_id=proposer_id)
    _seed_claim(object_repository, claim)
    verification = _verification(target_id=claim.id, reviewer_id=proposer_id, recommendation="pass")

    with pytest.raises(SelfVerificationError):
        service.record(
            verification,
            claim,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )


def test_target_id_mismatch_with_claim_rejected() -> None:
    """A caller-supplied ``verification`` whose ``target_id`` does not match
    the ``claim`` passed alongside it is rejected before the self-verification
    gate even runs — otherwise the gate would silently check the WRONG
    claim's proposer_id.
    """
    service, _claim_service, object_repository, event_log = _services()
    claim = _claim(status="under_review")
    _seed_claim(object_repository, claim)
    verification = _verification(target_id=new_urn("claim"))  # a different claim entirely

    with pytest.raises(ValueError, match="target_id"):
        service.record(
            verification,
            claim,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )
    assert object_repository.list_revisions(verification.id) == []
    assert event_log.read_all() == []


# ---------------------------------------------------------------------------
# An independent reviewer's verification records successfully.
# ---------------------------------------------------------------------------


def test_independent_reviewer_records_successfully() -> None:
    service, _claim_service, object_repository, event_log = _services()
    claim = _claim(status="under_review")
    _seed_claim(object_repository, claim)
    verification = _verification(target_id=claim.id)
    correlation_id = _correlation_id()

    stored = service.record(
        verification,
        claim,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    assert stored.revision == 1
    assert stored.id == verification.id
    assert stored.body["independence_profile"] == verification.independence_profile.model_dump()
    assert object_repository.get_latest(verification.id).id == verification.id

    events = [a for a in event_log.read_all() if a.event.object_id == verification.id]
    assert len(events) == 1
    assert events[0].event.event_type == "verification.recorded"
    assert events[0].event.causation_id is None
    assert events[0].event.correlation_id == correlation_id


def test_record_rejects_non_one_revision() -> None:
    service, _claim_service, object_repository, _event_log = _services()
    claim = _claim(status="under_review")
    _seed_claim(object_repository, claim)
    verification = _verification(target_id=claim.id, revision=2)

    with pytest.raises(ValueError, match="revision"):
        service.record(
            verification,
            claim,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )


def test_passing_verification_does_not_change_claim_status() -> None:
    service, _claim_service, object_repository, _event_log = _services()
    claim = _claim(
        status="supported",
        evidence_relations=[new_urn("evidence-anchor")],
        verification_ids=[new_urn("verification")],
    )
    _seed_claim(object_repository, claim)
    verification = _verification(target_id=claim.id, recommendation="pass")

    service.record(
        verification,
        claim,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert object_repository.get_latest(claim.id).revision == 1
    assert object_repository.get_latest(claim.id).body["status"] == "supported"


# ---------------------------------------------------------------------------
# MRR-FR-075: a failed verification drives the claim's status by the
# documented deterministic rule.
# ---------------------------------------------------------------------------


def test_failed_verification_on_supported_claim_drives_to_review_required() -> None:
    service, _claim_service, object_repository, event_log = _services()
    claim = _claim(
        status="supported",
        evidence_relations=[new_urn("evidence-anchor")],
        verification_ids=[new_urn("verification")],
    )
    _seed_claim(object_repository, claim)
    verification = _verification(target_id=claim.id, recommendation="fail")

    service.record(
        verification,
        claim,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    updated_claim = object_repository.get_latest(claim.id)
    assert updated_claim.revision == 2
    assert updated_claim.body["status"] == "review_required"

    claim_events = [
        a.event.event_type for a in event_log.read_all() if a.event.object_id == claim.id
    ]
    assert claim_events == ["claim.review_required"]


def test_failed_verification_on_under_review_claim_drives_to_contested() -> None:
    service, _claim_service, object_repository, event_log = _services()
    claim = _claim(status="under_review")
    _seed_claim(object_repository, claim)
    verification = _verification(target_id=claim.id, recommendation="fail")

    service.record(
        verification,
        claim,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    updated_claim = object_repository.get_latest(claim.id)
    assert updated_claim.revision == 2
    assert updated_claim.body["status"] == "contested"

    claim_events = [
        a.event.event_type for a in event_log.read_all() if a.event.object_id == claim.id
    ]
    assert claim_events == ["claim.contested"]


@pytest.mark.parametrize(
    "status",
    ["draft", "contested", "contradicted", "unresolved", "unsupported", "review_required"],
)
def test_failed_verification_on_other_statuses_leaves_status_unchanged(status: str) -> None:
    """None of these statuses is `supported`, so the packet's own invariant
    ("never leaves a failed-verification claim in supported") is already
    satisfied without acting — no transition is attempted (see the
    service module docstring's documented policy).
    """
    service, _claim_service, object_repository, event_log = _services()
    claim = _claim(status=status)
    _seed_claim(object_repository, claim)
    verification = _verification(target_id=claim.id, recommendation="fail")

    service.record(
        verification,
        claim,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    updated_claim = object_repository.get_latest(claim.id)
    assert updated_claim.revision == 1
    assert updated_claim.body["status"] == status
    claim_events = [a for a in event_log.read_all() if a.event.object_id == claim.id]
    assert claim_events == []


def test_failed_verification_on_terminal_status_does_not_raise() -> None:
    """A withdrawn/superseded claim is already terminal and already not
    `supported` — no transition is attempted, and CLAIM_LIFECYCLE draws no
    outgoing edge from either terminal state anyway (attempting one would
    raise InvalidTransitionError), so the no-op branch is also the only
    SAFE branch here.
    """
    service, _claim_service, object_repository, _event_log = _services()
    claim = _claim(status="withdrawn")
    _seed_claim(object_repository, claim)
    verification = _verification(target_id=claim.id, recommendation="fail")

    service.record(
        verification,
        claim,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert object_repository.get_latest(claim.id).revision == 1


# ---------------------------------------------------------------------------
# MRR-FR-077: two conflicting reviews are both preserved (no overwrite).
# ---------------------------------------------------------------------------


def test_two_conflicting_reviews_are_both_preserved() -> None:
    service, _claim_service, object_repository, event_log = _services()
    claim = _claim(status="under_review")
    _seed_claim(object_repository, claim)

    first = _verification(target_id=claim.id, recommendation="pass", reviewer_id=new_urn("person"))
    stored_first = service.record(
        first,
        claim,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    second = _verification(
        target_id=claim.id,
        recommendation="fail",
        reviewer_id=new_urn("person"),
        adjudication_relation=first.id,
    )
    stored_second = service.record(
        second,
        claim,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    # Both are separate, fully preserved objects — neither overwrote the other.
    assert stored_first.id != stored_second.id
    assert object_repository.get_latest(stored_first.id).body["recommendation"] == "pass"
    assert object_repository.get_latest(stored_second.id).body["recommendation"] == "fail"
    assert object_repository.get_latest(stored_second.id).body["adjudication_relation"] == first.id

    verification_events = {
        a.event.object_id
        for a in event_log.read_all()
        if a.event.event_type == "verification.recorded"
    }
    assert verification_events == {stored_first.id, stored_second.id}


# ---------------------------------------------------------------------------
# Event provenance completeness (MRR-NFR-001).
# ---------------------------------------------------------------------------


def test_event_provenance_is_complete_and_causation_is_root() -> None:
    service, _claim_service, object_repository, event_log = _services()
    claim = _claim(status="under_review")
    _seed_claim(object_repository, claim)
    verification = _verification(target_id=claim.id)
    correlation_id = _correlation_id()

    stored = service.record(
        verification,
        claim,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    events = [a.event for a in event_log.read_all() if a.event.object_id == stored.id]
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "verification.recorded"
    assert event.actor == _ACTOR
    assert event.policy_version == _POLICY_VERSION
    assert event.correlation_id == correlation_id
    assert event.causation_id is None
    assert event.object_revision == 1
    assert event.occurred_at.tzinfo is not None
    assert event.payload["recommendation"] == "pass"


def test_service_exposes_only_record_as_public_method() -> None:
    public_methods = {
        name
        for name in dir(VerificationService)
        if not name.startswith("_") and callable(getattr(VerificationService, name))
    }
    assert public_methods == {"record"}
