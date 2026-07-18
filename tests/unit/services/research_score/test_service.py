"""Unit tests for ``mrr.services.research_score.service.ResearchScoreService``
(task-packets/E2-T01.yaml), run entirely DB-free against in-memory fakes of
``mrr.domain.repositories.ObjectRepository`` and the event-log read surface
the service depends on — no PostgreSQL, no ``sqlalchemy.Engine``. The
atomic "write a revision + an event together" step is stood in for by a
small fake "unit of work" function (``_fake_record``) with the same
``RecordRevisionWithEvent`` shape ``bind_unit_of_work`` produces in
production — the packet's own suggested DB-free alternative to fighting
``record_object_revision_with_event``'s concrete Postgres-typed parameters.

Acceptance-test mapping (task-packets/E2-T01.yaml, unit tier):

- "the gate accepts APPROVED/ACTIVE and rejects missing + all other
  statuses with the specific typed errors" ->
  ``test_ensure_can_start_work_raises_score_not_found_for_missing_score``,
  ``test_ensure_can_start_work_rejects_non_startable_statuses``,
  ``test_ensure_can_start_work_accepts_approved_and_active``.
- "approve without approvals is rejected; with one it succeeds" ->
  ``test_approve_without_approval_reference_raises_and_persists_nothing``,
  ``test_approve_with_approval_reference_succeeds``.
- "illegal transitions raise InvalidTransitionError and NOTHING persisted"
  -> ``test_illegal_transition_raises_and_persists_nothing``.
- "create rejects non-DRAFT initial status" ->
  ``test_create_rejects_non_draft_initial_status``.
- "event provenance fields are all populated" ->
  ``test_transition_event_carries_complete_provenance``.

A few additional DB-free tests cover ``revise()``'s own guardrails (status
must not change, revision number must be exactly latest + 1) and the
"revision 1 stays readable and unchanged" invariant at the unit level too —
the packet frames the full material-revision round trip as an integration
test (real PostgreSQL), but the same logic is cheap and valuable to check
against the fake here as well.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from mrr.contracts import ResearchScore
from mrr.domain.exceptions import (
    ApprovalRequiredError,
    InvalidTransitionError,
    ObjectNotFoundError,
    RevisionConflictError,
    ScoreNotApprovedError,
    ScoreNotFoundError,
)
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.research_score.service import RecordRevisionWithEvent, ResearchScoreService

# ---------------------------------------------------------------------------
# In-memory fakes (ObjectRepository protocol conformance + a minimal event
# journal), and a fake "unit of work" combining them.
# ---------------------------------------------------------------------------


class FakeObjectRepository:
    """In-memory stand-in for ``mrr.domain.repositories.ObjectRepository``.
    Enforces the same optimistic-concurrency contract
    ``PostgresObjectRepository`` does (belt check + revision-numbering
    check), so a service bug that computes the wrong expected/next revision
    fails the test loudly instead of silently succeeding against a lenient
    fake.
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


