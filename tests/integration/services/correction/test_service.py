"""Integration tests for
``mrr.services.correction.service.CorrectionImpactService`` (task-packets/
E3-T06.yaml), run against a real PostgreSQL via the ``postgres_engine``
fixture in tests/integration/conftest.py — wired exactly as production code
would: ``PostgresObjectRepository``/``PostgresEdgeRepository``/
``PostgresEventLog`` over the fixture's engine, with a real
``mrr.services.claim.service.ClaimService`` injected (never reimplemented).
Skips visibly if ``MRR_TEST_DATABASE_URL`` is unset (fails hard instead if
``CI=true``) — see that module's docstring.

Acceptance-test mapping (task-packets/E3-T06.yaml, integration tier):

- "an affected claim gains a review_required revision while its prior status
  remains in list_revisions (integration, real PostgreSQL)" ->
  ``test_dependent_claim_gains_review_required_revision_prior_status_preserved``.
- "re-run is idempotent (no duplicate revisions)" ->
  ``test_repeated_propagate_impact_adds_no_duplicate_revisions``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mrr.contracts import Claim, CorrectionEvent
from mrr.domain.identity import new_urn
from mrr.persistence.repositories import (
    PostgresEdgeRepository,
    PostgresEventLog,
    PostgresObjectRepository,
)
from mrr.services.claim.service import ClaimService
from mrr.services.claim.service import bind_edge_unit_of_work as bind_claim_edge_unit_of_work
from mrr.services.claim.service import bind_unit_of_work as bind_claim_unit_of_work
from mrr.services.correction.service import CorrectionImpactService, bind_unit_of_work
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


def _correction(*, affected_object_ids: list[str], **overrides: Any) -> CorrectionEvent:
    data: dict[str, Any] = {
        "id": new_urn("correction"),
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


def _services_for(
    engine: Engine,
) -> tuple[
    CorrectionImpactService,
    ClaimService,
    PostgresObjectRepository,
    PostgresEdgeRepository,
    PostgresEventLog,
]:
    object_repository = PostgresObjectRepository(engine)
    edge_repository = PostgresEdgeRepository(engine)
    event_log = PostgresEventLog(engine)
    claim_service = ClaimService(
        object_repository,
        event_log,
        edge_repository,
        bind_claim_unit_of_work(engine, object_repository, event_log),
        bind_claim_edge_unit_of_work(engine, event_log),
    )
    correction_service = CorrectionImpactService(
        object_repository,
        edge_repository,
        claim_service,
        event_log,
        bind_unit_of_work(engine, object_repository, event_log),
    )
    return correction_service, claim_service, object_repository, edge_repository, event_log


def test_dependent_claim_gains_review_required_revision_prior_status_preserved(
    postgres_engine: Engine,
) -> None:
    service, claim_service, object_repository, edge_repository, _ = _services_for(postgres_engine)
    actor = new_urn("agent-role")
    correlation_id = new_urn("correction-run")

    root = _claim(status="draft")
    dependent = _claim(status="draft")
    claim_service.create(
        root, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    claim_service.create(
        dependent, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    claim_service.submit_for_review(
        dependent.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    claim_service.add_dependency_edge(
        dependent.id,
        root.id,
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    correction = _correction(affected_object_ids=[root.id])
    service.record(
        correction, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    stored_correction = service.propagate_impact(
        correction.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    assert stored_correction.body["impact_objects"] == [dependent.id]

    dependent_revisions = object_repository.list_revisions(dependent.id)
    # under_review (from submit_for_review) is still present in history,
    # never overwritten — a new review_required revision is appended on top.
    assert [rev.body["status"] for rev in dependent_revisions] == [
        "draft",
        "under_review",
        "review_required",
    ]
    assert object_repository.get_latest(dependent.id).body["status"] == "review_required"

    # The typed dependency edge is queryable both directions.
    assert [e.target_id for e in edge_repository.edges_from(dependent.id, "depends_on")] == [
        root.id
    ]


def test_repeated_propagate_impact_adds_no_duplicate_revisions(postgres_engine: Engine) -> None:
    service, claim_service, object_repository, edge_repository, event_log = _services_for(
        postgres_engine
    )
    actor = new_urn("agent-role")
    correlation_id = new_urn("correction-run")

    root = _claim(status="draft")
    dependent = _claim(status="draft")
    claim_service.create(
        root, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    claim_service.create(
        dependent, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    claim_service.add_dependency_edge(
        dependent.id,
        root.id,
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    correction = _correction(affected_object_ids=[root.id])
    service.record(
        correction, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    first = service.propagate_impact(
        correction.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    dependent_revisions_after_first = object_repository.list_revisions(dependent.id)
    correction_revisions_after_first = object_repository.list_revisions(correction.id)
    events_after_first = [
        appended for appended in event_log.read_all() if appended.event.object_id == correction.id
    ]

    second = service.propagate_impact(
        correction.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    assert first.body["impact_objects"] == second.body["impact_objects"] == [dependent.id]
    assert first.revision == second.revision
    assert object_repository.list_revisions(dependent.id) == dependent_revisions_after_first
    assert object_repository.list_revisions(correction.id) == correction_revisions_after_first
    assert [
        appended for appended in event_log.read_all() if appended.event.object_id == correction.id
    ] == events_after_first
