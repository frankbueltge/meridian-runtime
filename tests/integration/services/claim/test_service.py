"""Integration tests for ``mrr.services.claim.service.ClaimService``
(task-packets/E3-T02.yaml), run against a real PostgreSQL via the
``postgres_engine`` fixture in tests/integration/conftest.py — wired exactly
as production code would: ``PostgresObjectRepository``/``PostgresEventLog``
over the fixture's engine, with ``bind_unit_of_work``/``bind_edge_unit_of_work``
closing over them to produce the atomic ``record``/``record_edge``
dependencies. Skips visibly if ``MRR_TEST_DATABASE_URL`` is unset (fails
hard instead if ``CI=true``) — see that module's docstring.

Acceptance-test mapping (task-packets/E3-T02.yaml, integration tier):

- "full path draft->under_review->supported persists revisions + edges +
  events atomically" ->
  ``test_full_path_persists_revisions_edges_and_events_atomically``.
- "the typed edges are queryable via EdgeRepository (edges_from/edges_to)"
  -> covered by the same test, plus
  ``test_edges_are_queryable_via_edges_from_and_edges_to``.
- "an illegal transition rolls back" ->
  ``test_illegal_transition_persists_no_revision_and_no_event``.
- edge-write atomicity specifically -> ``test_invalid_edge_type_persists_no_edge_and_no_event``.

Acceptance-test mapping (task-packets/K1-T02.yaml, integration tier — the
three re-pinned v0.2.0 Gherkin behaviors NOT pinned at the unit tier only):

- [re-pins method-causal-claim-gate.feature, MRR-MTH-004/006] ->
  ``test_attach_ruling_persists_ruled_by_edge_atomically_against_real_postgres``,
  ``test_attach_ruling_violation_persists_nothing_against_real_postgres``.
- [re-pins method-kill-condition-propagation.feature, MRR-MTH-010] ->
  ``test_apply_kill_condition_persists_withdrawal_and_decided_by_edge_atomically``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from mrr.contracts import Claim
from mrr.domain.exceptions import (
    ClaimCeilingExceededError,
    InvalidTransitionError,
    UnknownEdgeTypeError,
)
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject
from mrr.persistence.repositories import (
    PostgresEdgeRepository,
    PostgresEventLog,
    PostgresObjectRepository,
)
from mrr.services.claim.service import ClaimService, bind_edge_unit_of_work, bind_unit_of_work
from sqlalchemy import Engine

_POLICY_VERSION = "policy-2026-07-01"


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


def _service_for(
    engine: Engine,
) -> tuple[ClaimService, PostgresObjectRepository, PostgresEventLog, PostgresEdgeRepository]:
    object_repository = PostgresObjectRepository(engine)
    event_log = PostgresEventLog(engine)
    edge_repository = PostgresEdgeRepository(engine)
    record = bind_unit_of_work(engine, object_repository, event_log)
    record_edge = bind_edge_unit_of_work(engine, event_log)
    service = ClaimService(object_repository, event_log, edge_repository, record, record_edge)
    return service, object_repository, event_log, edge_repository


# ---------------------------------------------------------------------------
# K1-T02: MethodRuling/MethodProtocol/MethodProfile/ResearchDecision seed
# helper — this packet introduces no MethodProtocolService/MethodRulingService
# (deliberately deferred to task-packets/K1-T03.yaml), so every prerequisite
# object these tests need is constructed directly and inserted via the
# generic PostgresObjectRepository, bypassing any service, exactly mirroring
# tests/unit/services/claim/test_service.py's own identical seed helpers.
# ---------------------------------------------------------------------------


def _seed_generic(
    object_repository: PostgresObjectRepository, *, kind: str, body: dict[str, Any]
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


def _seed_ruling_under_profile(
    object_repository: PostgresObjectRepository,
    *,
    ruled_ceiling: str,
    profile_max_ceiling: str,
) -> str:
    profile_id = _seed_generic(
        object_repository, kind="MethodProfile", body={"max_claim_ceiling": profile_max_ceiling}
    )
    protocol_id = _seed_generic(
        object_repository, kind="MethodProtocol", body={"profile_id": profile_id}
    )
    return _seed_generic(
        object_repository,
        kind="MethodRuling",
        body={"protocol_id": protocol_id, "ruled_ceiling": ruled_ceiling},
    )


def _seed_research_decision(
    object_repository: PostgresObjectRepository, *, decision_type: str
) -> str:
    return _seed_generic(
        object_repository, kind="ResearchDecision", body={"decision_type": decision_type}
    )


def test_full_path_persists_revisions_edges_and_events_atomically(
    postgres_engine: Engine,
) -> None:
    service, object_repository, event_log, edge_repository = _service_for(postgres_engine)
    claim = _claim(status="draft")
    actor = new_urn("agent-role")
    correlation_id = new_urn("claim-run")
    anchor_id = new_urn("evidence-anchor")
    verification_id = new_urn("verification")

    service.create(
        claim, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    service.submit_for_review(
        claim.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    edge = service.add_evidence_edge(
        claim.id,
        anchor_id,
        "supports",
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )
    stored = service.to_supported(
        claim.id,
        evidence_relations=[anchor_id],
        verification_ids=[verification_id],
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    assert stored.revision == 3
    assert stored.body["status"] == "supported"
    assert stored.body["evidence_relations"] == [anchor_id]
    assert stored.body["verification_ids"] == [verification_id]

    revisions = object_repository.list_revisions(claim.id)
    assert [rev.revision for rev in revisions] == [1, 2, 3]
    assert [rev.body["status"] for rev in revisions] == ["draft", "under_review", "supported"]

    # The typed edge is queryable via EdgeRepository, both directions.
    from_results = edge_repository.edges_from(claim.id, "supports")
    assert [e.id for e in from_results] == [edge.id]
    to_results = edge_repository.edges_to(anchor_id, "supports")
    assert [e.id for e in to_results] == [edge.id]

    events = [appended for appended in event_log.read_all() if appended.event.object_id == claim.id]
    assert [appended.event.event_type for appended in events] == [
        "claim.created",
        "claim.submitted_for_review",
        "claim.evidence_edge_added",
        "claim.supported",
    ]
    # The evidence-edge-added event is emitted while the claim sits at revision 2
    # (after submit_for_review): adding an edge is not a new claim revision, so the
    # event faithfully records the claim's current revision (2), not 1.
    for appended, expected_revision in zip(events, [1, 2, 2, 3], strict=True):
        event = appended.event
        assert event.actor == actor
        assert event.policy_version == _POLICY_VERSION
        assert event.correlation_id == correlation_id
        assert event.object_revision == expected_revision
        assert event.occurred_at.tzinfo is not None

    # Provenance completeness (MRR-NFR-001): a real causal chain.
    assert events[0].event.causation_id is None
    for earlier, later in zip(events, events[1:], strict=False):
        assert later.event.causation_id == earlier.event.id


def test_edges_are_queryable_via_edges_from_and_edges_to(postgres_engine: Engine) -> None:
    service, object_repository, _, edge_repository = _service_for(postgres_engine)
    claim_a = _claim(status="draft")
    claim_b = _claim(status="draft")
    actor = new_urn("agent-role")
    correlation_id = new_urn("claim-run")

    service.create(
        claim_a, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    service.create(
        claim_b, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    edge = service.link_related_claim(
        claim_a.id,
        claim_b.id,
        "qualifies",
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    assert [e.id for e in edge_repository.edges_from(claim_a.id, "qualifies")] == [edge.id]
    assert [e.id for e in edge_repository.edges_to(claim_b.id, "qualifies")] == [edge.id]

    # Both claims remain separate, unmerged, addressable objects.
    assert object_repository.get_latest(claim_a.id).revision == 1
    assert object_repository.get_latest(claim_b.id).revision == 1


def test_illegal_transition_persists_no_revision_and_no_event(postgres_engine: Engine) -> None:
    service, object_repository, event_log, _ = _service_for(postgres_engine)
    claim = _claim(status="draft")
    actor = new_urn("agent-role")
    correlation_id = new_urn("claim-run")

    service.create(
        claim, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    # draft -> supported is not a drawn CLAIM_LIFECYCLE edge (must go
    # through under_review first).
    with pytest.raises(InvalidTransitionError):
        service.to_supported(
            claim.id,
            evidence_relations=[new_urn("evidence-anchor")],
            verification_ids=[new_urn("verification")],
            actor=actor,
            policy_version=_POLICY_VERSION,
            correlation_id=correlation_id,
        )

    assert [rev.revision for rev in object_repository.list_revisions(claim.id)] == [1]
    events = [appended for appended in event_log.read_all() if appended.event.object_id == claim.id]
    assert len(events) == 1  # only "created" — the rejected transition wrote nothing


def test_invalid_edge_type_persists_no_edge_and_no_event(postgres_engine: Engine) -> None:
    service, _, event_log, edge_repository = _service_for(postgres_engine)
    claim = _claim(status="draft")
    actor = new_urn("agent-role")
    correlation_id = new_urn("claim-run")

    service.create(
        claim, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    events_before = len(event_log.read_all())

    with pytest.raises(UnknownEdgeTypeError):
        service.add_evidence_edge(
            claim.id,
            new_urn("evidence-anchor"),
            "not-a-real-edge-type",
            actor=actor,
            policy_version=_POLICY_VERSION,
            correlation_id=correlation_id,
        )

    assert edge_repository.edges_from(claim.id) == []
    assert len(event_log.read_all()) == events_before


# ---------------------------------------------------------------------------
# K1-T02: the claim-ceiling gate against real PostgreSQL
# (re-pins method-causal-claim-gate.feature, MRR-MTH-004/006).
# ---------------------------------------------------------------------------


def test_attach_ruling_persists_ruled_by_edge_atomically_against_real_postgres(
    postgres_engine: Engine,
) -> None:
    service, object_repository, event_log, edge_repository = _service_for(postgres_engine)
    claim = _claim(status="draft", claim_type="causal")
    actor = new_urn("agent-role")
    correlation_id = new_urn("claim-run")

    service.create(
        claim, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    service.submit_for_review(
        claim.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    ruling_id = _seed_ruling_under_profile(
        object_repository, ruled_ceiling="causal_bounded", profile_max_ceiling="causal_bounded"
    )

    edge = service.attach_ruling(
        claim.id,
        ruling_id,
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    assert edge.edge_type == "ruled_by"
    assert [e.id for e in edge_repository.edges_from(claim.id, "ruled_by")] == [edge.id]
    events = [
        appended.event
        for appended in event_log.read_all()
        if appended.event.object_id == claim.id
        and appended.event.event_type == "claim.ruling_attached"
    ]
    assert len(events) == 1


def test_attach_ruling_violation_persists_nothing_against_real_postgres(
    postgres_engine: Engine,
) -> None:
    service, object_repository, event_log, edge_repository = _service_for(postgres_engine)
    claim = _claim(status="draft", claim_type="causal")
    actor = new_urn("agent-role")
    correlation_id = new_urn("claim-run")

    service.create(
        claim, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    service.submit_for_review(
        claim.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    events_before = len(event_log.read_all())
    ruling_id = _seed_ruling_under_profile(
        object_repository,
        ruled_ceiling="associational_adjusted",
        profile_max_ceiling="causal_bounded",
    )

    with pytest.raises(ClaimCeilingExceededError):
        service.attach_ruling(
            claim.id,
            ruling_id,
            actor=actor,
            policy_version=_POLICY_VERSION,
            correlation_id=correlation_id,
        )

    assert edge_repository.edges_from(claim.id, "ruled_by") == []
    assert len(event_log.read_all()) == events_before


# ---------------------------------------------------------------------------
# K1-T02: kill-condition transition plumbing against real PostgreSQL
# (re-pins method-kill-condition-propagation.feature, MRR-MTH-010).
# ---------------------------------------------------------------------------


def test_apply_kill_condition_persists_withdrawal_and_decided_by_edge_atomically(
    postgres_engine: Engine,
) -> None:
    service, object_repository, event_log, edge_repository = _service_for(postgres_engine)
    claim = _claim(status="draft")
    actor = new_urn("agent-role")
    correlation_id = new_urn("claim-run")

    service.create(
        claim, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    service.submit_for_review(
        claim.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    decision_id = _seed_research_decision(object_repository, decision_type="kill_branch")

    stored, edge = service.apply_kill_condition(
        claim.id,
        decision_id,
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    assert stored.body["status"] == "withdrawn"
    assert edge.edge_type == "decided_by"
    assert edge.target_id == decision_id

    events = [
        appended.event for appended in event_log.read_all() if appended.event.object_id == claim.id
    ]
    event_types = [e.event_type for e in events]
    assert "claim.kill_condition_triggered" in event_types
    assert "claim.kill_decision_recorded" in event_types

    kill_event = next(e for e in events if e.event_type == "claim.kill_condition_triggered")
    assert kill_event.payload["code"] == "KILL_CONDITION_TRIGGERED"
    assert kill_event.payload["research_decision_id"] == decision_id

    # "Killed branches remain addressable and inspectable" (MRR-MTH-010).
    assert object_repository.get_latest(claim.id).body["status"] == "withdrawn"
    revisions = object_repository.list_revisions(claim.id)
    assert [rev.revision for rev in revisions] == [1, 2, 3]
    assert edge_repository.edges_from(claim.id, "decided_by")[0].id == edge.id
