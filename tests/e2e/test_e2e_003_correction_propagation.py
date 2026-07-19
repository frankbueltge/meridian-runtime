"""E2E-003 (E3 scope) — task-packets/E3-T07.yaml, docs/spec/
05_EVALUATION_AND_ACCEPTANCE.md ("E2E-003 Correction propagation"). Builds a
real fixture against PostgreSQL (the ``postgres_engine`` fixture in this
directory's own ``conftest.py``, identical pattern to
``tests/e2e/test_e2e_001_single_node_evidence_loop.py``) by composing the
merged E3 services directly — ``mrr.services.evidence.service.
SourceRecordService``/``EvidenceAnchorService`` (E3-T01),
``mrr.services.claim.service.ClaimService`` (E3-T02),
``mrr.services.correction.service.CorrectionImpactService`` (E3-T06), and
this task's own ``mrr.services.projection.service.ProjectionService``
(E3-T07) — wired exactly as production code would, over the real
``mrr.persistence`` repositories/event log. No CLI orchestration exists yet
that builds claims (``mrr.services.cli.orchestration.run_local_evidence_loop``
stops at sealing an Evidence Crate — E2-T07's own module docstring: "Steps
6-9 ... belong to E3/E8"), so this fixture is composed directly from the
already-merged E3 services, matching
``tests/integration/services/correction/test_service.py``'s own
"``PostgresObjectRepository``/``PostgresEdgeRepository``/``PostgresEventLog``
over the fixture's engine" wiring style, just one tier up (real Postgres,
via the e2e ``postgres_engine`` fixture, not the integration one).

--- Scenario coverage: E3 scope vs. E6/E8 (task-packets/E3-T07.yaml) -----------

docs/spec/05_EVALUATION_AND_ACCEPTANCE.md's E2E-003 has four scenario steps
and three pass criteria:

    1. A supported claim is transferred and used in downstream claims and a
       publication.
    2. Its source is invalidated.
    3. Correction impact traverses all edges.
    4. Recipients respond differently.

    Pass criteria: every dependency is flagged; recipient autonomy is
    preserved; unresolved public correction is visible.

This test covers:

    - Step 1, PARTIALLY: a supported claim IS used in downstream claims (two
      dependent Claims linked via ``depends_on`` edges) — the "transferred"
      and "... a publication" halves are cross-practice transfer (E6) and
      publication bundle/projection-for-external-disclosure (E8,
      MRR-FR-101/102/103), both explicitly out of this task's scope
      (task-packets/E3-T07.yaml forbidden_changes).
    - Step 2, FULLY: a critical, ``source_invalidated`` ``CorrectionEvent``
      names the supported claim's own evidence in ``affected_objects``.
    - Step 3, FULLY: ``CorrectionImpactService.propagate_impact`` (E3-T06,
      merged, composed here as-is) traverses the ``depends_on`` edges to both
      dependents.
    - Step 4, NOT COVERED: "Recipients respond differently" models
      cross-practice recipients and their individual accept/modify/reject
      responses to a delivered correction notification — E6 territory
      (notification/response machinery does not exist yet; task-packets/
      E3-T07.yaml forbidden_changes names this explicitly). There is only one
      practice in this fixture; no recipient response is modeled.

    Pass criteria covered: "every dependency is flagged" (assertion (a) below)
    and "unresolved public correction is visible" (assertions (b)/(c) below,
    via the projection). "Recipient autonomy is preserved" is NOT exercised
    (no recipients exist in this E3-scope fixture at all) — see
    ``mrr.domain.projection``'s own module docstring for why
    ``REJECTED_BY_RECIPIENT`` (the autonomy case) is nonetheless already
    treated as "unresolved" by this task's own FR-095 definition, ready for
    E6 to exercise once recipient responses exist.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mrr.contracts import Claim, CorrectionEvent, EvidenceAnchor, SourceRecord
from mrr.domain.identity import new_urn
from mrr.persistence.repositories import (
    PostgresEdgeRepository,
    PostgresEventLog,
    PostgresObjectRepository,
)
from mrr.services.claim.service import ClaimService
from mrr.services.claim.service import bind_edge_unit_of_work as bind_claim_edge_unit_of_work
from mrr.services.claim.service import bind_unit_of_work as bind_claim_unit_of_work
from mrr.services.correction.service import CorrectionImpactService
from mrr.services.correction.service import bind_unit_of_work as bind_correction_unit_of_work
from mrr.services.evidence.service import EvidenceAnchorService, SourceRecordService
from mrr.services.evidence.service import bind_unit_of_work as bind_evidence_unit_of_work
from mrr.services.projection.service import ProjectionService
from sqlalchemy import Engine

_ACTOR = new_urn("agent-role")
_POLICY_VERSION = "policy-2026-07-01"


def _correlation_id() -> str:
    return new_urn("e2e-003-run")


def _services_for(
    engine: Engine,
) -> tuple[
    ClaimService,
    CorrectionImpactService,
    SourceRecordService,
    EvidenceAnchorService,
    ProjectionService,
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
        bind_correction_unit_of_work(engine, object_repository, event_log),
    )
    evidence_record = bind_evidence_unit_of_work(engine, object_repository, event_log)
    source_record_service = SourceRecordService(evidence_record)
    evidence_anchor_service = EvidenceAnchorService(evidence_record)
    projection_service = ProjectionService(object_repository, edge_repository, event_log)

    return (
        claim_service,
        correction_service,
        source_record_service,
        evidence_anchor_service,
        projection_service,
    )


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
        "assertion": "The supported claim under test in E2E-003's E3-scope fixture.",
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


def _source_record(*, id: str | None = None, **overrides: Any) -> SourceRecord:
    data: dict[str, Any] = {
        "id": id or new_urn("source-record"),
        "api_version": "mrr/v1alpha1",
        "kind": "SourceRecord",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": datetime.now(UTC),
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "d" * 64,
        "identifiers": {"doi": "10.1234/e2e-003-fixture"},
        "title": "E2E-003 fixture source record — later invalidated",
        "creators": ["Fixture Publisher"],
        "retrieval_timestamp": datetime.now(UTC),
        "retrieval_method": "http-get",
        "source_type": "dataset",
        "primary_secondary_derived": "primary",
    }
    data.update(overrides)
    return SourceRecord.model_validate(data)


def _text_evidence_anchor(
    *, id: str | None = None, source_record_id: str, **overrides: Any
) -> EvidenceAnchor:
    data: dict[str, Any] = {
        "id": id or new_urn("evidence-anchor"),
        "api_version": "mrr/v1alpha1",
        "kind": "EvidenceAnchor",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": datetime.now(UTC),
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "f" * 64,
        "relation": "supports",
        "anchor_kind": "text",
        "extraction_method": "manual-quote",
        "extractor_id": new_urn("agent-role"),
        "anchor_validation_status": "validated",
        "source_record_id": source_record_id,
        "snapshot_hash": "sha256:" + "1" * 64,
        "transformation_chain": [],
    }
    data.update(overrides)
    return EvidenceAnchor.model_validate(data)


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
        "reason": "Fixture reason: the publisher withdrew the cited dataset after publication.",
        "evidence_refs": [new_urn("evidence-anchor")],
        "originator_id": new_urn("person"),
        "requested_action": "Review every claim depending on this source.",
        "replacement_object_id": None,
        "status": "OPEN",
        "impact_objects": [],
    }
    data.update(overrides)
    return CorrectionEvent.model_validate(data)


def test_correction_propagation_flags_dependents_and_is_visible_in_the_projection(
    postgres_engine: Engine,
) -> None:
    (
        claim_service,
        correction_service,
        source_record_service,
        evidence_anchor_service,
        projection_service,
    ) = _services_for(postgres_engine)

    # --- Build a supported claim with real evidence provenance. -----------
    source_record = _source_record()
    source_record_service.create(
        source_record,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    anchor = _text_evidence_anchor(source_record_id=source_record.id)
    evidence_anchor_service.create(
        anchor, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    root = _claim(status="draft")
    claim_service.create(
        root, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    claim_service.submit_for_review(
        root.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    claim_service.add_evidence_edge(
        root.id,
        anchor.id,
        "supports",
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    verification_id = new_urn("verification")
    claim_service.to_supported(
        root.id,
        evidence_relations=[anchor.id],
        verification_ids=[verification_id],
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    # --- Downstream dependent claims (step 1's "used in downstream
    # claims" half — transfer/publication are E6/E8, out of scope). --------
    dependent_one = _claim(status="draft")
    dependent_two = _claim(status="draft")
    claim_service.create(
        dependent_one,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    claim_service.create(
        dependent_two,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    claim_service.submit_for_review(
        dependent_one.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    claim_service.submit_for_review(
        dependent_two.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    claim_service.add_dependency_edge(
        dependent_one.id,
        root.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    claim_service.add_dependency_edge(
        dependent_two.id,
        root.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    # --- Step 2: the source is invalidated (a critical CorrectionEvent). ---
    correction = _correction(affected_object_ids=[root.id])
    correction_service.record(
        correction, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    # --- Step 3: correction impact traverses the depends_on edges. --------
    stored_correction = correction_service.propagate_impact(
        correction.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    # (a) Every dependency is flagged review_required.
    assert set(stored_correction.body["impact_objects"]) == {dependent_one.id, dependent_two.id}

    object_repository = PostgresObjectRepository(postgres_engine)
    assert object_repository.get_latest(dependent_one.id).body["status"] == "review_required"
    assert object_repository.get_latest(dependent_two.id).body["status"] == "review_required"
    # The root claim's own status is untouched — its fate runs through the
    # correction's own (E6) resolution workflow, not this propagation step.
    assert object_repository.get_latest(root.id).body["status"] == "supported"

    # (b) The projection's claim table shows the unresolved correction and
    # the flagged claims — MRR-FR-095, "unresolved public correction is
    # visible".
    rows = {row.claim_id: row for row in projection_service.build_claim_table()}

    for flagged_claim_id in (root.id, dependent_one.id, dependent_two.id):
        row = rows[flagged_claim_id]
        assert row.flagged is True
        assert row.unresolved_correction_ids == (correction.id,)

    assert rows[dependent_one.id].status == "review_required"
    assert rows[dependent_two.id].status == "review_required"
    assert rows[root.id].status == "supported"

    # (c) The provenance map traces the corrected claim to its evidence.
    provenance = projection_service.build_provenance_map(root.id)
    hops_by_via = {hop.via: hop for hop in provenance.edges}

    assert hops_by_via["edge"].source_id == root.id
    assert hops_by_via["edge"].target_id == anchor.id
    assert hops_by_via["edge"].target_kind == "EvidenceAnchor"
    assert hops_by_via["edge"].relation == "supports"

    assert hops_by_via["field"].source_id == anchor.id
    assert hops_by_via["field"].target_id == source_record.id
    assert hops_by_via["field"].target_kind == "SourceRecord"
    assert hops_by_via["field"].relation == "source_record_id"

    # Rebuilding the projection from the same (unchanged) graph state is
    # byte-identical — re-derivable, no hidden state (task-packets/
    # E3-T07.yaml invariant).
    assert projection_service.build_claim_table() == projection_service.build_claim_table()
    assert projection_service.build_provenance_map(
        root.id
    ) == projection_service.build_provenance_map(root.id)
