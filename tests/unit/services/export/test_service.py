"""Unit tests for ``mrr.services.export.service.ExportService`` (task-
packets/E8-T01.yaml/E8-T03.yaml, EXTENDED by task-packets/E8-T06.yaml),
focused on the E8-T06 claim-rooted closure — ``resolve_closure_from_claims``/
``export_from_claims`` — run entirely DB-free against in-memory fakes of
``mrr.domain.repositories.ObjectRepository``/``EdgeRepository`` and the
event-log read surface, mirroring ``tests/unit/services/projection
/test_service.py``'s/``tests/unit/services/verification/test_service.py``'s
own identical ``FakeObjectRepository``/``FakeEdgeRepository``/``FakeEventLog``
fakes (duplicated here rather than imported, matching this codebase's own
"private module helper, not shared across test modules" precedent).

Unlike those two sibling files, objects here are seeded by DIRECT
``FakeObjectRepository.insert_revision`` (plain dict bodies, never through
``ClaimService``/``EvidenceAnchorService``/``VerificationService``) — a
disclosed, deliberate choice: this module tests ``ExportService``'s OWN R1
declared-reference-field resolver in isolation, over already-persisted
bodies, exactly the shape ``ObjectRepository.get_latest`` hands it in
production; it does not need, and should not depend on, any OTHER service's
own create/lifecycle rules. "claim.created"/"verification.recorded" creation
events ARE still appended (via :func:`_seed`'s own ``creation_event_type``)
wherever ``ProjectionService.build_claim_table``/``ExportService
._discover_verifications_targeting`` need to discover an id by event-log
scan — hand-constructed ``mrr.provenance.events.DomainEvent`` instances,
mirroring the exact event-type strings ``mrr.services.claim.service
.ClaimService.create``/``mrr.services.verification.service
.VerificationService.record`` actually append (transcribed, not imported —
see ``mrr.services.projection.service``'s own module docstring for why
this codebase does this per consuming module).

Acceptance-test mapping (task-packets/E8-T06.yaml, unit tier):

- crate seed / claim seed reach IDENTICAL closures for a graph the crate DOES
  reference: covered at the INTEGRATION tier instead (a sealed EvidenceCrate
  needs real signing/sealing machinery this unit tier deliberately avoids —
  mirrors task-packets/E8-T01.yaml's own precedent of testing crate-rooted
  behavior exclusively at the integration tier); see
  tests/integration/services/test_export_report_claim_rooted.py's
  ``test_crate_rooted_and_claim_rooted_closures_agree_when_the_crate_references_its_claims``.
- R1's declared-reference-field resolver, transitive to fixpoint ->
  ``test_resolve_closure_from_claims_reaches_evidence_anchors_and_source_records``,
  ``test_resolve_closure_from_claims_follows_verification_ids_to_a_new_anchor``.
- "each [EvidenceAnchor field] only when the field is non-empty" -> the run
  manifest is absent when every anchor's own ``run_id`` is empty, present
  when it is not -> ``test_run_manifest_absent_when_no_anchor_run_id_is_populated``,
  ``test_run_manifest_included_when_an_anchor_run_id_resolves``.
- a referenced urn that does not resolve is a typed refusal naming it ->
  ``test_a_dangling_evidence_relations_urn_is_a_typed_refusal``.
- R2d verification discovery (event-log scan) finds a verification even
  when the claim's OWN ``verification_ids`` array does not name it — the
  real K1-T04 fact-lock (see ``mrr.services.export.service``'s own module
  docstring) -> ``test_verification_discovered_via_event_log_even_when_not_in_verification_ids``.
- ``--claim-id``/``--all-claims`` root resolution + refusals ->
  ``test_explicit_claim_ids_resolve_to_their_own_closures``,
  ``test_an_unknown_claim_id_is_object_not_found``,
  ``test_a_claim_id_naming_a_non_claim_kind_is_a_typed_refusal``,
  ``test_all_claims_enumerates_every_claim_the_schema_contains``,
  ``test_all_claims_over_a_schema_with_zero_claims_refuses``,
  ``test_an_explicitly_empty_claim_ids_sequence_also_refuses``.
- ``ExportClosure.crate_id``/``artifact_refs`` for the claim-rooted case ->
  ``test_claim_rooted_closure_has_no_crate_id_and_no_artifact_refs``.
- determinism (two resolutions of the same graph agree) ->
  ``test_resolve_closure_from_claims_is_deterministic_on_rebuild``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from mrr.domain.exceptions import ObjectNotFoundError, RevisionConflictError
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject, TypedEdge
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.export.service import ExportService, NoClaimsToExportError

# ---------------------------------------------------------------------------
# In-memory fakes — identical in spirit to
# tests/unit/services/projection/test_service.py's own fakes.
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
            content_hash=f"sha256:{'e' * 64}",
            prev_hash=self.appended[-1].content_hash if self.appended else None,
        )
        self.appended.append(appended)
        return appended

    def read_all(self) -> list[AppendedEvent]:
        return list(self.appended)


# ---------------------------------------------------------------------------
# Fixture factories — plain dict bodies, seeded by DIRECT repository
# insertion (see the module docstring's own disclosed rationale).
# ---------------------------------------------------------------------------

_PRACTICE_ID = new_urn("practice")
_AGENT_ID = new_urn("agent")
_POLICY_VERSION = "policy-e8-t06-unit-test"
_CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def _seed(
    object_repository: FakeObjectRepository,
    event_log: FakeEventLog,
    *,
    id: str,
    kind: str,
    body: dict[str, Any],
    creation_event_type: str | None = None,
) -> None:
    # Every real stored body carries its own "id" (schemas/common.schema
    # .json baseObject, required) — injected here rather than at every call
    # site, so ``ProjectionService.build_claim_table``'s own
    # ``claim_body["id"]`` read (needed for the --all-claims tests) never
    # sees a body missing it.
    full_body = {**body, "id": id}
    obj = StoredObject(
        id=id,
        api_version="mrr/v1alpha1",
        kind=kind,
        practice_id=_PRACTICE_ID,
        revision=1,
        created_at=_CREATED_AT,
        created_by=_AGENT_ID,
        content_hash="sha256:" + "a" * 64,
        supersedes=None,
        labels=None,
        body=full_body,
    )
    object_repository.insert_revision(obj, expected_current_revision=None)
    if creation_event_type is not None:
        event = DomainEvent(
            id=new_urn("event"),
            event_type=creation_event_type,
            occurred_at=_CREATED_AT,
            actor=_AGENT_ID,
            policy_version=_POLICY_VERSION,
            causation_id=None,
            correlation_id=new_urn("run"),
            object_id=id,
            object_revision=1,
            payload={},
        )
        event_log.append_for_test(event)


def _claim_body(
    *,
    evidence_relations: tuple[str, ...] = (),
    counterevidence_relations: tuple[str, ...] = (),
    verification_ids: tuple[str, ...] = (),
    source_family_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "kind": "Claim",
        "assertion": "A claim asserted for E8-T06 unit coverage.",
        "claim_type": "statistical",
        "scope": {},
        "status": "draft",
        "evidence_relations": list(evidence_relations),
        "counterevidence_relations": list(counterevidence_relations),
        "dependencies": [],
        "source_family_ids": list(source_family_ids),
        "uncertainty": [],
        "known_unknowns": [],
        "proposer_id": _AGENT_ID,
        "verification_ids": list(verification_ids),
        "correction_ids": [],
    }


def _anchor_body(*, source_record_id: str | None, run_id: str | None) -> dict[str, Any]:
    return {
        "kind": "EvidenceAnchor",
        "relation": "supports",
        "anchor_kind": "text",
        "extraction_method": "manual quotation",
        "extractor_id": _AGENT_ID,
        "anchor_validation_status": "validated",
        "anchor_unavailable_reason": None,
        "source_record_id": source_record_id,
        "snapshot_hash": "sha256:" + "b" * 64,
        "run_id": run_id,
    }


def _source_record_body() -> dict[str, Any]:
    return {
        "kind": "SourceRecord",
        "title": "A fixture source",
        "source_type": "journal-article",
        "primary_secondary_derived": "primary",
    }


def _verification_body(
    *, target_id: str, recommendation: str, evidence_inspected: tuple[str, ...] = ()
) -> dict[str, Any]:
    return {
        "kind": "VerificationResult",
        "target_id": target_id,
        "target_kind": "claim",
        "reviewer_id": _AGENT_ID,
        "reviewer_role": "independent reviewer",
        "recommendation": recommendation,
        "confidence": 0.8,
        "evidence_inspected": list(evidence_inspected),
        "findings": [],
    }


def _run_manifest_body() -> dict[str, Any]:
    return {"kind": "RunManifest", "run_state": "completed"}


def _source_family_body(*, member_source_ids: tuple[str, ...]) -> dict[str, Any]:
    return {"kind": "SourceFamily", "member_source_ids": list(member_source_ids)}


def _export_service() -> tuple[ExportService, FakeObjectRepository, FakeEventLog]:
    object_repository = FakeObjectRepository()
    edge_repository = FakeEdgeRepository()
    event_log = FakeEventLog()

    class _NeverInvokedArtifactStore:
        def put(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("no test in this module ever fetches artifact bytes")

        def get(self, content_hash: str) -> bytes:
            raise AssertionError("no test in this module ever fetches artifact bytes")

        def stat(self, content_hash: str) -> Any:
            raise AssertionError("no test in this module ever fetches artifact bytes")

        def exists(self, content_hash: str) -> bool:
            raise AssertionError("no test in this module ever fetches artifact bytes")

    export_service = ExportService(
        object_repository, edge_repository, event_log, _NeverInvokedArtifactStore()
    )
    return export_service, object_repository, event_log


# ---------------------------------------------------------------------------
# R1: the declared-reference-field resolver, transitive to fixpoint.
# ---------------------------------------------------------------------------


def test_resolve_closure_from_claims_reaches_evidence_anchors_and_source_records() -> None:
    """The crux itself: claim.evidence_relations/counterevidence_relations
    -> anchors -> anchor.source_record_id -> source records, none of which
    ``ProjectionService.build_provenance_map`` alone would ever reach
    (fact-locked against the real K1-T04 schema — see the module
    docstring's own note in ``mrr.services.export.service``).
    """
    export_service, object_repository, event_log = _export_service()
    claim_id = new_urn("claim")
    support_anchor_id = new_urn("evidence-anchor")
    counter_anchor_id = new_urn("evidence-anchor")
    support_source_id = new_urn("source-record")
    counter_source_id = new_urn("source-record")

    _seed(
        object_repository,
        event_log,
        id=claim_id,
        kind="Claim",
        body=_claim_body(
            evidence_relations=(support_anchor_id,),
            counterevidence_relations=(counter_anchor_id,),
        ),
    )
    _seed(
        object_repository,
        event_log,
        id=support_anchor_id,
        kind="EvidenceAnchor",
        body=_anchor_body(source_record_id=support_source_id, run_id=None),
    )
    _seed(
        object_repository,
        event_log,
        id=counter_anchor_id,
        kind="EvidenceAnchor",
        body=_anchor_body(source_record_id=counter_source_id, run_id=None),
    )
    _seed(
        object_repository,
        event_log,
        id=support_source_id,
        kind="SourceRecord",
        body=_source_record_body(),
    )
    _seed(
        object_repository,
        event_log,
        id=counter_source_id,
        kind="SourceRecord",
        body=_source_record_body(),
    )

    closure = export_service.resolve_closure_from_claims([claim_id])

    assert set(closure.object_bodies) == {
        claim_id,
        support_anchor_id,
        counter_anchor_id,
        support_source_id,
        counter_source_id,
    }


def test_resolve_closure_from_claims_follows_verification_ids_to_a_new_anchor() -> None:
    """A ``VerificationResult`` reached via ``Claim.verification_ids`` has
    its OWN ``evidence_inspected`` anchors pulled in too (the fixpoint,
    R1's own "transitively to fixpoint" — a claim's declared verification
    inspecting an anchor the claim's own evidence_relations never named).
    """
    export_service, object_repository, event_log = _export_service()
    claim_id = new_urn("claim")
    named_anchor_id = new_urn("evidence-anchor")
    inspected_only_anchor_id = new_urn("evidence-anchor")
    verification_id = new_urn("verification")

    _seed(
        object_repository,
        event_log,
        id=claim_id,
        kind="Claim",
        body=_claim_body(
            evidence_relations=(named_anchor_id,), verification_ids=(verification_id,)
        ),
    )
    _seed(
        object_repository,
        event_log,
        id=named_anchor_id,
        kind="EvidenceAnchor",
        body=_anchor_body(source_record_id=None, run_id=None),
    )
    _seed(
        object_repository,
        event_log,
        id=inspected_only_anchor_id,
        kind="EvidenceAnchor",
        body=_anchor_body(source_record_id=None, run_id=None),
    )
    _seed(
        object_repository,
        event_log,
        id=verification_id,
        kind="VerificationResult",
        body=_verification_body(
            target_id=claim_id,
            recommendation="pass",
            evidence_inspected=(named_anchor_id, inspected_only_anchor_id),
        ),
        creation_event_type="verification.recorded",
    )

    closure = export_service.resolve_closure_from_claims([claim_id])

    assert inspected_only_anchor_id in closure.object_bodies
    assert verification_id in closure.object_bodies


# ---------------------------------------------------------------------------
# "each [anchor field] only when the field is non-empty": the run manifest.
# ---------------------------------------------------------------------------


def test_run_manifest_absent_when_no_anchor_run_id_is_populated() -> None:
    """The honest, real-K1-T04-shaped case: every anchor's own ``run_id``
    is empty, so the run manifest is NOT reachable claim-side — asserted
    absent, never fabricated (task-packets/E8-T06.yaml R1).
    """
    export_service, object_repository, event_log = _export_service()
    claim_id = new_urn("claim")
    anchor_id = new_urn("evidence-anchor")
    run_manifest_id = new_urn("run-manifest")

    _seed(
        object_repository,
        event_log,
        id=claim_id,
        kind="Claim",
        body=_claim_body(evidence_relations=(anchor_id,)),
    )
    _seed(
        object_repository,
        event_log,
        id=anchor_id,
        kind="EvidenceAnchor",
        body=_anchor_body(source_record_id=None, run_id=None),
    )
    _seed(
        object_repository,
        event_log,
        id=run_manifest_id,
        kind="RunManifest",
        body=_run_manifest_body(),
    )

    closure = export_service.resolve_closure_from_claims([claim_id])

    assert run_manifest_id not in closure.object_bodies


def test_run_manifest_included_when_an_anchor_run_id_resolves() -> None:
    """The "when non-empty" branch: one anchor DOES carry a non-empty
    ``run_id`` -> the run manifest IS included (task-packets/E8-T06.yaml
    R5's own second, smaller fixture requirement, exercised here at unit
    tier too).
    """
    export_service, object_repository, event_log = _export_service()
    claim_id = new_urn("claim")
    anchor_id = new_urn("evidence-anchor")
    run_manifest_id = new_urn("run-manifest")

    _seed(
        object_repository,
        event_log,
        id=claim_id,
        kind="Claim",
        body=_claim_body(evidence_relations=(anchor_id,)),
    )
    _seed(
        object_repository,
        event_log,
        id=anchor_id,
        kind="EvidenceAnchor",
        body=_anchor_body(source_record_id=None, run_id=run_manifest_id),
    )
    _seed(
        object_repository,
        event_log,
        id=run_manifest_id,
        kind="RunManifest",
        body=_run_manifest_body(),
    )

    closure = export_service.resolve_closure_from_claims([claim_id])

    assert run_manifest_id in closure.object_bodies
    assert closure.object_bodies[run_manifest_id]["kind"] == "RunManifest"


# ---------------------------------------------------------------------------
# Fail-fast refusals.
# ---------------------------------------------------------------------------


def test_a_dangling_evidence_relations_urn_is_a_typed_refusal() -> None:
    export_service, object_repository, event_log = _export_service()
    claim_id = new_urn("claim")
    dangling_anchor_id = new_urn("evidence-anchor")

    _seed(
        object_repository,
        event_log,
        id=claim_id,
        kind="Claim",
        body=_claim_body(evidence_relations=(dangling_anchor_id,)),
    )

    with pytest.raises(ObjectNotFoundError) as excinfo:
        export_service.resolve_closure_from_claims([claim_id])
    assert excinfo.value.id == dangling_anchor_id


def test_verification_discovered_via_event_log_even_when_not_in_verification_ids() -> None:
    """The real K1-T04 fact-lock, reproduced directly: a claim whose own
    ``verification_ids`` is EMPTY still gets its recorded verifications —
    discovered via ``ExportService._discover_verifications_targeting``'s
    event-log scan (R2d, shared with the crate-rooted path), not via the
    (in this case empty, hence useless) declared field.
    """
    export_service, object_repository, event_log = _export_service()
    claim_id = new_urn("claim")
    pass_verification_id = new_urn("verification")
    fail_verification_id = new_urn("verification")

    _seed(object_repository, event_log, id=claim_id, kind="Claim", body=_claim_body())
    _seed(
        object_repository,
        event_log,
        id=pass_verification_id,
        kind="VerificationResult",
        body=_verification_body(target_id=claim_id, recommendation="pass"),
        creation_event_type="verification.recorded",
    )
    _seed(
        object_repository,
        event_log,
        id=fail_verification_id,
        kind="VerificationResult",
        body=_verification_body(target_id=claim_id, recommendation="fail"),
        creation_event_type="verification.recorded",
    )

    closure = export_service.resolve_closure_from_claims([claim_id])

    assert pass_verification_id in closure.object_bodies
    assert fail_verification_id in closure.object_bodies
    recommendations = {
        closure.object_bodies[vid]["recommendation"]
        for vid in (pass_verification_id, fail_verification_id)
    }
    assert recommendations == {"pass", "fail"}


# ---------------------------------------------------------------------------
# --claim-id / --all-claims root resolution.
# ---------------------------------------------------------------------------


def test_explicit_claim_ids_resolve_to_their_own_closures() -> None:
    export_service, object_repository, event_log = _export_service()
    claim_a = new_urn("claim")
    claim_b = new_urn("claim")
    _seed(object_repository, event_log, id=claim_a, kind="Claim", body=_claim_body())
    _seed(object_repository, event_log, id=claim_b, kind="Claim", body=_claim_body())

    closure = export_service.resolve_closure_from_claims([claim_a])

    assert set(closure.object_bodies) == {claim_a}


def test_an_unknown_claim_id_is_object_not_found() -> None:
    export_service, _object_repository, _event_log = _export_service()
    unknown_claim_id = new_urn("claim")

    with pytest.raises(ObjectNotFoundError):
        export_service.resolve_closure_from_claims([unknown_claim_id])


def test_a_claim_id_naming_a_non_claim_kind_is_a_typed_refusal() -> None:
    export_service, object_repository, event_log = _export_service()
    not_a_claim_id = new_urn("evidence-anchor")
    _seed(
        object_repository,
        event_log,
        id=not_a_claim_id,
        kind="EvidenceAnchor",
        body=_anchor_body(source_record_id=None, run_id=None),
    )

    with pytest.raises(ValueError, match="EvidenceAnchor"):
        export_service.resolve_closure_from_claims([not_a_claim_id])


def test_all_claims_enumerates_every_claim_the_schema_contains() -> None:
    export_service, object_repository, event_log = _export_service()
    claim_a = new_urn("claim")
    claim_b = new_urn("claim")
    _seed(
        object_repository,
        event_log,
        id=claim_a,
        kind="Claim",
        body=_claim_body(),
        creation_event_type="claim.created",
    )
    _seed(
        object_repository,
        event_log,
        id=claim_b,
        kind="Claim",
        body=_claim_body(),
        creation_event_type="claim.created",
    )

    closure = export_service.resolve_closure_from_claims(None)

    assert set(closure.object_bodies) == {claim_a, claim_b}


def test_all_claims_over_a_schema_with_zero_claims_refuses() -> None:
    export_service, _object_repository, _event_log = _export_service()

    with pytest.raises(NoClaimsToExportError):
        export_service.resolve_closure_from_claims(None)


def test_an_explicitly_empty_claim_ids_sequence_also_refuses() -> None:
    """The invariant is phrased as an OUTCOME ("a zero-claim claim-rooted
    export refuses"), not as one specific code path — an explicit, empty
    ``claim_ids`` list refuses identically to an empty ``--all-claims``
    enumeration.
    """
    export_service, _object_repository, _event_log = _export_service()

    with pytest.raises(NoClaimsToExportError):
        export_service.resolve_closure_from_claims([])


# ---------------------------------------------------------------------------
# ExportClosure shape + determinism.
# ---------------------------------------------------------------------------


def test_claim_rooted_closure_has_no_crate_id_and_no_artifact_refs() -> None:
    export_service, object_repository, event_log = _export_service()
    claim_id = new_urn("claim")
    _seed(object_repository, event_log, id=claim_id, kind="Claim", body=_claim_body())

    closure = export_service.resolve_closure_from_claims([claim_id])

    assert closure.crate_id is None
    assert closure.artifact_refs == ()


def test_resolve_closure_from_claims_is_deterministic_on_rebuild() -> None:
    export_service, object_repository, event_log = _export_service()
    claim_id = new_urn("claim")
    anchor_id = new_urn("evidence-anchor")
    source_id = new_urn("source-record")
    family_id = new_urn("source-family")

    _seed(
        object_repository,
        event_log,
        id=claim_id,
        kind="Claim",
        body=_claim_body(evidence_relations=(anchor_id,), source_family_ids=(family_id,)),
    )
    _seed(
        object_repository,
        event_log,
        id=anchor_id,
        kind="EvidenceAnchor",
        body=_anchor_body(source_record_id=source_id, run_id=None),
    )
    _seed(
        object_repository, event_log, id=source_id, kind="SourceRecord", body=_source_record_body()
    )
    _seed(
        object_repository,
        event_log,
        id=family_id,
        kind="SourceFamily",
        body=_source_family_body(member_source_ids=(source_id,)),
    )

    first = export_service.resolve_closure_from_claims([claim_id])
    second = export_service.resolve_closure_from_claims([claim_id])

    assert dict(first.object_bodies) == dict(second.object_bodies)
    assert first.provenance_edges == second.provenance_edges
