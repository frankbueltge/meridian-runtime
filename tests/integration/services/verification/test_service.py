"""Integration tests for ``mrr.services.verification.service.VerificationService``
(task-packets/E3-T04.yaml), run against a real PostgreSQL via the
``postgres_engine`` fixture in tests/integration/conftest.py — wired exactly
as production code would: ``PostgresObjectRepository``/``PostgresEventLog``
over the fixture's engine, with ``bind_unit_of_work``/``bind_edge_unit_of_work``
closing over them, and a real ``mrr.services.claim.service.ClaimService``
sharing the same repositories — matching
``tests/integration/services/claim/test_service.py``'s own wiring.

Acceptance-test mapping (task-packets/E3-T04.yaml, integration tier):

- "recording persists one revision + one event atomically (integration,
  real PostgreSQL)" ->
  ``test_record_persists_revision_one_and_exactly_one_event_atomically``.
- "a failed verification against a real claim transitions it (via the real
  ClaimService) out of supported" ->
  ``test_failed_verification_transitions_a_real_supported_claim_out_of_supported``.
- "conflicting reviews coexist" -> ``test_conflicting_reviews_coexist_in_real_postgresql``.
- self-verification gate against real, persisted claim data ->
  ``test_self_verification_by_proposer_persists_nothing_in_real_postgresql``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from mrr.contracts import Claim, VerificationResult
from mrr.domain.exceptions import SelfVerificationError
from mrr.domain.identity import new_urn
from mrr.persistence.repositories import (
    PostgresEdgeRepository,
    PostgresEventLog,
    PostgresObjectRepository,
)
from mrr.services.claim.service import ClaimService, bind_edge_unit_of_work
from mrr.services.claim.service import bind_unit_of_work as bind_claim_uow
from mrr.services.verification.service import VerificationService, bind_unit_of_work
from sqlalchemy import Engine

from scripts.check_contracts import SCHEMAS_DIR, build_registry, build_validator_for_schema

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
        "rationale": "Fixture rationale for an integration-level VerificationService check.",
        "conflicts_of_interest": [],
        "adjudication_relation": None,
    }
    data.update(overrides)
    return VerificationResult.model_validate(data)


def _kwargs(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "actor": new_urn("agent"),
        "policy_version": _POLICY_VERSION,
        "correlation_id": new_urn("research-run"),
    }
    data.update(overrides)
    return data


def _create_under_review_claim(claim_service: ClaimService, **overrides: Any) -> Claim:
    """Create a real ``draft`` claim then legally transition it to
    ``under_review`` (``ClaimService.create`` only accepts ``draft`` —
    ``CLAIM_LIFECYCLE``'s initial state — so a claim must always be created
    as ``draft`` first, matching ``ClaimService.create``'s own docstring).
    Returns a ``Claim`` snapshot reflecting the ACTUAL persisted
    ``under_review`` state, since ``VerificationService.record`` reads
    ``claim.status``/``claim.proposer_id`` off the object passed to it
    rather than re-fetching from the database itself.
    """
    claim = _claim(status="draft", **overrides)
    claim_service.create(claim, **_kwargs())
    stored = claim_service.submit_for_review(claim.id, **_kwargs())
    return Claim.model_validate(stored.body)


def _services_for(
    engine: Engine,
) -> tuple[VerificationService, ClaimService, PostgresObjectRepository, PostgresEventLog]:
    object_repository = PostgresObjectRepository(engine)
    event_log = PostgresEventLog(engine)
    edge_repository = PostgresEdgeRepository(engine)
    claim_record = bind_claim_uow(engine, object_repository, event_log)
    claim_record_edge = bind_edge_unit_of_work(engine, event_log)
    claim_service = ClaimService(
        object_repository, event_log, edge_repository, claim_record, claim_record_edge
    )
    verification_record = bind_unit_of_work(engine, object_repository, event_log)
    verification_service = VerificationService(verification_record, claim_service)
    return verification_service, claim_service, object_repository, event_log


def test_record_persists_revision_one_and_exactly_one_event_atomically(
    postgres_engine: Engine,
) -> None:
    service, claim_service, object_repository, event_log = _services_for(postgres_engine)
    claim = _create_under_review_claim(claim_service)
    verification = _verification(target_id=claim.id)

    stored = service.record(verification, claim, **_kwargs())

    assert stored.revision == 1
    persisted = object_repository.get_latest(stored.id)
    assert persisted.revision == 1
    assert persisted.body["recommendation"] == "pass"

    events = [a for a in event_log.read_all() if a.event.object_id == stored.id]
    assert len(events) == 1
    assert events[0].event.event_type == "verification.recorded"


def test_read_back_from_database_is_schema_and_pydantic_valid(postgres_engine: Engine) -> None:
    service, claim_service, object_repository, _event_log = _services_for(postgres_engine)
    claim = _create_under_review_claim(claim_service)
    verification = _verification(
        target_id=claim.id,
        verification_type="numeric",
        numeric_recomputation={"recomputed_value": 42.0},
        evidence_inspected=[new_urn("evidence-anchor")],
    )

    stored = service.record(verification, claim, **_kwargs())
    persisted = object_repository.get_latest(stored.id)

    reconstructed = VerificationResult.model_validate(persisted.body)
    assert reconstructed.verification_type == "numeric"

    schema = json.loads((SCHEMAS_DIR / "verification-result.schema.json").read_text())
    registry = build_registry()
    build_validator_for_schema(schema, registry).validate(persisted.body)


def test_self_verification_by_proposer_persists_nothing_in_real_postgresql(
    postgres_engine: Engine,
) -> None:
    service, claim_service, object_repository, event_log = _services_for(postgres_engine)
    proposer_id = new_urn("agent-role")
    claim = _create_under_review_claim(claim_service, proposer_id=proposer_id)
    verification = _verification(target_id=claim.id, reviewer_id=proposer_id)
    events_before = len(event_log.read_all())

    with pytest.raises(SelfVerificationError):
        service.record(verification, claim, **_kwargs())

    assert object_repository.list_revisions(verification.id) == []
    assert len(event_log.read_all()) == events_before


def test_failed_verification_transitions_a_real_supported_claim_out_of_supported(
    postgres_engine: Engine,
) -> None:
    service, claim_service, object_repository, event_log = _services_for(postgres_engine)
    claim = _claim(status="draft")
    claim_service.create(claim, **_kwargs())
    claim_service.submit_for_review(claim.id, **_kwargs())
    anchor_id = new_urn("evidence-anchor")
    claim_service.add_evidence_edge(claim.id, anchor_id, "supports", **_kwargs())
    verification_id_for_claim = new_urn("verification")
    supported = claim_service.to_supported(
        claim.id,
        evidence_relations=[anchor_id],
        verification_ids=[verification_id_for_claim],
        **_kwargs(),
    )
    assert supported.body["status"] == "supported"

    supported_claim = Claim.model_validate(supported.body)
    failing_verification = _verification(target_id=claim.id, recommendation="fail")

    service.record(failing_verification, supported_claim, **_kwargs())

    updated = object_repository.get_latest(claim.id)
    assert updated.body["status"] == "review_required"
    assert updated.body["status"] != "supported"

    claim_events = [
        a.event.event_type for a in event_log.read_all() if a.event.object_id == claim.id
    ]
    assert claim_events[-1] == "claim.review_required"


def test_conflicting_reviews_coexist_in_real_postgresql(postgres_engine: Engine) -> None:
    service, claim_service, object_repository, event_log = _services_for(postgres_engine)
    claim = _create_under_review_claim(claim_service)

    first = _verification(target_id=claim.id, recommendation="pass")
    stored_first = service.record(first, claim, **_kwargs())

    second = _verification(
        target_id=claim.id, recommendation="fail", adjudication_relation=first.id
    )
    stored_second = service.record(second, claim, **_kwargs())

    assert stored_first.id != stored_second.id
    persisted_first = object_repository.get_latest(stored_first.id)
    persisted_second = object_repository.get_latest(stored_second.id)
    assert persisted_first.body["recommendation"] == "pass"
    assert persisted_second.body["recommendation"] == "fail"
    assert persisted_second.body["adjudication_relation"] == first.id

    # The claim itself transitioned once, driven by the second (failing) review.
    updated = object_repository.get_latest(claim.id)
    assert updated.body["status"] == "contested"
