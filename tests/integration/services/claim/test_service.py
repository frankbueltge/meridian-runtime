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
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from mrr.contracts import Claim
from mrr.domain.exceptions import InvalidTransitionError, UnknownEdgeTypeError
from mrr.domain.identity import new_urn
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
    for appended, expected_revision in zip(events, [1, 2, 1, 3], strict=True):
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
