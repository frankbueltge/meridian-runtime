"""``run_synthesis_evidence_loop`` (task-packets/K1-T03.yaml): the composition
function that drives one complete ``systematic_evidence_synthesis`` v1 run —
approve a Research Score, register a node's capability, negotiate and
execute a deterministic Task Bundle running
``mrr.services.node_runtime.synthesis_executor.SystematicEvidenceSynthesisExecutor``,
deserialize its JSON output into real, persisted governance objects, record
the Run Manifest, and seal the Evidence Crate — mirroring
``mrr.services.cli.orchestration.run_local_evidence_loop``'s EXACT
choreography (Research Score -> capability registration -> Task Bundle ->
``dispatch()`` -> ``execute()`` -> ``RunManifestRecorder.record`` (UNCHANGED)
-> ``EvidenceCrateSealer.seal`` (UNCHANGED)), with ONE new step inserted
between ``execute()`` returning and ``RunManifestRecorder.record``:
deserializing the executor's own JSON output into real ``EvidenceMatrix``/
``MethodRuling``/``Claim``(s)/``ResearchDecision`` objects plus the
``governed_by_protocol``/``ruled_by`` typed edges spec 08 section 3 names.

This is a NEW, sibling file — NOT a modification of ``orchestration.py`` —
precisely so this packet's diff cannot regress E2E-001 by construction
(forbidden_changes; confirmed by re-running
``tests/e2e/test_e2e_001_single_node_evidence_loop.py`` unmodified).

--- Scope: this packet only READS already-accepted/locked K1-T01 objects ----

``run_synthesis_evidence_loop`` does NOT build or drive
``QuestionModel``/``ConceptCharter``/a write-side ``MethodProtocol`` through
their OWN lifecycles — the caller is responsible for having ALREADY
persisted an accepted ``QuestionModel`` and a LOCKED ``MethodProtocol``
(whose ``profile_id`` resolves to a real, persisted ``MethodProfile``)
beforehand, via the generic ``ObjectRepository`` or a future task's own
service — exactly like task-packets/K1-T02.yaml's own integration tests seed
their prerequisite objects directly, bypassing any service
(``_seed_generic``). This function takes their ids
(``question_model_id``/``method_protocol_id``) and resolves them via
``ObjectRepository.get_latest``.

--- No MethodProfile persistence needed for dispatch -------------------------

``build_dispatch_table``'s SECOND pass (a caller-supplied factory not
already covered by an accepted-profile match) reaches the table without any
``MethodProfile`` object needing to exist in the database at all — this
mirrors ``run_local_evidence_loop``'s own default-table construction
(``build_dispatch_table([], {DEFAULT_CAPABILITY_NAME: ReferenceTaskExecutor})``)
exactly. A REAL, persisted ``MethodProfile`` is still required, separately,
for ``ClaimService.attach_ruling``'s own ceiling-chain resolution
(``MethodRuling.protocol_id -> MethodProtocol.profile_id ->
MethodProfile.max_claim_ceiling``) — that object must already resolve via
``ObjectRepository.get_latest`` by the time this function calls
``attach_ruling``, but this function never itself drives that profile
through ``MethodProfileService``'s own ``propose``/``accept`` lifecycle.

--- Claim status: reviewer_resolution overrides the packet body's own mapping ---

task-packets/K1-T03.yaml's ``reviewer_resolution`` (2026-07-21) resolves the
self-verification question CONSERVATIVELY, overriding the packet body's own
``derived_decisions (l)`` (a node-identity self-verification workaround):
"the synthesis run mints claim candidates in proposed status ONLY and
MUST NOT author its own supporting VerificationResults ... claims advance
through the unchanged claim/verification lifecycle afterward." This function
therefore NEVER constructs a ``VerificationResult`` and NEVER drives a claim
to ``status == "supported"`` (which the ``Claim`` contract's own
``model_validator`` structurally requires at least one real
``verification_ids`` entry for) — reconciling this with spec 08 section 5's
amended text ("contested and unsupported map to the existing claim statuses
of the same name") as follows, the narrowest reading that keeps every
sentence of both texts true simultaneously:

- a ``"supported"``-track finding (the executor's own analysis outcome)
  yields a ``Claim`` created and left at ``CLAIM_LIFECYCLE``'s own initial
  state, ``"draft"`` — genuinely "proposed", never advanced further by this
  run. Reaching real ``"supported"`` requires a later, independently
  attributed ``VerificationResult`` this run does not and must not supply
  (AGENTS.md rule 8) — "claims advance through the unchanged claim/
  verification lifecycle afterward", literally.
- a ``"contested"``/``"unsupported"``-track finding is driven all the way
  to that SAME-NAMED ``Claim.status`` (``create`` -> ``submit_for_review``
  -> ``to_contested``/``to_unsupported``) — neither transition requires any
  ``VerificationResult`` at the contract level (only ``to_supported``
  does), so driving these two all the way is compatible with "MUST NOT
  author its own supporting VerificationResults" with zero exception.

Flagged prominently (required_output): this deliberately does NOT match the
packet body's own acceptance_tests wording ("two Claims ('supported' and
'contested')") — under the binding reviewer_resolution, NEITHER candidate
reaches ``"supported"`` within this run; the supported-track candidate stays
``"draft"``. Every ``Claim`` this run mints — regardless of which status it
reaches — still gets a ``MethodRuling`` (``create`` + ``issue``) and a
``ruled_by`` edge via ``ClaimService.attach_ruling`` (which is NOT gated on
claim status at all), per derived_decisions (f).

--- EvidenceCrate.source_records/evidence_anchors/proposed_claims: CLOSED ----
--- (task-packets/E9-T00.yaml item 7, 2026-07-21) -----------------------------

Originally a newly discovered, disclosed specification/implementation gap
(K1-T03's own required_output): task-packets/K1-T03.yaml's own
derived_decisions (i) assumed ``EvidenceCrateSealer.seal()`` already
accepted caller-supplied ``source_records``/``evidence_anchors``/
``proposed_claims`` lists, citing "evidence_crate.py lines 62-73" — those
line numbers were actually ``mrr.contracts.evidence_crate.EvidenceCrate``'s
own CONTRACT field declarations, not ``EvidenceCrateSealer.seal()``'s
parameter list. Direct reading of the ACTUAL, merged
``mrr.services.node_runtime.evidence_crate.EvidenceCrateSealer.seal``
(confirmed unchanged since E2-T06, ``c55d491``, plus the E5-T00 ADR-0004
signing-convention commit) showed it hardcoded all three to ``[]``
unconditionally in the draft it built — no keyword parameter for any of
them, unlike ``artifact_refs``/``failures``/``known_unknowns``. K1-T03's own
``forbidden_changes``/``allowed_paths`` both barred modifying
``evidence_crate.py`` at all, so that run called ``seal()`` EXACTLY as
``run_local_evidence_loop`` already did — meaning the SEALED CRATE's own
``source_records``/``evidence_anchors``/``proposed_claims`` fields were
empty, even though this run genuinely DOES create real
``SourceRecord``/``EvidenceAnchor``/``Claim`` objects, all independently
resolvable via the ``governed_by_protocol`` edge graph this run also writes
(matching K1's own stated exit criterion — "a third party can follow one
sealed crate from question to claim landscape entirely through recorded
objects" — via edges, not via the crate's own three array fields).
K1-T03's own docstring named extending ``EvidenceCrateSealer.seal()`` to
accept these three lists (mirroring how it already accepted
``artifact_refs``) "a natural, small follow-up, explicitly left for a
future task".

That future task is task-packets/E9-T00.yaml item 7: ``seal()`` now accepts
``source_records``/``evidence_anchors``/``proposed_claims`` as additive,
default-``()`` keyword parameters (see
``mrr.services.node_runtime.evidence_crate``'s own module docstring), and
this function passes its own already-collected
``entry_ids_to_source_record_id.values()``/``entry_ids_to_evidence_anchor_
id.values()``/``claim_ids`` through (via ``_persist_synthesis_output``'s
now-widened return tuple — zero new reads, zero new persisted state). The
sealed crate's own three array fields now carry the same information the
``governed_by_protocol`` edge graph already did, redundantly but
harmlessly, matching what spec 08 section 5's I/O contract and the crate's
own required schema fields both anticipate. ``run_local_evidence_loop``
(a DIFFERENT caller, forbidden_changes) is untouched — its own call to
``seal()`` still omits all three keywords and still gets ``[]``, byte-
identical to before.

--- NodeManifest capability-name pattern conflict (second disclosed gap) ----

A second newly discovered, disclosed specification/implementation conflict
(required_output): ``mrr.contracts.node_manifest.CapabilityDefinition.name``
(E2-T02, protected, outside ``allowed_paths``) enforces a stricter pattern
than ``CAPABILITY_NAME`` ("mrr.method.systematic_evidence_synthesis/1",
examples/method-profile.example.json's own already-accepted value) can
satisfy — confirmed by direct construction failure. This composition's own
NodeManifest/TaskBundle/dispatch-table layer therefore uses a disclosed,
pattern-compliant synonym, ``_NODE_CAPABILITY_NAME``, for those three call
sites only — see that constant's own module-level docstring, right above
its definition, for the full derivation and why it does not affect anything
else this run persists.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.contracts import (
    ArtifactRef,
    BaseObject,
    Claim,
    ClaimType,
    EvidenceAnchor,
    EvidenceCrate,
    EvidenceMatrix,
    EvidenceMatrixRow,
    FailureCategory,
    FailureEntry,
    MethodRuling,
    NodeManifest,
    ResearchDecision,
    ResearchScore,
    RunManifest,
    Scope,
    Signature,
    SourceRecord,
    TaskBundle,
    Urn,
)
from mrr.crypto.canonical import canonicalize
from mrr.crypto.keys import encode_public_key
from mrr.domain.artifacts import ArtifactStore
from mrr.domain.hashing_policy import compute_content_hash, sign_object
from mrr.domain.identity import is_valid_urn, new_urn
from mrr.domain.repositories import EDGE_VOCABULARY, TypedEdge
from mrr.persistence.repositories import (
    PostgresEdgeRepository,
    PostgresEventLog,
    PostgresObjectRepository,
)
from mrr.persistence.tables import edges_table
from mrr.provenance.events import DomainEvent
from mrr.services.capability_registry.service import CapabilityRegistry
from mrr.services.capability_registry.service import bind_unit_of_work as _bind_capability_uow
from mrr.services.claim.service import ClaimService
from mrr.services.claim.service import bind_edge_unit_of_work as _bind_claim_edge_uow
from mrr.services.claim.service import bind_unit_of_work as _bind_claim_uow
from mrr.services.evidence.service import EvidenceAnchorService, SourceRecordService
from mrr.services.evidence.service import bind_unit_of_work as _bind_evidence_uow
from mrr.services.evidence_matrix.service import EvidenceMatrixService
from mrr.services.evidence_matrix.service import bind_unit_of_work as _bind_matrix_uow
from mrr.services.method_ruling.service import MethodRulingService
from mrr.services.method_ruling.service import bind_unit_of_work as _bind_ruling_uow
from mrr.services.node_runtime.dispatch import (
    CapabilityDispatchTable,
    build_dispatch_table,
    dispatch,
)
from mrr.services.node_runtime.evidence_crate import EvidenceCrateSealer
from mrr.services.node_runtime.evidence_crate import bind_unit_of_work as _bind_crate_uow
from mrr.services.node_runtime.executor import Executor, TerminalOutcome
from mrr.services.node_runtime.run_manifest import RunManifestRecorder
from mrr.services.node_runtime.run_manifest import bind_unit_of_work as _bind_manifest_uow
from mrr.services.node_runtime.synthesis_executor import (
    CAPABILITY_NAME,
    SystematicEvidenceSynthesisExecutor,
)
from mrr.services.research_decision.service import ResearchDecisionService
from mrr.services.research_decision.service import bind_unit_of_work as _bind_decision_uow
from mrr.services.research_score.service import ResearchScoreService
from mrr.services.research_score.service import bind_unit_of_work as _bind_score_uow
from mrr.services.task_bundle.service import NodeTaskDecisionService, TaskBundleService
from mrr.services.task_bundle.service import bind_event_unit_of_work as _bind_bundle_event_uow
from mrr.services.task_bundle.service import bind_unit_of_work as _bind_bundle_uow
from sqlalchemy import Engine

#: The capability this composition function always negotiates and executes
#: — this run has exactly one purpose, unlike ``run_local_evidence_loop``'s
#: caller-configurable ``capability_name``.
_CAPABILITY_VERSION_DEFAULT = "1.0.0"

#: A NEWLY DISCOVERED, disclosed specification/implementation conflict
#: (required_output): ``mrr.contracts.node_manifest.CapabilityDefinition.name``
#: (E2-T02, protected — outside this packet's own ``allowed_paths``) enforces
#: the pattern ``^[a-z0-9-]+(\.[a-z0-9-]+)+$`` (dot-separated segments of
#: lowercase alphanumerics/hyphens ONLY) — confirmed by direct construction
#: failure (a ``pydantic.ValidationError``) that ``CAPABILITY_NAME`` itself
#: ("mrr.method.systematic_evidence_synthesis/1",
#: examples/method-profile.example.json's own ALREADY-ACCEPTED value, an
#: underscore-and-slash-bearing string) can NEVER be registered as a
#: ``NodeManifest`` capability name. K0-T02's own e2e test
#: (``tests/e2e/test_k0_t02_capability_dispatch.py``) never actually
#: exercised this: its own in-test second capability name
#: ("mrr.k0-t02-test.second-capability") was deliberately already
#: pattern-compliant, so this conflict — between K0-T01's OWN looser
#: ``MethodProfile.executor_task_family`` shape (a plain non-empty ``str``,
#: no pattern) and E2-T02's stricter, EARLIER ``NodeManifest`` pattern — was
#: never previously reachable. ``TaskBundleService.create``'s own
#: ``_ensure_capability_declared`` gate (also unchanged, outside
#: ``allowed_paths``) requires ``TaskBundle.capability.name`` to EXACTLY
#: equal a ``NodeManifest``-declared name, so this orchestration's own
#: TaskBundle/NodeManifest/dispatch-table layer uses this pattern-compliant,
#: disclosed SYNONYM instead, for those three call sites ONLY.
#: ``CAPABILITY_NAME`` itself is untouched and still exactly what
#: ``synthesis_executor.py`` exports, what
#: ``examples/method-profile.example.json``/a real accepted ``MethodProfile``
#: declares in ``executor_task_family``, and what this function's own
#: ``ResearchScore.methods.allowed`` cites (an unconstrained, plain
#: ``list[str]`` field) — nothing in this packet's OWN runtime path actually
#: cross-checks ``MethodProfile.executor_task_family`` against
#: ``TaskBundle.capability.name``/``NodeManifest.capabilities[].name`` (this
#: composition never calls ``MethodProfileService.find_accepted_by_capability``
#: or passes a real profile to ``build_dispatch_table``'s first pass — see
#: derived_decisions (a) and this module's own "No MethodProfile persistence
#: needed for dispatch" section), so this substitution is invisible to every
#: OTHER object this run persists. Resolving the underlying pattern conflict
#: (relaxing ``NodeManifest``'s pattern, or requiring a pattern-compliant
#: ``executor_task_family`` convention) is left for a future task — this
#: packet may not touch ``node_manifest.py`` at all (outside both
#: ``allowed_paths`` and this packet's own scope).
_NODE_CAPABILITY_NAME = "mrr.method.systematic-evidence-synthesis-1"

_REFERENCE_IMAGE_DIGEST = "sha256:" + "d" * 64

#: Mirrors ``mrr.services.cli.orchestration._FAILURE_CATEGORY_BY_OUTCOME`` —
#: a local copy, not a shared import (see that module's own docstring for
#: the identical rationale: no spec-ratified mapping exists).
_FAILURE_CATEGORY_BY_OUTCOME: dict[str, FailureCategory] = {
    "failed": "execution_error",
    "timed_out": "execution_error",
    "cancelled": "execution_error",
    "policy_denied": "policy_denied",
    "partial": "unknown",
}

#: The one typed edge every governed object this run produces carries back
#: to the locked ``MethodProtocol`` (docs/spec/08_RESEARCH_METHOD_KERNEL.md
#: section 3).
_GOVERNED_BY_PROTOCOL_EDGE_TYPE = "governed_by_protocol"
_RULED_BY_EDGE_TYPE = "ruled_by"

#: MethodRuling.deterministic_rule_reference (re-exported from
#: synthesis_executor for the ceiling-chain construction below).
_DETERMINISTIC_RULE_REFERENCE = "k1-t03.eligibility_and_ceiling_rules.v1"


@dataclass(frozen=True, slots=True)
class SynthesisEvidenceLoopResult:
    """Every id a caller needs to independently resolve and verify what this
    run produced, without re-running anything. Mirrors
    ``mrr.services.cli.orchestration.LocalEvidenceLoopResult``'s own shape,
    extended with the K1-T03 objects it additionally persists.
    """

    evidence_crate_id: Urn
    run_manifest_id: Urn
    task_id: Urn
    research_score_id: Urn
    node_id: Urn
    output_hash: str | None
    run_state: TerminalOutcome
    is_deterministic: bool
    evidence_matrix_id: Urn | None
    claim_ids: tuple[Urn, ...]
    method_ruling_ids: tuple[Urn, ...]
    research_decision_ids: tuple[Urn, ...]


# ---------------------------------------------------------------------------
# Local edge-writing helper — a LOCAL copy, not an import of
# mrr.services.claim.service.bind_edge_unit_of_work, per derived_decisions
# (f): "written by run_synthesis_evidence_loop directly via the generic
# EdgeRepository/a locally-defined RecordEdgeWithEvent callable (mirroring
# ClaimService._write_edge's shape, duplicated locally rather than imported
# cross-module)".
# ---------------------------------------------------------------------------


def _write_governed_by_protocol_edge(
    engine: Engine,
    event_log: PostgresEventLog,
    *,
    source_id: Urn,
    protocol_id: Urn,
    actor: Urn,
    policy_version: str,
    correlation_id: Urn,
) -> TypedEdge:
    """Insert one ``governed_by_protocol`` edge (``source_id ->
    protocol_id``) plus one ``synthesis_run.governed_by_protocol_recorded``
    domain event, atomically — the same columns, values, and
    ``EDGE_VOCABULARY``/``UnknownEdgeTypeError`` fail-closed check
    ``PostgresEdgeRepository.add_edge`` itself uses, just sharing
    ``event_log.append``'s connection instead of opening its own (identical
    rationale to ``ClaimService.bind_edge_unit_of_work``).
    """
    if _GOVERNED_BY_PROTOCOL_EDGE_TYPE not in EDGE_VOCABULARY:  # pragma: no cover - defensive
        raise ValueError(f"unknown edge type: {_GOVERNED_BY_PROTOCOL_EDGE_TYPE!r}")

    now = datetime.now(UTC)
    edge = TypedEdge(
        id=new_urn("edge"),
        source_id=source_id,
        target_id=protocol_id,
        edge_type=_GOVERNED_BY_PROTOCOL_EDGE_TYPE,
        created_at=now,
        created_by=actor,
        scope=None,
        status="active",
        practice_id=None,
    )
    event = DomainEvent(
        id=new_urn("domain-event"),
        event_type="synthesis_run.governed_by_protocol_recorded",
        occurred_at=now,
        actor=actor,
        policy_version=policy_version,
        causation_id=None,
        correlation_id=correlation_id,
        object_id=source_id,
        object_revision=1,
        payload={"edge_id": edge.id, "protocol_id": protocol_id},
    )
    with engine.begin() as conn:
        conn.execute(
            sa.insert(edges_table).values(
                id=edge.id,
                source_id=edge.source_id,
                target_id=edge.target_id,
                edge_type=edge.edge_type,
                created_at=edge.created_at,
                created_by=edge.created_by,
                practice_id=edge.practice_id,
                scope=edge.scope,
                status=edge.status,
            )
        )
        event_log.append(conn, event)
    return edge


# ---------------------------------------------------------------------------
# ADR-0004 signing helpers — local copies of
# mrr.services.cli.orchestration's own identical helpers (forbidden_changes
# bars importing from that module or modifying it).
# ---------------------------------------------------------------------------


def _finalize_content_hash[T: BaseObject](draft: T) -> T:
    body: dict[str, Any] = json.loads(draft.model_dump_json(exclude_none=True))
    real_hash = compute_content_hash(body)
    return draft.model_copy(update={"content_hash": real_hash})


def _build_research_score(*, practice_id: str, actor: str) -> ResearchScore:
    now = datetime.now(UTC)
    draft = ResearchScore.model_validate(
        {
            "id": new_urn("research-score"),
            "api_version": "mrr/v1alpha1",
            "kind": "ResearchScore",
            "practice_id": practice_id,
            "revision": 1,
            "created_at": now,
            "created_by": actor,
            "content_hash": "sha256:" + "0" * 64,
            "question": (
                "Does the systematic_evidence_synthesis v1 executor task family "
                "produce a stable, replayable evidence landscape for a small, "
                "human-curated corpus?"
            ),
            "objectives": [
                "Exercise the full synthesis loop: corpus ingestion, inclusion "
                "filtering, matrix assembly, independence counting, eligibility "
                "and ceiling rules, through sealed crate."
            ],
            "non_goals": [],
            "scope": {},
            "methods": {"allowed": [CAPABILITY_NAME], "prohibited": []},
            "data_classes": ["PUBLIC"],
            "autonomy": {},
            "budgets": {},
            "quality_gates": [
                "Deterministic replay: identical inputs yield an identical output hash."
            ],
            "stop_conditions": ["Local execution budget exhausted."],
            "publication_policy": {
                "max_disclosure": "INTERNAL",
                "external_publication_requires_approval": True,
            },
            "status": "DRAFT",
            "approvals": [],
        }
    )
    return _finalize_content_hash(draft)


def _add_approval_revision(latest: ResearchScore, *, approval_id: str) -> ResearchScore:
    updated = latest.model_copy(
        update={
            "approvals": [approval_id],
            "revision": latest.revision + 1,
            "created_at": datetime.now(UTC),
        }
    )
    return _finalize_content_hash(updated)


def _build_node_manifest(
    *,
    node_id: str,
    node_practice_id: str,
    actor: str,
    capability_version: str,
    node_signing_key: Ed25519PrivateKey,
    node_key_id: str,
) -> NodeManifest:
    now = datetime.now(UTC)
    placeholder_signature = Signature(
        signer_practice_id=node_practice_id,
        key_id=node_key_id,
        algorithm="Ed25519",
        signed_at=now,
        value="0" * 44,
    )
    draft = NodeManifest.model_validate(
        {
            "id": new_urn("node-manifest"),
            "api_version": "mrr/v1alpha1",
            "kind": "NodeManifest",
            "practice_id": node_practice_id,
            "revision": 1,
            "created_at": now,
            "created_by": actor,
            "content_hash": "sha256:" + "0" * 64,
            "node_id": node_id,
            "capabilities": [
                {
                    "name": _NODE_CAPABILITY_NAME,
                    "version": capability_version,
                    "input_schema": "urn:mrr:schema:synthesis-corpus:1",
                    "output_schema": "urn:mrr:schema:evidence-crate:1",
                    "max_autonomy": "A1",
                    "approval": "automatic",
                    "network_profile": "none",
                }
            ],
            "restrictions": [],
            "accepted_classifications": ["PUBLIC"],
            "transport_modes": ["online"],
            "valid_from": now - timedelta(minutes=1),
            "valid_until": now + timedelta(days=365),
            "public_keys": [encode_public_key(node_signing_key.public_key())],
            "signature": placeholder_signature,
        }
    )
    body: dict[str, Any] = json.loads(draft.model_dump_json(exclude_none=True))
    body["content_hash"] = compute_content_hash(body)
    signature_value = sign_object(node_signing_key, body)
    final_signature = placeholder_signature.model_copy(update={"value": signature_value})
    body["signature"] = final_signature.model_dump(mode="json")
    return NodeManifest.model_validate(body)


def _build_task_bundle(
    *,
    origin_practice_id: str,
    target_node_id: str,
    research_score_id: str,
    research_score_revision: int,
    actor: str,
    capability_version: str,
    input_artifact_refs: list[ArtifactRef],
    instructions: dict[str, Any],
    timeout_seconds: int,
    origin_signing_key: Ed25519PrivateKey,
    origin_key_id: str,
    code_revision: str | None,
) -> TaskBundle:
    now = datetime.now(UTC)
    placeholder_signature = Signature(
        signer_practice_id=origin_practice_id,
        key_id=origin_key_id,
        algorithm="Ed25519",
        signed_at=now,
        value="0" * 44,
    )
    draft = TaskBundle.model_validate(
        {
            "id": new_urn("task-bundle"),
            "api_version": "mrr/v1alpha1",
            "kind": "TaskBundle",
            "practice_id": origin_practice_id,
            "revision": 1,
            "created_at": now,
            "created_by": actor,
            "content_hash": "sha256:" + "0" * 64,
            "origin_practice_id": origin_practice_id,
            "target_node_id": target_node_id,
            "research_score_id": research_score_id,
            "research_score_revision": research_score_revision,
            "branch_id": new_urn("branch"),
            "capability": {"name": _NODE_CAPABILITY_NAME, "version": capability_version},
            "purpose": (
                "Run the systematic_evidence_synthesis v1 executor task family "
                "(task-packets/K1-T03.yaml)."
            ),
            "instructions": instructions,
            "inputs": input_artifact_refs,
            "data_access_mode": "read_local",
            "execution": {
                "image_digest": _REFERENCE_IMAGE_DIGEST,
                "entrypoint": ["mrr-synthesis-task"],
                "code_revision": code_revision,
            },
            "resource_limits": {
                "cpu": 1.0,
                "memory_mb": 256,
                "disk_mb": 64,
                "timeout_seconds": timeout_seconds,
            },
            "network_policy": {"mode": "deny_all", "allowlist": []},
            "output_schema": "urn:mrr:schema:evidence-crate:1",
            "classification": "PUBLIC",
            "approval_requirement": "automatic",
            "expires_at": now + timedelta(days=1),
            "nonce": secrets.token_hex(16),
            "signature": placeholder_signature,
            "status": "CREATED",
        }
    )
    body: dict[str, Any] = json.loads(draft.model_dump_json(exclude_none=True))
    body["content_hash"] = compute_content_hash(body)
    signature_value = sign_object(origin_signing_key, body)
    final_signature = placeholder_signature.model_copy(update={"value": signature_value})
    body["signature"] = final_signature.model_dump(mode="json")
    return TaskBundle.model_validate(body)


# ---------------------------------------------------------------------------
# The composition function.
# ---------------------------------------------------------------------------


def run_synthesis_evidence_loop(
    *,
    engine: Engine,
    artifact_store: ArtifactStore,
    origin_signing_key: Ed25519PrivateKey,
    node_signing_key: Ed25519PrivateKey,
    question_model_id: Urn,
    method_protocol_id: Urn,
    corpus_entries: list[dict[str, Any]],
    protocol_parameters: dict[str, Any],
    actor: Urn | None = None,
    policy_version: str = "policy-mrr-k1-t03-synthesis",
    correlation_id: Urn | None = None,
    origin_practice_id: Urn | None = None,
    node_practice_id: Urn | None = None,
    node_id: Urn | None = None,
    origin_key_id: str = "origin-key-1",
    node_key_id: str = "node-key-1",
    capability_version: str = _CAPABILITY_VERSION_DEFAULT,
    timeout_seconds: int = 30,
    code_revision: str | None = None,
    executor: Executor | None = None,
    execution_attempt: int = 1,
    approve_score: bool = True,
) -> SynthesisEvidenceLoopResult:
    """Compose the merged E2 services, the three new K1-T03 services, and
    ``SystematicEvidenceSynthesisExecutor`` into one complete
    ``systematic_evidence_synthesis`` v1 run. See the module docstring for
    the full choreography and its disclosed scope limits.

    Args:
        question_model_id: an ALREADY-PERSISTED, accepted ``QuestionModel``
            id (resolved via the generic ``ObjectRepository`` — this
            function does not itself drive ``QuestionModel`` through its
            own lifecycle).
        method_protocol_id: an ALREADY-PERSISTED, LOCKED ``MethodProtocol``
            id whose ``profile_id`` resolves to a real, persisted
            ``MethodProfile`` (needed by ``ClaimService.attach_ruling``'s
            own ceiling-chain resolution).
        corpus_entries: the small, synthetic/sample corpus snapshot — a list
            of plain dicts, each shaped per
            ``mrr.services.node_runtime.synthesis_executor.CorpusEntry``.
        protocol_parameters: the protocol-parameters sidecar, a plain dict
            shaped per
            ``mrr.services.node_runtime.synthesis_executor.ProtocolParameters``
            — ``protocol_id``/``protocol_lock_content_hash`` MUST match the
            resolved ``method_protocol_id`` object's own id/content_hash, or
            the executor fails closed with ``ProtocolLockViolationError``.
        executor: an explicit override (e.g. one configured with a
            model-assisted ``extraction_callable``) — used exactly as
            supplied, unchanged precedence over the default dispatch-table
            resolution, mirroring ``run_local_evidence_loop``'s identical
            ``executor`` parameter.

    Returns:
        A ``SynthesisEvidenceLoopResult`` naming every object this run
        produced.
    """
    resolved_actor = actor if actor is not None else new_urn("agent-role")
    resolved_correlation_id = (
        correlation_id if correlation_id is not None else new_urn("research-run")
    )
    resolved_origin_practice_id = (
        origin_practice_id if origin_practice_id is not None else new_urn("practice")
    )
    resolved_node_practice_id = (
        node_practice_id if node_practice_id is not None else new_urn("practice")
    )
    resolved_node_id = node_id if node_id is not None else new_urn("node")

    object_repository = PostgresObjectRepository(engine)
    event_log = PostgresEventLog(engine)

    research_score_service = ResearchScoreService(
        object_repository, event_log, _bind_score_uow(engine, object_repository, event_log)
    )
    capability_registry = CapabilityRegistry(
        object_repository, event_log, _bind_capability_uow(engine, object_repository, event_log)
    )
    bundle_record = _bind_bundle_uow(engine, object_repository, event_log)
    bundle_record_event = _bind_bundle_event_uow(engine, event_log)
    task_bundle_service = TaskBundleService(
        object_repository,
        event_log,
        bundle_record,
        bundle_record_event,
        research_score_service,
        capability_registry,
    )
    node_decision_service = NodeTaskDecisionService(
        object_repository, event_log, bundle_record, bundle_record_event
    )
    run_manifest_recorder = RunManifestRecorder(
        _bind_manifest_uow(engine, object_repository, event_log)
    )
    evidence_crate_sealer = EvidenceCrateSealer(
        _bind_crate_uow(engine, object_repository, event_log)
    )

    evidence_record = _bind_evidence_uow(engine, object_repository, event_log)
    source_record_service = SourceRecordService(evidence_record)
    evidence_anchor_service = EvidenceAnchorService(evidence_record)

    matrix_record = _bind_matrix_uow(engine, object_repository, event_log)
    evidence_matrix_service = EvidenceMatrixService(object_repository, event_log, matrix_record)

    ruling_record = _bind_ruling_uow(engine, object_repository, event_log)
    method_ruling_service = MethodRulingService(object_repository, event_log, ruling_record)

    decision_record = _bind_decision_uow(engine, object_repository, event_log)
    research_decision_service = ResearchDecisionService(decision_record)

    edge_repository = PostgresEdgeRepository(engine)
    claim_record = _bind_claim_uow(engine, object_repository, event_log)
    claim_record_edge = _bind_claim_edge_uow(engine, event_log)
    claim_service = ClaimService(
        object_repository, event_log, edge_repository, claim_record, claim_record_edge
    )

    # --- 1. Research Score: create -> submit_for_review -> revise(+approval)
    #        -> [approve -> activate].
    score = _build_research_score(practice_id=resolved_origin_practice_id, actor=resolved_actor)
    research_score_service.create(
        score,
        actor=resolved_actor,
        policy_version=policy_version,
        correlation_id=resolved_correlation_id,
    )
    in_review = research_score_service.submit_for_review(
        score.id,
        actor=resolved_actor,
        policy_version=policy_version,
        correlation_id=resolved_correlation_id,
    )
    revised = _add_approval_revision(
        ResearchScore.model_validate(in_review.body), approval_id=new_urn("approval")
    )
    research_score_service.revise(
        revised,
        actor=resolved_actor,
        policy_version=policy_version,
        correlation_id=resolved_correlation_id,
    )
    if approve_score:
        research_score_service.approve(
            score.id,
            actor=resolved_actor,
            policy_version=policy_version,
            correlation_id=resolved_correlation_id,
        )
        research_score_service.activate(
            score.id,
            actor=resolved_actor,
            policy_version=policy_version,
            correlation_id=resolved_correlation_id,
        )

    # --- 2. Register the node's signed capability manifest.
    node_manifest = _build_node_manifest(
        node_id=resolved_node_id,
        node_practice_id=resolved_node_practice_id,
        actor=resolved_actor,
        capability_version=capability_version,
        node_signing_key=node_signing_key,
        node_key_id=node_key_id,
    )
    capability_registry.register(
        node_manifest,
        node_signing_key.public_key(),
        actor=resolved_actor,
        policy_version=policy_version,
        correlation_id=resolved_correlation_id,
    )

    # --- 3. Resolve the already-locked MethodProtocol; store the three
    #        input artifacts (corpus, protocol-parameters sidecar, protocol
    #        body) and mint their ArtifactRefs.
    method_protocol_stored = object_repository.get_latest(method_protocol_id)
    corpus_bytes = canonicalize(corpus_entries)
    protocol_parameters_bytes = canonicalize(protocol_parameters)
    method_protocol_bytes = canonicalize(method_protocol_stored.body)

    corpus_descriptor = artifact_store.put(
        corpus_bytes,
        media_type="application/json",
        producer_run_id=resolved_correlation_id,
        classification="PUBLIC",
        created_at=datetime.now(UTC),
    )
    protocol_parameters_descriptor = artifact_store.put(
        protocol_parameters_bytes,
        media_type="application/json",
        producer_run_id=resolved_correlation_id,
        classification="PUBLIC",
        created_at=datetime.now(UTC),
    )
    method_protocol_descriptor = artifact_store.put(
        method_protocol_bytes,
        media_type="application/json",
        producer_run_id=resolved_correlation_id,
        classification="PUBLIC",
        created_at=datetime.now(UTC),
    )

    corpus_artifact_id = new_urn("artifact")
    protocol_parameters_artifact_id = new_urn("artifact")
    method_protocol_artifact_id = new_urn("artifact")

    input_artifact_refs = [
        ArtifactRef(
            artifact_id=corpus_artifact_id,
            content_hash=corpus_descriptor.content_hash,
            classification="PUBLIC",
        ),
        ArtifactRef(
            artifact_id=protocol_parameters_artifact_id,
            content_hash=protocol_parameters_descriptor.content_hash,
            classification="PUBLIC",
        ),
        ArtifactRef(
            artifact_id=method_protocol_artifact_id,
            content_hash=method_protocol_descriptor.content_hash,
            classification="PUBLIC",
        ),
    ]
    instructions = {
        "corpus_artifact_id": corpus_artifact_id,
        "protocol_parameters_artifact_id": protocol_parameters_artifact_id,
        "method_protocol_artifact_id": method_protocol_artifact_id,
        "question_id": question_model_id,
    }

    # --- 4. Create + offer + accept the Task Bundle.
    bundle = _build_task_bundle(
        origin_practice_id=resolved_origin_practice_id,
        target_node_id=resolved_node_id,
        research_score_id=score.id,
        research_score_revision=revised.revision,
        actor=resolved_actor,
        capability_version=capability_version,
        input_artifact_refs=input_artifact_refs,
        instructions=instructions,
        timeout_seconds=timeout_seconds,
        origin_signing_key=origin_signing_key,
        origin_key_id=origin_key_id,
        code_revision=code_revision,
    )
    task_bundle_service.create(
        bundle,
        actor=resolved_actor,
        policy_version=policy_version,
        correlation_id=resolved_correlation_id,
    )
    task_bundle_service.offer(
        bundle.id,
        actor=resolved_actor,
        policy_version=policy_version,
        correlation_id=resolved_correlation_id,
    )
    accepted = node_decision_service.accept(
        bundle.id,
        resolved_node_id,
        origin_signing_key.public_key(),
        actor=resolved_node_id,
        policy_version=policy_version,
        correlation_id=resolved_correlation_id,
    )
    accepted_bundle = TaskBundle.model_validate(accepted.content.body)

    # --- 5. Execute.
    resolved_executor: Executor
    if executor is not None:
        resolved_executor = executor
    else:
        dispatch_table: CapabilityDispatchTable = build_dispatch_table(
            [], {_NODE_CAPABILITY_NAME: SystematicEvidenceSynthesisExecutor}
        )
        resolved_executor = dispatch(accepted_bundle, dispatch_table)

    resolved_inputs: dict[str, bytes] = {
        corpus_artifact_id: artifact_store.get(corpus_descriptor.content_hash),
        protocol_parameters_artifact_id: artifact_store.get(
            protocol_parameters_descriptor.content_hash
        ),
        method_protocol_artifact_id: artifact_store.get(method_protocol_descriptor.content_hash),
    }
    started_at = datetime.now(UTC)
    execution_result = resolved_executor.execute(
        accepted_bundle, resolved_inputs, execution_attempt=execution_attempt
    )
    ended_at = datetime.now(UTC)

    # --- 6. Deserialize the executor's own JSON output into real, persisted
    #        objects (this composition's own new step, between execute()
    #        returning and RunManifestRecorder.record).
    evidence_matrix_id: Urn | None = None
    claim_ids: list[Urn] = []
    method_ruling_ids: list[Urn] = []
    research_decision_ids: list[Urn] = []
    source_record_ids: list[Urn] = []
    evidence_anchor_ids: list[Urn] = []

    if execution_result.outcome == "completed" and execution_result.output is not None:
        output = json.loads(execution_result.output.decode("utf-8"))
        (
            evidence_matrix_id,
            claim_ids,
            method_ruling_ids,
            research_decision_ids,
            source_record_ids,
            evidence_anchor_ids,
        ) = _persist_synthesis_output(
            output,
            engine=engine,
            event_log=event_log,
            source_record_service=source_record_service,
            evidence_anchor_service=evidence_anchor_service,
            evidence_matrix_service=evidence_matrix_service,
            method_ruling_service=method_ruling_service,
            research_decision_service=research_decision_service,
            claim_service=claim_service,
            object_repository=object_repository,
            corpus_content_hash=corpus_descriptor.content_hash,
            method_protocol_id=method_protocol_id,
            practice_id=resolved_origin_practice_id,
            actor=resolved_actor,
            policy_version=policy_version,
            correlation_id=resolved_correlation_id,
        )

    # --- 7. Record the Run Manifest (unchanged, MTH-020).
    run_manifest_stored = run_manifest_recorder.record(
        execution_result,
        accepted_bundle,
        practice_id=resolved_node_practice_id,
        executor_id=resolved_node_id,
        executor_role="systematic-evidence-synthesis-executor",
        started_at=started_at,
        ended_at=ended_at,
        actor=resolved_node_id,
        policy_version=policy_version,
        correlation_id=resolved_correlation_id,
    )
    run_manifest = RunManifest.model_validate(run_manifest_stored.body)

    # --- 8. Seal the Evidence Crate (MTH-020). Since task-packets/
    #        E9-T00.yaml item 7, source_records/evidence_anchors/
    #        proposed_claims are passed through from the already-collected
    #        ids above — see the module docstring's now-closed disclosed
    #        gap ("EvidenceCrate.source_records/evidence_anchors/
    #        proposed_claims stay empty").
    artifact_refs: list[ArtifactRef] = list(input_artifact_refs)
    if execution_result.output is not None:
        output_descriptor = artifact_store.put(
            execution_result.output,
            media_type="application/json",
            producer_run_id=run_manifest.id,
            classification="PUBLIC",
            created_at=datetime.now(UTC),
        )
        artifact_refs.append(
            ArtifactRef(
                artifact_id=new_urn("artifact"),
                content_hash=output_descriptor.content_hash,
                classification="PUBLIC",
            )
        )

    failures: list[FailureEntry] = []
    if execution_result.outcome != "completed":
        category = _FAILURE_CATEGORY_BY_OUTCOME.get(execution_result.outcome, "unknown")
        failures.append(
            FailureEntry(
                code=f"E_RUN_{execution_result.outcome.upper()}",
                category=category,
                message=(
                    execution_result.detail
                    or f"run ended in terminal state {execution_result.outcome!r}"
                ),
            )
        )

    crate_stored = evidence_crate_sealer.seal(
        run_manifest,
        execution_result,
        accepted_bundle,
        artifact_refs=artifact_refs,
        node_signing_key=node_signing_key,
        node_key_id=node_key_id,
        signer_practice_id=resolved_node_practice_id,
        actor=resolved_node_id,
        policy_version=policy_version,
        correlation_id=resolved_correlation_id,
        failures=failures,
        source_records=source_record_ids,
        evidence_anchors=evidence_anchor_ids,
        proposed_claims=claim_ids,
    )
    crate = EvidenceCrate.model_validate(crate_stored.body)

    if evidence_matrix_id is not None:
        _write_governed_by_protocol_edge(
            engine,
            event_log,
            source_id=crate.id,
            protocol_id=method_protocol_id,
            actor=resolved_actor,
            policy_version=policy_version,
            correlation_id=resolved_correlation_id,
        )

    return SynthesisEvidenceLoopResult(
        evidence_crate_id=crate.id,
        run_manifest_id=run_manifest.id,
        task_id=accepted_bundle.id,
        research_score_id=score.id,
        node_id=resolved_node_id,
        output_hash=execution_result.output_hash,
        run_state=execution_result.outcome,
        is_deterministic=execution_result.is_deterministic,
        evidence_matrix_id=evidence_matrix_id,
        claim_ids=tuple(claim_ids),
        method_ruling_ids=tuple(method_ruling_ids),
        research_decision_ids=tuple(research_decision_ids),
    )


def _persistable_source_family_id(raw: str | None) -> Urn | None:
    """``SourceRecord.source_family_id``/``EvidenceMatrixRow.source_family_id``
    (``mrr.contracts``) both require a real ``Urn`` referencing an actually
    persisted ``SourceFamily`` object — but ``CorpusEntry.source_family_id``
    (``mrr.services.node_runtime.synthesis_executor``) is a plain,
    UNVALIDATED string, used purely as an opaque dedup key by
    ``mrr.domain.source_independence``'s pure counting step inside
    ``execute()`` (which never checks it is a URN at all). This packet does
    NOT create ``SourceFamily`` objects (out of scope; a future task's job)
    — so a caller-supplied grouping key that happens not to be a real URN
    (e.g. this packet's own small test fixtures use human-readable labels
    like ``"family-supported-1"``) is honestly represented as ``None`` on
    the PERSISTED record, rather than fabricating a reference to a
    ``SourceFamily`` that was never actually created. A grouping key that
    DOES happen to already be a real, valid URN (e.g. a future caller that
    already resolved a real ``SourceFamily`` upstream) is passed through
    unchanged.
    """
    if raw is not None and is_valid_urn(raw):
        return raw
    return None


def _persist_synthesis_output(
    output: dict[str, Any],
    *,
    engine: Engine,
    event_log: PostgresEventLog,
    source_record_service: SourceRecordService,
    evidence_anchor_service: EvidenceAnchorService,
    evidence_matrix_service: EvidenceMatrixService,
    method_ruling_service: MethodRulingService,
    research_decision_service: ResearchDecisionService,
    claim_service: ClaimService,
    object_repository: PostgresObjectRepository,
    corpus_content_hash: str,
    method_protocol_id: Urn,
    practice_id: Urn,
    actor: Urn,
    policy_version: str,
    correlation_id: Urn,
) -> tuple[Urn, list[Urn], list[Urn], list[Urn], list[Urn], list[Urn]]:
    """Deserialize ``SystematicEvidenceSynthesisExecutor``'s own canonical
    JSON output into real, persisted objects. See the module docstring for
    the full choreography this implements.

    Returns ``(matrix_id, claim_ids, method_ruling_ids,
    research_decision_ids, source_record_ids, evidence_anchor_ids)`` — the
    last two (task-packets/E9-T00.yaml item 7 wiring) are
    ``list(entry_ids_to_source_record_id.values())``/``list(entry_ids_to_
    evidence_anchor_id.values())``, the same ids this function already
    mints and persists for the frozen ``EvidenceMatrix``'s own rows, now
    ALSO surfaced to the caller so ``run_synthesis_evidence_loop`` can pass
    them through to ``EvidenceCrateSealer.seal()`` — zero new reads, zero
    new persisted state.
    """
    protocol_id = output["protocol_id"]
    question_id = output["question_id"]
    question_model = object_repository.get_latest(question_id)
    question_scope = Scope.model_validate(question_model.body.get("scope", {}))

    entry_ids_to_source_record_id: dict[str, Urn] = {}
    entry_ids_to_evidence_anchor_id: dict[str, Urn] = {}
    matrix_rows: list[EvidenceMatrixRow] = []

    for row in output["corpus_rows"]:
        if not row["included"]:
            continue
        entry_id = row["entry_id"]
        source_record_id = new_urn("source-record")
        entry_ids_to_source_record_id[entry_id] = source_record_id

        source_record_fields = row["source_record"]
        source_record = SourceRecord.model_validate(
            {
                "id": source_record_id,
                "api_version": "mrr/v1alpha1",
                "kind": "SourceRecord",
                "practice_id": practice_id,
                "revision": 1,
                "created_at": datetime.now(UTC),
                "created_by": actor,
                "content_hash": "sha256:" + "0" * 64,
                "identifiers": source_record_fields.get("identifiers", {}),
                "title": source_record_fields["title"],
                "creators": source_record_fields.get("creators", []),
                "publication_date": source_record_fields.get("publication_date"),
                "version": source_record_fields.get("version"),
                "retrieval_timestamp": source_record_fields["retrieval_timestamp"],
                "retrieval_method": source_record_fields["retrieval_method"],
                "snapshot_artifact_hash": corpus_content_hash,
                "source_type": source_record_fields["source_type"],
                "primary_secondary_derived": source_record_fields["primary_secondary_derived"],
                "source_family_id": _persistable_source_family_id(row.get("source_family_id")),
                "derivation_evidence": source_record_fields.get("derivation_evidence"),
                "accessibility": source_record_fields.get("accessibility"),
                "licensing": source_record_fields.get("licensing"),
            }
        )
        source_record = _finalize_content_hash(source_record)
        source_record_service.create(
            source_record, actor=actor, policy_version=policy_version, correlation_id=correlation_id
        )

        evidence_anchor_id: Urn | None = None
        if row["verification_status"] == "verified":
            evidence_anchor_id = new_urn("evidence-anchor")
            entry_ids_to_evidence_anchor_id[entry_id] = evidence_anchor_id
            anchor = EvidenceAnchor.model_validate(
                {
                    "id": evidence_anchor_id,
                    "api_version": "mrr/v1alpha1",
                    "kind": "EvidenceAnchor",
                    "practice_id": practice_id,
                    "revision": 1,
                    "created_at": datetime.now(UTC),
                    "created_by": actor,
                    "content_hash": "sha256:" + "0" * 64,
                    "relation": row["evidence_relation"],
                    "anchor_kind": "text",
                    "extraction_method": "human_curated_corpus_excerpt",
                    "extractor_id": actor,
                    "anchor_validation_status": "validated",
                    "source_record_id": source_record_id,
                    "snapshot_hash": corpus_content_hash,
                    "transformation_chain": [],
                }
            )
            anchor = _finalize_content_hash(anchor)
            evidence_anchor_service.create(
                anchor, actor=actor, policy_version=policy_version, correlation_id=correlation_id
            )

        matrix_rows.append(
            EvidenceMatrixRow.model_validate(
                {
                    "row_id": entry_id,
                    "source_record_id": source_record_id,
                    "evidence_anchor_id": evidence_anchor_id,
                    "source_family_id": _persistable_source_family_id(row.get("source_family_id")),
                    "verification_status": row["verification_status"],
                    "unverifiable_reason": row.get("unverifiable_reason"),
                    "claim_relevant_finding": row["claim_relevant_finding"],
                    "extraction": row.get("extraction", {}),
                }
            )
        )

    matrix_id = new_urn("evidence-matrix")
    matrix = EvidenceMatrix.model_validate(
        {
            "id": matrix_id,
            "api_version": "mrr/v1alpha1",
            "kind": "EvidenceMatrix",
            "practice_id": practice_id,
            "revision": 1,
            "created_at": datetime.now(UTC),
            "created_by": actor,
            "content_hash": "sha256:" + "0" * 64,
            "protocol_id": protocol_id,
            "question_id": question_id,
            "rows": [row.model_dump(mode="json") for row in matrix_rows],
            "status": "draft",
        }
    )
    matrix = _finalize_content_hash(matrix)
    evidence_matrix_service.create(
        matrix, actor=actor, policy_version=policy_version, correlation_id=correlation_id
    )
    evidence_matrix_service.activate(
        matrix_id, actor=actor, policy_version=policy_version, correlation_id=correlation_id
    )
    _write_governed_by_protocol_edge(
        engine,
        event_log,
        source_id=matrix_id,
        protocol_id=method_protocol_id,
        actor=actor,
        policy_version=policy_version,
        correlation_id=correlation_id,
    )

    claim_ids: list[Urn] = []
    method_ruling_ids: list[Urn] = []
    research_decision_ids: list[Urn] = []

    for analysis in output["analyses"]:
        applies_to_analysis = analysis["applies_to_analysis"]
        if analysis["outcome"] == "insufficient_evidence":
            decision_id = new_urn("research-decision")
            decision = ResearchDecision.model_validate(
                {
                    "id": decision_id,
                    "api_version": "mrr/v1alpha1",
                    "kind": "ResearchDecision",
                    "practice_id": practice_id,
                    "revision": 1,
                    "created_at": datetime.now(UTC),
                    "created_by": actor,
                    "content_hash": "sha256:" + "0" * 64,
                    "decision_type": "stop_insufficient_evidence",
                    "protocol_id": protocol_id,
                    "applies_to_analysis": applies_to_analysis,
                    "rationale": analysis["decision"]["rationale"],
                    "status": "issued",
                }
            )
            decision = _finalize_content_hash(decision)
            research_decision_service.create(
                decision, actor=actor, policy_version=policy_version, correlation_id=correlation_id
            )
            research_decision_ids.append(decision_id)
            continue

        candidate = analysis["claim_candidate"]
        evidence_relations = [
            entry_ids_to_evidence_anchor_id[entry_id]
            for entry_id in candidate["supporting_entry_ids"]
            if entry_id in entry_ids_to_evidence_anchor_id
        ]
        counterevidence_relations = [
            entry_ids_to_evidence_anchor_id[entry_id]
            for entry_id in candidate["contradicting_entry_ids"]
            if entry_id in entry_ids_to_evidence_anchor_id
        ]

        claim_id = new_urn("claim")
        claim_type: ClaimType = candidate["claim_type"]
        claim = Claim.model_validate(
            {
                "id": claim_id,
                "api_version": "mrr/v1alpha1",
                "kind": "Claim",
                "practice_id": practice_id,
                "revision": 1,
                "created_at": datetime.now(UTC),
                "created_by": actor,
                "content_hash": "sha256:" + "0" * 64,
                "assertion": candidate["assertion"],
                "claim_type": claim_type,
                "scope": question_scope.model_dump(mode="json"),
                "status": "draft",
                "evidence_relations": evidence_relations,
                "counterevidence_relations": counterevidence_relations,
                "dependencies": [],
                "source_family_ids": [],
                "uncertainty": [],
                "known_unknowns": [],
                "proposer_id": actor,
                "verification_ids": [],
                "correction_ids": [],
            }
        )
        claim = _finalize_content_hash(claim)
        claim_service.create(
            claim, actor=actor, policy_version=policy_version, correlation_id=correlation_id
        )

        # A "contested"/"unsupported" finding is driven all the way to the
        # same-named Claim.status — neither transition requires a
        # VerificationResult at the contract level. A "supported" finding
        # stays at "draft" (genuinely proposed) — see the module docstring's
        # "Claim status: reviewer_resolution overrides" section.
        if candidate["status"] == "contested":
            claim_service.submit_for_review(
                claim_id, actor=actor, policy_version=policy_version, correlation_id=correlation_id
            )
            claim_service.to_contested(
                claim_id, actor=actor, policy_version=policy_version, correlation_id=correlation_id
            )
        elif candidate["status"] == "unsupported":
            claim_service.submit_for_review(
                claim_id, actor=actor, policy_version=policy_version, correlation_id=correlation_id
            )
            claim_service.to_unsupported(
                claim_id, actor=actor, policy_version=policy_version, correlation_id=correlation_id
            )

        ruling_id = new_urn("method-ruling")
        ruling = MethodRuling.model_validate(
            {
                "id": ruling_id,
                "api_version": "mrr/v1alpha1",
                "kind": "MethodRuling",
                "practice_id": practice_id,
                "revision": 1,
                "created_at": datetime.now(UTC),
                "created_by": actor,
                "content_hash": "sha256:" + "0" * 64,
                "ruled_ceiling": candidate["ruled_ceiling"],
                "scope_of_validity": question_scope.model_dump(mode="json"),
                "non_applicability_conditions": candidate["non_applicability_conditions"],
                "ruling_basis": "deterministic_rule",
                "deterministic_rule_reference": candidate["deterministic_rule_reference"],
                "issued_by": actor,
                "protocol_id": protocol_id,
                "applies_to_analysis": applies_to_analysis,
                "status": "pending",
            }
        )
        ruling = _finalize_content_hash(ruling)
        method_ruling_service.create(
            ruling, actor=actor, policy_version=policy_version, correlation_id=correlation_id
        )
        method_ruling_service.issue(
            ruling_id, actor=actor, policy_version=policy_version, correlation_id=correlation_id
        )

        claim_service.attach_ruling(
            claim_id,
            ruling_id,
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
        )
        _write_governed_by_protocol_edge(
            engine,
            event_log,
            source_id=claim_id,
            protocol_id=method_protocol_id,
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
        )

        claim_ids.append(claim_id)
        method_ruling_ids.append(ruling_id)

    evidence_matrix_service.freeze(
        matrix_id, actor=actor, policy_version=policy_version, correlation_id=correlation_id
    )

    return (
        matrix_id,
        claim_ids,
        method_ruling_ids,
        research_decision_ids,
        list(entry_ids_to_source_record_id.values()),
        list(entry_ids_to_evidence_anchor_id.values()),
    )
