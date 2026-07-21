"""``establish_and_run_synthesis`` (task-packets/K1-T04.yaml): the composition
function for the FIRST REAL ``systematic_evidence_synthesis`` v1 run — the
model-collapse question over the two pinned, hash-verified atlas snapshots
(``corpora/model-collapse/``).

Unlike ``mrr.services.cli.synthesis_orchestration.run_synthesis_evidence_loop``
(K1-T03, UNCHANGED, called here exactly as it already exposes itself), this
function does not assume a ``QuestionModel``/``ConceptCharter``/locked
``MethodProtocol`` already exist — it PROPOSES and DRIVES them through their
own real, event-emitting lifecycles first, so this first real run does not
bypass governance with a raw, test-only ``_seed_generic``-style insert
(objective (b)/(c)).

--- Choreography (derived_decisions (f)) -----------------------------------

1. ``MethodProfileService.propose``+``.accept`` for a fresh ``MethodProfile``
   (or resolve a caller-supplied already-accepted ``method_profile_id``) —
   ``MethodProfileService`` itself is reused entirely UNCHANGED.
2. A LOCAL capability-name guard (derived_decisions (h)): assert
   ``CAPABILITY_NAME in accepted_profile.executor_task_family``, raising
   before any further object is created if it does not — catching an
   accidental drift between the semantic capability name and the profile's
   own declaration, WITHOUT touching ``NodeManifest`` or inventing a general
   synonym registry (that general fix stays deferred, per PR #52's own
   review and this packet's own derived_decisions (h)).
3. ``QuestionModelService.propose``+``.accept`` from this packet's own
   drafted content (``corpora/model-collapse/question-model.proposal.json``).
4. ``ConceptCharterService.propose``+``.accept`` from this packet's own
   drafted content (``concept-charter.proposal.json``), followed by ONE
   ``operationalizes`` edge write (``ConceptCharter.id -> QuestionModel.id``,
   derived_decisions (g) — an object-level, not entry-to-term, edge; see
   that decision's own "disclosed, not a clean fit" note).
5. ``MethodProtocolService.create``+``.submit_for_review``+``.lock`` from
   this packet's own drafted content (``method-protocol.proposal.json``),
   referencing the profile id from step 1.
6. ``run_synthesis_evidence_loop`` (K1-T03, UNCHANGED) with the resulting
   ``question_model_id``/``method_protocol_id``, the real ``corpus_entries``
   derived from the pinned atlas snapshots, and the ``protocol_parameters``
   sidecar (with ``protocol_id``/``protocol_lock_content_hash`` filled in
   from the just-locked protocol's own real id/content_hash).

Extraction stays entirely model-free throughout (derived_decisions (j)): no
``executor``/``extraction_callable`` override is injected unless the caller
explicitly supplies one — ``run_synthesis_evidence_loop``'s own default
(``executor=None`` -> dispatch-table resolution of
``SystematicEvidenceSynthesisExecutor`` with ``extraction_callable=None``) is
used, exactly like K1-T03's own headline acceptance path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.contracts import (
    BaseObject,
    ConceptCharter,
    MethodProfile,
    MethodProtocol,
    QuestionModel,
    Urn,
)
from mrr.domain.artifacts import ArtifactStore
from mrr.domain.hashing_policy import compute_content_hash
from mrr.domain.identity import new_urn
from mrr.domain.repositories import EDGE_VOCABULARY, TypedEdge
from mrr.persistence.repositories import (
    PostgresEventLog,
    PostgresObjectRepository,
)
from mrr.persistence.tables import edges_table
from mrr.provenance.events import DomainEvent
from mrr.services.cli.synthesis_orchestration import (
    SynthesisEvidenceLoopResult,
    run_synthesis_evidence_loop,
)
from mrr.services.concept_charter.service import ConceptCharterService
from mrr.services.concept_charter.service import bind_unit_of_work as _bind_charter_uow
from mrr.services.method_profile.service import MethodProfileService
from mrr.services.method_profile.service import bind_unit_of_work as _bind_profile_uow
from mrr.services.method_protocol.service import MethodProtocolService
from mrr.services.method_protocol.service import bind_unit_of_work as _bind_protocol_uow
from mrr.services.node_runtime.executor import Executor
from mrr.services.node_runtime.synthesis_executor import CAPABILITY_NAME
from mrr.services.question_model.service import QuestionModelService
from mrr.services.question_model.service import bind_unit_of_work as _bind_question_uow
from sqlalchemy import Engine

__all__ = [
    "CapabilityNameNotDeclaredError",
    "DEFAULT_METHOD_PROFILE_BODY",
    "EstablishAndRunSynthesisResult",
    "establish_and_run_synthesis",
]

#: The one ``operationalizes`` typed edge this composition writes
#: (``ConceptCharter.id -> QuestionModel.id`` — object-level, derived_decisions
#: (g)).
_OPERATIONALIZES_EDGE_TYPE = "operationalizes"

#: The canonical ``MethodProfile`` body this run proposes when the caller does
#: not supply its own content or an already-accepted ``method_profile_id`` —
#: reused verbatim from ``examples/method-profile.example.json`` (K0-T01,
#: already accepted), the SAME capability name and executor_steps K1-T03's
#: own executor declares, not redesigned here.
DEFAULT_METHOD_PROFILE_BODY: dict[str, Any] = {
    "profile_key": "systematic_evidence_synthesis",
    "version": "1.0.0",
    "claim_types": ["observational", "interpretive"],
    "max_claim_ceiling": "associational_unadjusted",
    "protocol_form": "synthesis_protocol",
    "executor_task_family": [CAPABILITY_NAME],
    "executor_steps": [
        {"name": "snapshot_loading_and_hash_verification", "kind": "deterministic"},
        {"name": "inclusion_filtering", "kind": "deterministic"},
        {"name": "extraction_and_classification_proposal", "kind": "model_assisted"},
        {"name": "matrix_assembly", "kind": "deterministic"},
        {"name": "independence_validation", "kind": "deterministic"},
        {"name": "eligibility_and_ceiling_rules", "kind": "deterministic"},
        {"name": "crate_sealing", "kind": "deterministic"},
    ],
    "inappropriate_uses": [
        "producing causal or mechanism-strength claims beyond associational_unadjusted "
        "(MRR-MTH-006)",
        "questions requiring primary data collection rather than synthesis of "
        "already-existing evidence",
        "confirmatory causal inference over external real-world datasets (spec 08 section 7, "
        "not scheduled)",
    ],
}


class CapabilityNameNotDeclaredError(ValueError):
    """Raised by ``establish_and_run_synthesis`` (derived_decisions (h)) when
    the accepted (fresh or caller-supplied) ``MethodProfile``'s own
    ``executor_task_family`` does not declare
    ``mrr.services.node_runtime.synthesis_executor.CAPABILITY_NAME`` — a
    LOCAL, cheap runtime assertion catching an accidental drift between the
    semantic capability name and the profile's own declaration, raised
    BEFORE any ``QuestionModel``/``ConceptCharter``/``MethodProtocol`` is
    created. This is explicitly NOT the general capability-name pattern-
    conflict fix PR #52's review flagged (``NodeManifest.CapabilityDefinition
    .name``'s pattern vs. the real ``CAPABILITY_NAME``) — that remains
    deferred, left for a future, dedicated task.
    """

    def __init__(self, profile_id: str, *, declared_capabilities: list[str]) -> None:
        self.profile_id = profile_id
        self.declared_capabilities = declared_capabilities
        super().__init__(
            f"MethodProfile {profile_id!r} does not declare capability "
            f"{CAPABILITY_NAME!r} in its own executor_task_family "
            f"{declared_capabilities!r} — refusing to establish a QuestionModel/"
            "ConceptCharter/MethodProtocol against a profile that cannot dispatch "
            "to the systematic_evidence_synthesis v1 executor"
        )


@dataclass(frozen=True, slots=True)
class EstablishAndRunSynthesisResult:
    """Every id a caller needs to independently resolve and verify the full
    crate this run produced end to end — ``SynthesisEvidenceLoopResult``
    (K1-T03) extended with this packet's own four new governance-object ids.
    """

    method_profile_id: Urn
    question_model_id: Urn
    concept_charter_id: Urn
    method_protocol_id: Urn
    evidence_crate_id: Urn
    run_manifest_id: Urn
    task_id: Urn
    research_score_id: Urn
    node_id: Urn
    output_hash: str | None
    run_state: str
    is_deterministic: bool
    evidence_matrix_id: Urn | None
    claim_ids: tuple[Urn, ...]
    method_ruling_ids: tuple[Urn, ...]
    research_decision_ids: tuple[Urn, ...]


# ---------------------------------------------------------------------------
# Local helpers — content-hash finalization (a local copy, not a shared
# import of mrr.services.cli.synthesis_orchestration._finalize_content_hash,
# mirroring that module's own "local copy across separate modules"
# precedent) and the operationalizes edge writer (mirroring
# _write_governed_by_protocol_edge's own shape exactly, differing only in
# edge_type/event name).
# ---------------------------------------------------------------------------


def _finalize_content_hash[T: BaseObject](draft: T) -> T:
    body: dict[str, Any] = json.loads(draft.model_dump_json(exclude_none=True))
    real_hash = compute_content_hash(body)
    return draft.model_copy(update={"content_hash": real_hash})


def _write_operationalizes_edge(
    engine: Engine,
    event_log: PostgresEventLog,
    *,
    concept_charter_id: Urn,
    question_model_id: Urn,
    actor: Urn,
    policy_version: str,
    correlation_id: Urn,
) -> TypedEdge:
    """Insert one ``operationalizes`` edge (``concept_charter_id ->
    question_model_id``) plus one
    ``concept_charter.operationalizes_recorded`` domain event, atomically —
    a local copy of
    ``mrr.services.cli.synthesis_orchestration._write_governed_by_protocol_edge``'s
    own shape (derived_decisions (g)), differing only in ``edge_type``/event
    name.
    """
    if _OPERATIONALIZES_EDGE_TYPE not in EDGE_VOCABULARY:  # pragma: no cover - defensive
        raise ValueError(f"unknown edge type: {_OPERATIONALIZES_EDGE_TYPE!r}")

    now = datetime.now(UTC)
    edge = TypedEdge(
        id=new_urn("edge"),
        source_id=concept_charter_id,
        target_id=question_model_id,
        edge_type=_OPERATIONALIZES_EDGE_TYPE,
        created_at=now,
        created_by=actor,
        scope=None,
        status="active",
        practice_id=None,
    )
    event = DomainEvent(
        id=new_urn("domain-event"),
        event_type="concept_charter.operationalizes_recorded",
        occurred_at=now,
        actor=actor,
        policy_version=policy_version,
        causation_id=None,
        correlation_id=correlation_id,
        object_id=concept_charter_id,
        object_revision=1,
        payload={"edge_id": edge.id, "question_model_id": question_model_id},
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


def _build_method_profile(body: dict[str, Any], *, practice_id: Urn, actor: Urn) -> MethodProfile:
    now = datetime.now(UTC)
    draft = MethodProfile.model_validate(
        {
            "id": new_urn("method-profile"),
            "api_version": "mrr/v1alpha1",
            "kind": "MethodProfile",
            "practice_id": practice_id,
            "revision": 1,
            "created_at": now,
            "created_by": actor,
            "content_hash": "sha256:" + "0" * 64,
            "status": "draft",
            **body,
        }
    )
    return _finalize_content_hash(draft)


def _build_question_model(body: dict[str, Any], *, practice_id: Urn, actor: Urn) -> QuestionModel:
    now = datetime.now(UTC)
    draft = QuestionModel.model_validate(
        {
            "id": new_urn("question-model"),
            "api_version": "mrr/v1alpha1",
            "kind": "QuestionModel",
            "practice_id": practice_id,
            "revision": 1,
            "created_at": now,
            "created_by": actor,
            "content_hash": "sha256:" + "0" * 64,
            "status": "draft",
            **body,
        }
    )
    return _finalize_content_hash(draft)


def _build_concept_charter(body: dict[str, Any], *, practice_id: Urn, actor: Urn) -> ConceptCharter:
    now = datetime.now(UTC)
    draft = ConceptCharter.model_validate(
        {
            "id": new_urn("concept-charter"),
            "api_version": "mrr/v1alpha1",
            "kind": "ConceptCharter",
            "practice_id": practice_id,
            "revision": 1,
            "created_at": now,
            "created_by": actor,
            "content_hash": "sha256:" + "0" * 64,
            "status": "draft",
            **body,
        }
    )
    return _finalize_content_hash(draft)


def _build_method_protocol(
    body: dict[str, Any], *, practice_id: Urn, actor: Urn, profile_id: Urn
) -> MethodProtocol:
    now = datetime.now(UTC)
    draft = MethodProtocol.model_validate(
        {
            "id": new_urn("method-protocol"),
            "api_version": "mrr/v1alpha1",
            "kind": "MethodProtocol",
            "practice_id": practice_id,
            "revision": 1,
            "created_at": now,
            "created_by": actor,
            "content_hash": "sha256:" + "0" * 64,
            "profile_id": profile_id,
            "locked_at": None,
            "locked_by": None,
            "amendment": None,
            "status": "draft",
            **body,
        }
    )
    return _finalize_content_hash(draft)


# ---------------------------------------------------------------------------
# The composition function.
# ---------------------------------------------------------------------------


def establish_and_run_synthesis(
    *,
    engine: Engine,
    artifact_store: ArtifactStore,
    origin_signing_key: Ed25519PrivateKey,
    node_signing_key: Ed25519PrivateKey,
    question_model: dict[str, Any],
    concept_charter: dict[str, Any],
    method_protocol: dict[str, Any],
    corpus_entries: list[dict[str, Any]],
    protocol_parameters: dict[str, Any],
    method_profile: dict[str, Any] | None = None,
    method_profile_id: Urn | None = None,
    actor: Urn | None = None,
    policy_version: str = "policy-mrr-k1-t04-first-real-run",
    correlation_id: Urn | None = None,
    practice_id: Urn | None = None,
    origin_practice_id: Urn | None = None,
    node_practice_id: Urn | None = None,
    node_id: Urn | None = None,
    origin_key_id: str = "origin-key-1",
    node_key_id: str = "node-key-1",
    capability_version: str = "1.0.0",
    timeout_seconds: int = 30,
    code_revision: str | None = None,
    executor: Executor | None = None,
    execution_attempt: int = 1,
    approve_score: bool = True,
) -> EstablishAndRunSynthesisResult:
    """Establish the real prerequisite governance objects for the
    model-collapse question (task-packets/K1-T04.yaml), then run
    ``run_synthesis_evidence_loop`` (K1-T03, UNCHANGED) over them. See the
    module docstring for the full six-step choreography.

    Args:
        question_model: body-only ``QuestionModel`` proposal content (e.g.
            ``corpora/model-collapse/question-model.proposal.json``, parsed)
            — no ``id``/``status``/audit fields; those are minted here.
        concept_charter: body-only ``ConceptCharter`` proposal content.
        method_protocol: body-only ``MethodProtocol`` proposal content —
            ``profile_id`` is filled in automatically from the resolved
            ``MethodProfile``; do not include it in the caller-supplied dict.
        corpus_entries: the real corpus (e.g.
            ``corpora/model-collapse/corpus-entries.json``, parsed) — each
            dict shaped per
            ``mrr.services.node_runtime.synthesis_executor.CorpusEntry``.
        protocol_parameters: the protocol-parameters sidecar (e.g.
            ``corpora/model-collapse/protocol-parameters.sidecar.json``,
            parsed) — ``protocol_id``/``protocol_lock_content_hash`` are
            OVERWRITTEN here with the just-locked protocol's own real
            id/content_hash; any caller-supplied placeholder values are
            discarded.
        method_profile: body-only ``MethodProfile`` content for a FRESH
            profile this call proposes+accepts. Ignored if
            ``method_profile_id`` is given. Defaults to
            ``DEFAULT_METHOD_PROFILE_BODY`` (the same content
            ``examples/method-profile.example.json`` already carries,
            accepted).
        method_profile_id: an ALREADY-ACCEPTED ``MethodProfile`` id to reuse
            instead of proposing a fresh one, mirroring
            ``run_synthesis_evidence_loop``'s own "caller-supplied executor
            override" flexibility precedent (derived_decisions (f)).

    Raises:
        CapabilityNameNotDeclaredError: the resolved (fresh or
            caller-supplied) ``MethodProfile``'s ``executor_task_family``
            does not declare ``CAPABILITY_NAME`` — raised before any
            ``QuestionModel``/``ConceptCharter``/``MethodProtocol`` is
            created (derived_decisions (h)).
        ValueError: ``method_profile_id`` is given but does not resolve to
            an ``accepted`` ``MethodProfile``.

    Returns:
        Every id a reviewer needs to independently resolve and verify the
        crate end to end.
    """
    resolved_actor = actor if actor is not None else new_urn("agent-role")
    resolved_correlation_id = (
        correlation_id if correlation_id is not None else new_urn("research-run")
    )
    resolved_practice_id = practice_id if practice_id is not None else new_urn("practice")
    resolved_origin_practice_id = (
        origin_practice_id if origin_practice_id is not None else resolved_practice_id
    )

    object_repository = PostgresObjectRepository(engine)
    event_log = PostgresEventLog(engine)

    method_profile_service = MethodProfileService(
        object_repository, event_log, _bind_profile_uow(engine, object_repository, event_log)
    )
    question_model_service = QuestionModelService(
        object_repository, event_log, _bind_question_uow(engine, object_repository, event_log)
    )
    concept_charter_service = ConceptCharterService(
        object_repository, event_log, _bind_charter_uow(engine, object_repository, event_log)
    )
    method_protocol_service = MethodProtocolService(
        object_repository, event_log, _bind_protocol_uow(engine, object_repository, event_log)
    )

    # --- 1. Resolve/establish an accepted MethodProfile.
    if method_profile_id is not None:
        resolved_profile_id = method_profile_id
        accepted_profile_body = object_repository.get_latest(method_profile_id).body
        if accepted_profile_body["status"] != "accepted":
            raise ValueError(
                f"method_profile_id {method_profile_id!r} is not accepted "
                f"(status={accepted_profile_body['status']!r})"
            )
    else:
        profile_body = method_profile if method_profile is not None else DEFAULT_METHOD_PROFILE_BODY
        draft_profile = _build_method_profile(
            profile_body, practice_id=resolved_practice_id, actor=resolved_actor
        )
        method_profile_service.propose(
            draft_profile,
            actor=resolved_actor,
            policy_version=policy_version,
            correlation_id=resolved_correlation_id,
        )
        accepted_stored = method_profile_service.accept(
            draft_profile.id,
            actor=resolved_actor,
            policy_version=policy_version,
            correlation_id=resolved_correlation_id,
        )
        resolved_profile_id = draft_profile.id
        accepted_profile_body = accepted_stored.body

    # --- 2. The local capability-name guard (derived_decisions (h)) — raised
    #        BEFORE any QuestionModel/ConceptCharter/MethodProtocol is
    #        created.
    declared_capabilities = list(accepted_profile_body["executor_task_family"])
    if CAPABILITY_NAME not in declared_capabilities:
        raise CapabilityNameNotDeclaredError(
            resolved_profile_id, declared_capabilities=declared_capabilities
        )

    # --- 3. QuestionModel: propose + accept.
    draft_question_model = _build_question_model(
        question_model, practice_id=resolved_practice_id, actor=resolved_actor
    )
    question_model_service.propose(
        draft_question_model,
        actor=resolved_actor,
        policy_version=policy_version,
        correlation_id=resolved_correlation_id,
    )
    question_model_service.accept(
        draft_question_model.id,
        actor=resolved_actor,
        policy_version=policy_version,
        correlation_id=resolved_correlation_id,
    )
    resolved_question_model_id = draft_question_model.id

    # --- 4. ConceptCharter: propose + accept, then the operationalizes edge.
    draft_concept_charter = _build_concept_charter(
        concept_charter, practice_id=resolved_practice_id, actor=resolved_actor
    )
    concept_charter_service.propose(
        draft_concept_charter,
        actor=resolved_actor,
        policy_version=policy_version,
        correlation_id=resolved_correlation_id,
    )
    concept_charter_service.accept(
        draft_concept_charter.id,
        actor=resolved_actor,
        policy_version=policy_version,
        correlation_id=resolved_correlation_id,
    )
    resolved_concept_charter_id = draft_concept_charter.id
    _write_operationalizes_edge(
        engine,
        event_log,
        concept_charter_id=resolved_concept_charter_id,
        question_model_id=resolved_question_model_id,
        actor=resolved_actor,
        policy_version=policy_version,
        correlation_id=resolved_correlation_id,
    )

    # --- 5. MethodProtocol: create + submit_for_review + lock.
    protocol_body = dict(method_protocol)
    protocol_body.pop("profile_id", None)
    draft_protocol = _build_method_protocol(
        protocol_body,
        practice_id=resolved_practice_id,
        actor=resolved_actor,
        profile_id=resolved_profile_id,
    )
    method_protocol_service.create(
        draft_protocol,
        actor=resolved_actor,
        policy_version=policy_version,
        correlation_id=resolved_correlation_id,
    )
    method_protocol_service.submit_for_review(
        draft_protocol.id,
        actor=resolved_actor,
        policy_version=policy_version,
        correlation_id=resolved_correlation_id,
    )
    locked_stored = method_protocol_service.lock(
        draft_protocol.id,
        actor=resolved_actor,
        policy_version=policy_version,
        correlation_id=resolved_correlation_id,
    )
    resolved_method_protocol_id = draft_protocol.id
    locked_content_hash = locked_stored.content_hash

    # --- 6. Fill in the protocol-parameters sidecar's own protocol_id/
    #        protocol_lock_content_hash from the JUST-LOCKED protocol's real
    #        id/content_hash, discarding any caller-supplied placeholder.
    final_protocol_parameters = dict(protocol_parameters)
    final_protocol_parameters["protocol_id"] = resolved_method_protocol_id
    final_protocol_parameters["protocol_lock_content_hash"] = locked_content_hash

    loop_kwargs: dict[str, Any] = {
        "engine": engine,
        "artifact_store": artifact_store,
        "origin_signing_key": origin_signing_key,
        "node_signing_key": node_signing_key,
        "question_model_id": resolved_question_model_id,
        "method_protocol_id": resolved_method_protocol_id,
        "corpus_entries": corpus_entries,
        "protocol_parameters": final_protocol_parameters,
        "actor": resolved_actor,
        "policy_version": policy_version,
        "correlation_id": resolved_correlation_id,
        "origin_practice_id": resolved_origin_practice_id,
        "node_practice_id": node_practice_id,
        "node_id": node_id,
        "origin_key_id": origin_key_id,
        "node_key_id": node_key_id,
        "capability_version": capability_version,
        "timeout_seconds": timeout_seconds,
        "code_revision": code_revision,
        "executor": executor,
        "execution_attempt": execution_attempt,
        "approve_score": approve_score,
    }

    loop_result: SynthesisEvidenceLoopResult = run_synthesis_evidence_loop(**loop_kwargs)

    return EstablishAndRunSynthesisResult(
        method_profile_id=resolved_profile_id,
        question_model_id=resolved_question_model_id,
        concept_charter_id=resolved_concept_charter_id,
        method_protocol_id=resolved_method_protocol_id,
        evidence_crate_id=loop_result.evidence_crate_id,
        run_manifest_id=loop_result.run_manifest_id,
        task_id=loop_result.task_id,
        research_score_id=loop_result.research_score_id,
        node_id=loop_result.node_id,
        output_hash=loop_result.output_hash,
        run_state=loop_result.run_state,
        is_deterministic=loop_result.is_deterministic,
        evidence_matrix_id=loop_result.evidence_matrix_id,
        claim_ids=loop_result.claim_ids,
        method_ruling_ids=loop_result.method_ruling_ids,
        research_decision_ids=loop_result.research_decision_ids,
    )