class FakeEventLog:
    """In-memory stand-in for the ``read_all``-only event journal the
    service depends on (``mrr.services.research_score.service._EventJournal``).
    ``append_for_test`` is not part of that protocol — it is only what the
    fake unit-of-work function below calls to record an event.
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
    """A "lightweight fake unit-of-work" (task-packets/E2-T01.yaml's own
    phrase): the same ``RecordRevisionWithEvent`` shape
    ``bind_unit_of_work`` produces over real Postgres classes, but backed by
    the two in-memory fakes above — not atomic in any real transactional
    sense, but sufficient to unit-test the service's business logic (what
    gets built and in what order) without a database.
    """

    def _record(
        obj: StoredObject,
        expected_current_revision: int | None,
        event: DomainEvent,
    ) -> tuple[StoredObject, AppendedEvent]:
        stored = object_repository.insert_revision(obj, expected_current_revision)
        appended = event_log.append_for_test(event)
        return stored, appended

    return _record


def _service() -> tuple[ResearchScoreService, FakeObjectRepository, FakeEventLog]:
    object_repository = FakeObjectRepository()
    event_log = FakeEventLog()
    service = ResearchScoreService(
        object_repository, event_log, _fake_record(object_repository, event_log)
    )
    return service, object_repository, event_log


# ---------------------------------------------------------------------------
# ResearchScore fixture factory and the matching StoredObject converter
# (mirrors mrr.services.research_score.service._score_to_stored_object,
# duplicated locally rather than imported since it is a private module
# helper — tests seed the fake repository the same way the service itself
# would persist a score, without going through service.create(), so tests
# can seed arbitrary statuses directly).
# ---------------------------------------------------------------------------


def _score(*, id: str | None = None, **overrides: Any) -> ResearchScore:
    data: dict[str, Any] = {
        "id": id or new_urn("research-score"),
        "api_version": "mrr/v1alpha1",
        "kind": "ResearchScore",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": datetime.now(UTC),
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "question": "Does this fixture question satisfy the schema's minimum length?",
        "objectives": ["Measure something falsifiable."],
        "non_goals": [],
        "scope": {},
        "methods": {"allowed": [], "prohibited": []},
        "data_classes": ["PUBLIC"],
        "autonomy": {},
        "budgets": {},
        "quality_gates": ["No unsupported claim without independent verification."],
        "stop_conditions": ["Budget exhausted."],
        "publication_policy": {
            "max_disclosure": "INTERNAL",
            "external_publication_requires_approval": True,
        },
        "status": "DRAFT",
        "approvals": [],
    }
    data.update(overrides)
    return ResearchScore.model_validate(data)


def _stored_object_from_score(score: ResearchScore) -> StoredObject:
    body: dict[str, Any] = json.loads(score.model_dump_json(exclude_none=True))
    return StoredObject(
        id=score.id,
        api_version=score.api_version,
        kind=score.kind,
        practice_id=score.practice_id,
        revision=score.revision,
        created_at=score.created_at,
        created_by=score.created_by,
        content_hash=score.content_hash,
        supersedes=score.supersedes,
        labels=score.labels,
        body=body,
    )


def _seed(object_repository: FakeObjectRepository, score: ResearchScore) -> StoredObject:
    return object_repository.insert_revision(
        _stored_object_from_score(score), expected_current_revision=None
    )


_ACTOR = new_urn("agent-role")
_POLICY_VERSION = "policy-2026-07-01"


def _correlation_id() -> str:
    return new_urn("research-run")


# ---------------------------------------------------------------------------
# The MRR-FR-004 gate: ensure_can_start_work.
# ---------------------------------------------------------------------------


def test_ensure_can_start_work_raises_score_not_found_for_missing_score() -> None:
    service, _, _ = _service()
    score_id = new_urn("research-score")

    with pytest.raises(ScoreNotFoundError) as excinfo:
        service.ensure_can_start_work(score_id)
    assert excinfo.value.score_id == score_id


@pytest.mark.parametrize(
    "status",
    ["DRAFT", "IN_REVIEW", "REJECTED", "SUSPENDED", "SUPERSEDED", "ARCHIVED"],
)
def test_ensure_can_start_work_rejects_non_startable_statuses(status: str) -> None:
    service, object_repository, _ = _service()
    score = _score(status=status)
    _seed(object_repository, score)

    with pytest.raises(ScoreNotApprovedError) as excinfo:
        service.ensure_can_start_work(score.id)
    assert excinfo.value.score_id == score.id
    assert excinfo.value.actual_status == status


@pytest.mark.parametrize("status", ["APPROVED", "ACTIVE"])
def test_ensure_can_start_work_accepts_approved_and_active(status: str) -> None:
    service, object_repository, _ = _service()
    score = _score(status=status)
    _seed(object_repository, score)

    result = service.ensure_can_start_work(score.id)
    assert result.body["status"] == status


# ---------------------------------------------------------------------------
# approve(): approval-reference requirement.
# ---------------------------------------------------------------------------


def test_approve_without_approval_reference_raises_and_persists_nothing() -> None:
    service, object_repository, event_log = _service()
    score = _score(status="IN_REVIEW", approvals=[])
    _seed(object_repository, score)

    with pytest.raises(ApprovalRequiredError) as excinfo:
        service.approve(
            score.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
        )
    assert excinfo.value.score_id == score.id
    assert [rev.revision for rev in object_repository.list_revisions(score.id)] == [1]
    assert event_log.read_all() == []


def test_approve_with_approval_reference_succeeds() -> None:
    service, object_repository, event_log = _service()
    score = _score(status="IN_REVIEW", approvals=[new_urn("approval")])
    _seed(object_repository, score)

    stored = service.approve(
        score.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    assert stored.revision == 2
    assert stored.body["status"] == "APPROVED"
    events = event_log.read_all()
    assert len(events) == 1
    assert events[0].event.event_type == "research_score.approved"


# ---------------------------------------------------------------------------
# Illegal transitions fail closed and persist nothing.
# ---------------------------------------------------------------------------


def test_illegal_transition_raises_and_persists_nothing() -> None:
    service, object_repository, event_log = _service()
    score = _score(status="DRAFT")
    _seed(object_repository, score)

    # DRAFT -> APPROVED is not a drawn RESEARCH_SCORE_LIFECYCLE edge.
    with pytest.raises(InvalidTransitionError) as excinfo:
        service.approve(
            score.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
        )

    assert excinfo.value.machine == "ResearchScore"
    assert excinfo.value.from_state == "DRAFT"
    assert excinfo.value.to_state == "APPROVED"
    assert [rev.revision for rev in object_repository.list_revisions(score.id)] == [1]
    assert event_log.read_all() == []


# ---------------------------------------------------------------------------
# create(): rejects a non-DRAFT initial status.
# ---------------------------------------------------------------------------


def test_create_rejects_non_draft_initial_status() -> None:
    service, object_repository, event_log = _service()
    score = _score(status="APPROVED", approvals=[new_urn("approval")])

    with pytest.raises(InvalidTransitionError) as excinfo:
        service.create(
            score, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
        )

    assert excinfo.value.to_state == "APPROVED"
    assert object_repository.list_revisions(score.id) == []
    assert event_log.read_all() == []


def test_create_persists_revision_1_and_created_event() -> None:
    service, object_repository, event_log = _service()
    score = _score(status="DRAFT")
    correlation_id = _correlation_id()

    stored = service.create(
        score, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    assert stored.revision == 1
    assert stored.body["status"] == "DRAFT"
    assert object_repository.get_latest(score.id).id == score.id

    events = event_log.read_all()
    assert len(events) == 1
    assert events[0].event.event_type == "research_score.created"
    assert events[0].event.causation_id is None
    assert events[0].event.correlation_id == correlation_id


def test_create_rejects_wrong_initial_revision_number() -> None:
    service, _, _ = _service()
    score = _score(status="DRAFT", revision=2)

    with pytest.raises(ValueError, match="revision must be 1"):
        service.create(
            score, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
        )


# ---------------------------------------------------------------------------
# Event provenance completeness (MRR-NFR-001) and the causation chain.
# ---------------------------------------------------------------------------


def test_transition_event_carries_complete_provenance() -> None:
    service, _, event_log = _service()
    score = _score(status="DRAFT")
    correlation_id = _correlation_id()

    service.create(
        score, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    stored = service.submit_for_review(
        score.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    events = [appended.event for appended in event_log.read_all()]
    assert len(events) == 2
    created_event, submitted_event = events

    assert created_event.causation_id is None
    # The causal chain: the second event's causation_id names the first
    # event's own id, distinct from correlation_id (stable across both).
    assert submitted_event.causation_id == created_event.id

    for event in (created_event, submitted_event):
        assert event.actor == _ACTOR
        assert event.policy_version == _POLICY_VERSION
        assert event.correlation_id == correlation_id
        assert event.object_id == score.id
        assert event.occurred_at.tzinfo is not None

    assert created_event.object_revision == 1
    assert submitted_event.object_revision == 2
    assert stored.revision == 2
    assert stored.body["status"] == "IN_REVIEW"


# ---------------------------------------------------------------------------
# revise(): guardrails, and the material-change round trip (also covered at
# the integration tier against real PostgreSQL).
# ---------------------------------------------------------------------------


def test_revise_rejects_status_change() -> None:
    service, object_repository, _ = _service()
    score = _score(status="DRAFT")
    _seed(object_repository, score)

    revised = _score(id=score.id, status="IN_REVIEW", revision=2)
    with pytest.raises(ValueError, match="must not change status"):
        service.revise(
            revised, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
        )


def test_revise_rejects_wrong_revision_number() -> None:
    service, object_repository, _ = _service()
    score = _score(status="DRAFT")
    _seed(object_repository, score)

    revised = _score(id=score.id, status="DRAFT", revision=5)
    with pytest.raises(ValueError, match="revision must be"):
        service.revise(
            revised, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
        )


def test_revise_creates_revision_2_and_leaves_revision_1_unchanged() -> None:
    service, object_repository, event_log = _service()
    score = _score(status="DRAFT", question="The original research question, unmodified so far.")
    _seed(object_repository, score)

    revised = _score(
        id=score.id,
        status="DRAFT",
        revision=2,
        question="A materially revised research question, changed on purpose.",
    )
    stored = service.revise(
        revised, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    assert stored.revision == 2
    assert object_repository.get_revision(score.id, 1).body["question"] == score.question
    assert object_repository.get_revision(score.id, 2).body["question"] == revised.question
    events = event_log.read_all()
    assert len(events) == 1
    assert events[0].event.event_type == "research_score.revised"
