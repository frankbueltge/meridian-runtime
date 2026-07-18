"""Integration tests for
``mrr.services.research_score.service.ResearchScoreService``
(task-packets/E2-T01.yaml), run against a real PostgreSQL via the
``postgres_engine`` fixture in tests/integration/conftest.py — wired exactly
as production code would: ``PostgresObjectRepository``/``PostgresEventLog``
over the fixture's engine, with ``bind_unit_of_work`` closing over all three
to produce the atomic ``record`` dependency. Skips visibly if
``MRR_TEST_DATABASE_URL`` is unset (fails hard instead if ``CI=true``) — see
that module's docstring.

Acceptance-test mapping (task-packets/E2-T01.yaml, integration tier):

- "full lifecycle DRAFT->IN_REVIEW->APPROVED->ACTIVE each persists a
  revision + exactly one event atomically (assert event count and
  provenance from the DB)" ->
  ``test_full_lifecycle_persists_one_revision_and_one_event_per_transition``.
- "a material revise yields revision 2 with revision 1 unchanged" ->
  ``test_material_revise_creates_revision_2_and_leaves_revision_1_unchanged``.
- "an illegal transition rolls back (no new revision, no event)" ->
  ``test_illegal_transition_persists_no_revision_and_no_event``.
- "the gate against a superseded score raises" ->
  ``test_gate_rejects_a_superseded_score``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from mrr.contracts import ResearchScore
from mrr.domain.exceptions import InvalidTransitionError, ScoreNotApprovedError
from mrr.domain.identity import new_urn
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.services.research_score.service import ResearchScoreService, bind_unit_of_work
from sqlalchemy import Engine

_POLICY_VERSION = "policy-2026-07-01"


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


def _service_for(
    engine: Engine,
) -> tuple[ResearchScoreService, PostgresObjectRepository, PostgresEventLog]:
    object_repository = PostgresObjectRepository(engine)
    event_log = PostgresEventLog(engine)
    record = bind_unit_of_work(engine, object_repository, event_log)
    service = ResearchScoreService(object_repository, event_log, record)
    return service, object_repository, event_log


def test_full_lifecycle_persists_one_revision_and_one_event_per_transition(
    postgres_engine: Engine,
) -> None:
    service, object_repository, event_log = _service_for(postgres_engine)
    score = _score(status="DRAFT", approvals=[new_urn("approval")])
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")

    service.create(
        score, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    service.submit_for_review(
        score.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    service.approve(
        score.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    stored = service.activate(
        score.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    assert stored.revision == 4
    assert stored.body["status"] == "ACTIVE"

    revisions = object_repository.list_revisions(score.id)
    assert [rev.revision for rev in revisions] == [1, 2, 3, 4]
    assert [rev.body["status"] for rev in revisions] == ["DRAFT", "IN_REVIEW", "APPROVED", "ACTIVE"]

    events = [appended for appended in event_log.read_all() if appended.event.object_id == score.id]
    assert len(events) == 4  # exactly one event per transition, no more, no fewer
    assert [appended.event.event_type for appended in events] == [
        "research_score.created",
        "research_score.submitted_for_review",
        "research_score.approved",
        "research_score.activated",
    ]

    for appended, expected_revision in zip(events, [1, 2, 3, 4], strict=True):
        event = appended.event
        assert event.actor == actor
        assert event.policy_version == _POLICY_VERSION
        assert event.correlation_id == correlation_id
        assert event.object_id == score.id
        assert event.object_revision == expected_revision
        assert event.occurred_at.tzinfo is not None

    # Provenance completeness (MRR-NFR-001): a real causal chain within this
    # score's own history, distinct from the constant correlation_id above.
    assert events[0].event.causation_id is None
    for earlier, later in zip(events, events[1:], strict=True):
        assert later.event.causation_id == earlier.event.id

    # The gate now accepts the ACTIVE score.
    assert service.ensure_can_start_work(score.id).revision == 4


def test_material_revise_creates_revision_2_and_leaves_revision_1_unchanged(
    postgres_engine: Engine,
) -> None:
    service, object_repository, event_log = _service_for(postgres_engine)
    score = _score(status="DRAFT", question="The original research question, not yet touched.")
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")

    service.create(
        score, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    revised = _score(
        id=score.id,
        status="DRAFT",
        revision=2,
        question="A materially revised research question, changed on purpose here.",
    )
    stored = service.revise(
        revised, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    assert stored.revision == 2

    rev1 = object_repository.get_revision(score.id, 1)
    rev2 = object_repository.get_revision(score.id, 2)
    assert rev1.body["question"] == score.question
    assert rev2.body["question"] == revised.question
    # Revision 1's own record — the row itself — is byte-for-byte untouched.
    assert rev1.content_hash == score.content_hash
    assert rev1.created_at == score.created_at

    events = [appended for appended in event_log.read_all() if appended.event.object_id == score.id]
    assert [appended.event.event_type for appended in events] == [
        "research_score.created",
        "research_score.revised",
    ]


def test_illegal_transition_persists_no_revision_and_no_event(postgres_engine: Engine) -> None:
    service, object_repository, event_log = _service_for(postgres_engine)
    score = _score(status="DRAFT")
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")

    service.create(
        score, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    # DRAFT -> APPROVED is not a drawn RESEARCH_SCORE_LIFECYCLE edge.
    with pytest.raises(InvalidTransitionError):
        service.approve(
            score.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
        )

    assert [rev.revision for rev in object_repository.list_revisions(score.id)] == [1]
    events = [appended for appended in event_log.read_all() if appended.event.object_id == score.id]
    assert len(events) == 1  # only "created" — the rejected approve() wrote nothing


def test_gate_rejects_a_superseded_score(postgres_engine: Engine) -> None:
    service, _, _ = _service_for(postgres_engine)
    score = _score(status="DRAFT", approvals=[new_urn("approval")])
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")

    service.create(
        score, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    service.submit_for_review(
        score.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    service.approve(
        score.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    service.activate(
        score.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    service.supersede(
        score.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    with pytest.raises(ScoreNotApprovedError) as excinfo:
        service.ensure_can_start_work(score.id)
    assert excinfo.value.actual_status == "SUPERSEDED"
