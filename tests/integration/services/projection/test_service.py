"""Integration tests for ``mrr.services.projection.service.ProjectionService``
(task-packets/E3-T07.yaml, extended by task-packets/E6-T05.yaml's public
unresolved-correction projection), run against a real PostgreSQL via the
``postgres_engine`` fixture in tests/integration/conftest.py — wired exactly
as production code would: ``PostgresObjectRepository``/
``PostgresEdgeRepository``/``PostgresEventLog`` over the fixture's engine,
with real ``mrr.services.claim.service.ClaimService``/
``mrr.services.correction.service.CorrectionImpactService`` injected (never
reimplemented), matching ``tests/integration/services/correction/
test_service.py``'s identical "construct real upstream services over the
same real repositories" pattern. Skips visibly if ``MRR_TEST_DATABASE_URL``
is unset (fails hard instead if ``CI=true``) — see that module's docstring.

Acceptance-test mapping (task-packets/E6-T05.yaml, integration tier):

- "a critical correction and its flagged dependent claims are built via the
  already-merged ClaimService and CorrectionImpactService (E3-T02/E3-T06),
  then the new build_public_correction_view and build_public_claim_table
  methods are exercised against the real PostgresObjectRepository,
  PostgresEdgeRepository, and PostgresEventLog, confirming redaction behaves
  identically against a real store as against the unit-level fakes" ->
  ``test_public_correction_view_and_claim_table_redact_against_real_postgres``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mrr.contracts import Claim, CorrectionEvent
from mrr.domain.artifacts import Classification
from mrr.domain.identity import new_urn
from mrr.persistence.repositories import (
    PostgresEdgeRepository,
    PostgresEventLog,
    PostgresObjectRepository,
)
from mrr.services.claim.service import ClaimService, bind_edge_unit_of_work, bind_unit_of_work
from mrr.services.correction.service import CorrectionImpactService
from mrr.services.correction.service import bind_unit_of_work as bind_correction_unit_of_work
from mrr.services.projection.service import ProjectionService
from sqlalchemy import Engine

_ACTOR = new_urn("agent-role")
_POLICY_VERSION = "policy-2026-07-01"


def _correlation_id() -> str:
    return new_urn("projection-run")


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


def _services_for(
    engine: Engine,
) -> tuple[ProjectionService, ClaimService, CorrectionImpactService]:
    object_repository = PostgresObjectRepository(engine)
    edge_repository = PostgresEdgeRepository(engine)
    event_log = PostgresEventLog(engine)
    claim_service = ClaimService(
        object_repository,
        event_log,
        edge_repository,
        bind_unit_of_work(engine, object_repository, event_log),
        bind_edge_unit_of_work(engine, event_log),
    )
    correction_service = CorrectionImpactService(
        object_repository,
        edge_repository,
        claim_service,
        event_log,
        bind_correction_unit_of_work(engine, object_repository, event_log),
    )
    projection_service = ProjectionService(object_repository, edge_repository, event_log)
    return projection_service, claim_service, correction_service


def test_public_correction_view_and_claim_table_redact_against_real_postgres(
    postgres_engine: Engine,
) -> None:
    projection_service, claim_service, correction_service = _services_for(postgres_engine)

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

    # --- No attestation: structural facts visible, free text withheld. ---
    unattested_corrections = {
        row.correction_id: row for row in projection_service.build_public_correction_view({})
    }
    assert correction.id in unattested_corrections
    unattested_row = unattested_corrections[correction.id]
    assert unattested_row.severity == correction.severity
    assert unattested_row.status == correction.status
    assert unattested_row.unresolved is True
    assert unattested_row.redacted is True
    assert unattested_row.reason is None
    assert unattested_row.requested_action is None

    unattested_claims = {
        row.claim_id: row for row in projection_service.build_public_claim_table({})
    }
    assert root.id in unattested_claims
    assert dependent.id in unattested_claims
    assert unattested_claims[root.id].flagged is True
    assert unattested_claims[root.id].redacted is True
    assert unattested_claims[root.id].assertion is None
    assert unattested_claims[dependent.id].flagged is True
    assert unattested_claims[dependent.id].redacted is True
    assert unattested_claims[dependent.id].assertion is None

    # --- Full attestation: free text unlocked. ---
    attestation: dict[str, Classification] = {
        correction.id: "PUBLIC",
        root.id: "PUBLIC",
        dependent.id: "PUBLIC",
    }

    attested_corrections = {
        row.correction_id: row
        for row in projection_service.build_public_correction_view(attestation)
    }
    attested_row = attested_corrections[correction.id]
    assert attested_row.redacted is False
    assert attested_row.reason == correction.reason
    assert attested_row.requested_action == correction.requested_action

    attested_claims = {
        row.claim_id: row for row in projection_service.build_public_claim_table(attestation)
    }
    assert attested_claims[root.id].redacted is False
    assert attested_claims[root.id].assertion == root.assertion
    assert attested_claims[dependent.id].redacted is False
    assert attested_claims[dependent.id].assertion == dependent.assertion

    # --- Partial attestation (dependent's own id withheld) fails closed. ---
    partial_attestation: dict[str, Classification] = {
        correction.id: "PUBLIC",
        root.id: "PUBLIC",
        # dependent.id deliberately withheld — dependent is one of the
        # correction's own impact_objects after propagate_impact, so the
        # correction row itself must still redact, and dependent's own claim
        # row must still redact.
    }
    partial_corrections = {
        row.correction_id: row
        for row in projection_service.build_public_correction_view(partial_attestation)
    }
    assert partial_corrections[correction.id].redacted is True
    assert partial_corrections[correction.id].reason is None

    partial_claims = {
        row.claim_id: row
        for row in projection_service.build_public_claim_table(partial_attestation)
    }
    assert partial_claims[dependent.id].redacted is True
    assert partial_claims[dependent.id].assertion is None
    # root's own id and its one flagging correction are both attested, so
    # root's own row is unlocked regardless of dependent's attestation.
    assert partial_claims[root.id].redacted is False
    assert partial_claims[root.id].assertion == root.assertion

    # --- Determinism against the same real Postgres state. ---
    first = projection_service.build_public_correction_view(attestation)
    second = projection_service.build_public_correction_view(attestation)
    assert first == second

    first_claims = projection_service.build_public_claim_table(attestation)
    second_claims = projection_service.build_public_claim_table(attestation)
    assert first_claims == second_claims
