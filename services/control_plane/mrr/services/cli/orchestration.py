"""``run_local_evidence_loop`` (task-packets/E2-T07.yaml): the composition
function that drives one complete E2 local run — approve a Research Score,
register a node's capability, negotiate and execute a deterministic Task
Bundle, record the Run Manifest, and seal the Evidence Crate — with no
model/LLM dependency anywhere in the loop.

This module introduces **no new domain behavior**. Every write happens
through a merged E2 service exactly as its own integration tests wire it
(``mrr.services.research_score.service.ResearchScoreService``,
``mrr.services.capability_registry.service.CapabilityRegistry``,
``mrr.services.task_bundle.service.TaskBundleService`` /
``NodeTaskDecisionService``, ``mrr.services.node_runtime.executor
.ReferenceTaskExecutor``, ``mrr.services.node_runtime.run_manifest
.RunManifestRecorder``, ``mrr.services.node_runtime.evidence_crate
.EvidenceCrateSealer``); this function only builds the caller-supplied
objects those services require (a signed ``ResearchScore``/``NodeManifest``/
``TaskBundle``) and threads the ids each step returns into the next.

--- Two roles, one operator ------------------------------------------------

E2 is the single-node vertical slice: there is exactly one practice acting as
both the origin (proposing the score and the task) and the node (declaring
the capability, executing, and sealing the crate). ``origin_signing_key``
signs the ``TaskBundle`` at creation; ``node_signing_key`` signs the
``NodeManifest`` at registration and the ``EvidenceCrate`` at sealing. A
caller MAY pass the same key for both (a single-key deployment) or two
distinct keys (still one practice, but modeling the origin/node signing
identities as separate credentials) — this function does not require them to
differ, since ``NodeTaskDecisionService.accept`` verifies against whichever
public key the caller supplies as the bundle's current signer.

--- Hashing and signing convention ------------------------------------------

For every object this function itself constructs (``ResearchScore``,
``NodeManifest``, ``TaskBundle``) — as opposed to ``RunManifest``/
``EvidenceCrate``, which their own recording/sealing services mint
internally — this module computes the REAL ``content_hash`` via
``mrr.domain.hashing_policy.compute_content_hash`` (never leaves a
placeholder in place), and, for the two signed object kinds, a REAL Ed25519
signature via ``sign_object``. This mirrors
``mrr.services.node_runtime.evidence_crate.EvidenceCrateSealer.seal``
EXACTLY, including WHY it is built this specific way: build a draft Pydantic
model with a placeholder ``content_hash``/``signature.value``, hash/sign over
that draft's OWN ``model_dump(mode="json")`` output, then ``model_copy`` the
real values in. This is not merely stylistic — it is required for
correctness. If this module instead hand-built a plain ``dict`` with
manually-formatted ISO datetime strings and signed THAT, there would be no
guarantee that a later ``model_dump(mode="json")`` call on the
``model_validate``-reconstructed object reproduces byte-identical strings
(Pydantic's own datetime JSON serialization is the single source of truth for
that format; this module must never compete with it by formatting datetimes
itself). Every downstream signature check
(``CapabilityRegistry.register``, ``NodeTaskDecisionService.accept``) calls
``obj.model_dump(mode="json")`` on the very model instance (or a
``model_copy`` of it) this module built — by always deriving the signed
bytes from THAT SAME serializer, signing and verifying are guaranteed to
agree.
"""

from __future__ import annotations

import base64
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from mrr.contracts import (
    ArtifactRef,
    BaseObject,
    EvidenceCrate,
    FailureCategory,
    FailureEntry,
    NodeManifest,
    ResearchScore,
    RunManifest,
    Signature,
    TaskBundle,
    Urn,
)
from mrr.domain.artifacts import ArtifactStore
from mrr.domain.hashing_policy import compute_content_hash, sign_object
from mrr.domain.identity import new_urn
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.services.capability_registry.service import CapabilityRegistry
from mrr.services.capability_registry.service import bind_unit_of_work as _bind_capability_uow
from mrr.services.node_runtime.evidence_crate import EvidenceCrateSealer
from mrr.services.node_runtime.evidence_crate import bind_unit_of_work as _bind_crate_uow
from mrr.services.node_runtime.executor import Executor, ReferenceTaskExecutor, TerminalOutcome
from mrr.services.node_runtime.run_manifest import RunManifestRecorder
from mrr.services.node_runtime.run_manifest import bind_unit_of_work as _bind_manifest_uow
from mrr.services.research_score.service import ResearchScoreService
from mrr.services.research_score.service import bind_unit_of_work as _bind_score_uow
from mrr.services.task_bundle.service import NodeTaskDecisionService, TaskBundleService
from mrr.services.task_bundle.service import bind_event_unit_of_work as _bind_bundle_event_uow
from mrr.services.task_bundle.service import bind_unit_of_work as _bind_bundle_uow
from sqlalchemy import Engine

#: Reference input bytes used when a caller supplies none. Genuinely
#: arbitrary content — ``default_reference_transform`` (E2-T04) hashes
#: whatever bytes it is given; this default exists only so ``mrr run`` and a
#: bare ``run_local_evidence_loop()`` call have *something* deterministic to
#: feed the executor without requiring a caller-supplied file every time.
DEFAULT_INPUT_BYTES = b"mrr-e2-t07-reference-input-v1"

#: Defaults for the bundle's declared capability — the same reference
#: capability name/version this module registers on the node's manifest, so
#: ``TaskBundleService.create``'s ``CapabilityNotDeclaredError`` gate always
#: passes for an unmodified call.
DEFAULT_CAPABILITY_NAME = "reference.deterministic-transform"
DEFAULT_CAPABILITY_VERSION = "1.0.0"

#: ``TaskBundle.execution.image_digest`` is a required, schema-shaped
#: ``sha256:<64 hex>`` string, but ``ReferenceTaskExecutor`` never pulls or
#: runs a real OCI image (see its own HONESTY BOUNDARY docstring) — there is
#: no real digest to record. This placeholder follows the exact convention
#: the E2-T03/T06 test fixtures already use for the same reason
#: (``"sha256:" + "c" * 64``), not a fabricated claim of a real image: the
#: image this digest would name is never invoked by this executor.
_REFERENCE_IMAGE_DIGEST = "sha256:" + "c" * 64

#: Minimal, NOT spec-ratified mapping from a non-``completed``
#: ``TerminalOutcome`` to an ``EvidenceCrate`` ``FailureCategory``, needed
#: because ``EvidenceCrateSealer.seal`` requires the caller to construct
#: ``FailureEntry`` values itself (see that module's own docstring, "failures
#: /known_unknowns are caller-supplied, not auto-derived" — there is no
#: one-to-one mapping in the specification, and guessing one inside the
#: sealer would be inventing domain behavior). This is this task's own
#: minimal proposal, flagged in the PR as an open specification question,
#: exactly like ``mrr.services.task_bundle.service.RefusalReason``.
_FAILURE_CATEGORY_BY_OUTCOME: dict[str, FailureCategory] = {
    "failed": "execution_error",
    "timed_out": "execution_error",
    "cancelled": "execution_error",
    "policy_denied": "policy_denied",
    "partial": "unknown",
}


@dataclass(frozen=True, slots=True)
class LocalEvidenceLoopResult:
    """Every id a caller needs to independently resolve and verify the
    sealed crate this loop produced, without re-running anything.

    ``run_state``/``output_hash``/``is_deterministic`` mirror
    ``mrr.services.node_runtime.executor.ExecutionResult`` directly — this is
    the SAME outcome the executor produced, not a re-derived summary, so it
    can never disagree with what was actually recorded.
    """

    evidence_crate_id: Urn
    run_manifest_id: Urn
    task_id: Urn
    research_score_id: Urn
    node_id: Urn
    output_hash: str | None
    run_state: TerminalOutcome
    is_deterministic: bool


def _finalize_content_hash[T: BaseObject](draft: T) -> T:
    """Return a copy of ``draft`` with ``content_hash`` replaced by the real
    ``compute_content_hash`` value, computed over ``draft.model_dump(mode=
    "json")`` — the exact pattern
    ``EvidenceCrateSealer.seal``/``RunManifestRecorder.record`` already use
    internally. Whatever placeholder ``content_hash`` (or ``signature``)
    ``draft`` currently carries is irrelevant: ``compute_content_hash`` ->
    ``prepare_for_hash`` strips both fields before canonicalizing.
    """
    real_hash = compute_content_hash(draft.model_dump(mode="json"))
    return draft.model_copy(update={"content_hash": real_hash})


def _public_key_reference(public_key: Ed25519PublicKey) -> str:
    """A human-inspectable string naming a real Ed25519 public key — the raw
    32 verification-key bytes, standard base64-encoded, prefixed so the
    encoding is unambiguous. ``NodeManifest.public_keys`` is schema-typed as
    a plain ``list[str]`` with no format constraint (docs/spec/
    02_DOMAIN_MODEL.md section 2.2's own example is a bare ``did:key:...``
    placeholder); this module has no DID encoder available, so it records
    the actual verifying key material instead of inventing a DID string that
    would look authoritative but resolve to nothing.
    """
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return "ed25519-raw-base64:" + base64.b64encode(raw).decode("ascii")


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
            "content_hash": "sha256:" + "0" * 64,  # placeholder; recomputed below
            "question": (
                "Does the deterministic reference transform reproduce a stable, "
                "replayable output hash for the E2 single-node evidence loop?"
            ),
            "objectives": [
                "Exercise create/approve/activate through sealed-crate for one local run."
            ],
            "non_goals": [],
            "scope": {},
            "methods": {"allowed": [DEFAULT_CAPABILITY_NAME], "prohibited": []},
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
    """A material revision of an ``IN_REVIEW`` score adding one approval
    reference — the only route to satisfying ``ResearchScoreService.approve``
    's ``ApprovalRequiredError`` gate (task-packets/E2-T01.yaml: "APPROVED
    requires at least one recorded approval reference"). Mirrors the
    ``_create_and_approve_score`` helper every E2-T01/T03 integration test
    already uses. ``status`` is deliberately left untouched — a material
    revise must not itself change lifecycle status
    (``ResearchScoreService.revise`` rejects a status change).
    """
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
    capability_name: str,
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
            "content_hash": "sha256:" + "0" * 64,  # placeholder; recomputed below
            "node_id": node_id,
            "capabilities": [
                {
                    "name": capability_name,
                    "version": capability_version,
                    "input_schema": "urn:mrr:schema:reference-input:1",
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
            "public_keys": [_public_key_reference(node_signing_key.public_key())],
            "signature": placeholder_signature,
        }
    )
    draft = _finalize_content_hash(draft)
    # sign_object's prepare_for_signature strips only "signature" (keeping
    # the just-computed real content_hash) — the placeholder signature above
    # never influences what gets signed.
    signature_value = sign_object(node_signing_key, draft.model_dump(mode="json"))
    final_signature = placeholder_signature.model_copy(update={"value": signature_value})
    return draft.model_copy(update={"signature": final_signature})


def _build_task_bundle(
    *,
    origin_practice_id: str,
    target_node_id: str,
    research_score_id: str,
    research_score_revision: int,
    actor: str,
    capability_name: str,
    capability_version: str,
    input_artifact_ref: ArtifactRef,
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
            "content_hash": "sha256:" + "0" * 64,  # placeholder; recomputed below
            "origin_practice_id": origin_practice_id,
            "target_node_id": target_node_id,
            "research_score_id": research_score_id,
            "research_score_revision": research_score_revision,
            # No hypothesis-forest branch is generated here (E2-T07 forbidden_changes:
            # "the CLI references a branch_id but does not generate branches") — this
            # is a bare, otherwise-unbacked branch identifier, exactly as directed.
            "branch_id": new_urn("branch"),
            "capability": {"name": capability_name, "version": capability_version},
            "purpose": (
                "Run the bounded, deterministic reference computation for the E2 "
                "local evidence loop (task-packets/E2-T07.yaml)."
            ),
            "instructions": {"operation": "reference-transform"},
            "inputs": [input_artifact_ref],
            "data_access_mode": "read_local",
            "execution": {
                "image_digest": _REFERENCE_IMAGE_DIGEST,
                "entrypoint": ["mrr-reference-task"],
                "code_revision": code_revision,
            },
            "resource_limits": {
                "cpu": 1.0,
                "memory_mb": 64,
                "disk_mb": 16,
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
    draft = _finalize_content_hash(draft)
    signature_value = sign_object(origin_signing_key, draft.model_dump(mode="json"))
    final_signature = placeholder_signature.model_copy(update={"value": signature_value})
    return draft.model_copy(update={"signature": final_signature})


def run_local_evidence_loop(
    *,
    engine: Engine,
    artifact_store: ArtifactStore,
    origin_signing_key: Ed25519PrivateKey,
    node_signing_key: Ed25519PrivateKey,
    actor: Urn | None = None,
    policy_version: str = "policy-mrr-e2-local",
    correlation_id: Urn | None = None,
    origin_practice_id: Urn | None = None,
    node_practice_id: Urn | None = None,
    node_id: Urn | None = None,
    origin_key_id: str = "origin-key-1",
    node_key_id: str = "node-key-1",
    capability_name: str = DEFAULT_CAPABILITY_NAME,
    capability_version: str = DEFAULT_CAPABILITY_VERSION,
    input_bytes: bytes = DEFAULT_INPUT_BYTES,
    input_artifact_id: Urn | None = None,
    timeout_seconds: int = 30,
    code_revision: str | None = None,
    executor: Executor | None = None,
    execution_attempt: int = 1,
    approve_score: bool = True,
) -> LocalEvidenceLoopResult:
    """Compose the merged E2 services into one complete local evidence loop.

    In order: create + submit + revise(+approval) the Research Score, and —
    unless ``approve_score`` is ``False`` — approve and activate it; register
    the node's signed capability manifest; store the reference input bytes
    and build the declaring ``ArtifactRef``; create + offer the Task Bundle
    (gated by ``ResearchScoreService.ensure_can_start_work`` and the
    capability-declared check, both enforced INSIDE
    ``TaskBundleService.create``) and have the node accept it (verifying the
    origin's signature); execute via ``executor`` (a ``ReferenceTaskExecutor``
    by default); record the ``RunManifest``; seal the (node-signed)
    ``EvidenceCrate`` — for every terminal outcome, not only ``completed``
    (MRR-FR-050).

    Args:
        approve_score: when ``False``, the score is left ``IN_REVIEW`` (never
            approved) — ``TaskBundleService.create`` then raises
            ``mrr.domain.exceptions.ScoreNotApprovedError`` before anything
            else in the loop runs (MRR-FR-004's gate, exercised
            deliberately). No Task Bundle, Run Manifest, or Evidence Crate is
            created in that case; the exception propagates to the caller
            unmodified.
        executor: defaults to a plain ``ReferenceTaskExecutor()``. A caller
            (or a test exercising the policy-denied/timed-out paths) may pass
            one already configured with a ``policy_gate``, ``is_cancelled``,
            or a deliberately slow ``transform``.
        input_bytes: the reference task's declared input.
        input_artifact_id: the URN identifying the declared input artifact in
            ``TaskBundle.inputs``/the executor's ``inputs`` mapping. Defaults
            to a freshly minted ``new_urn("artifact")`` — appropriate for an
            ordinary run, where each invocation declares its own artifact
            identity. For the deterministic-replay invariant this task
            requires, a caller MUST hold this id constant (in addition to
            ``input_bytes``) across the two independent calls being compared:
            ``mrr.services.node_runtime.executor.default_reference_transform``
            hashes ``"<artifact_id>:<sha256(bytes)>"`` lines — it is
            deterministic in the FULL resolved ``inputs`` mapping (keys AND
            values), not merely in the byte content — so two runs that mint
            two different random artifact ids for otherwise-identical bytes
            are, from the executor's own point of view, two genuinely
            different inputs, and are not guaranteed (or expected) to produce
            the same output hash. Replaying "the same declared input"
            therefore means holding both its id and its bytes fixed.
        code_revision: the code/workflow revision to record
            (``TaskBundle.execution.code_revision`` -> ``RunManifest
            .code_commit`` -> ``EvidenceCrate.environment.code_revision``,
            MRR-FR-053). Caller-injected, never derived by this function —
            a research runtime must not depend on running inside a git
            working tree to know its own code version (there is none in a
            deployed container); ``mrr.services.cli.main`` resolves this
            from ``--code-revision`` or the ``MRR_CODE_COMMIT`` environment
            variable before calling here. Defaults to ``None``: an explicit,
            honest "unknown" (MRR-NFR-012), never a fabricated value. A run
            started with ``code_revision=None`` executes and records its Run
            Manifest exactly as usual, but ``EvidenceCrateSealer.seal`` then
            raises ``ValueError`` — no crate (of ANY terminal outcome, not
            only ``completed``) can be sealed without a real code revision;
            see that class's own docstring for why this is a deliberate
            explicit failure, not a gap this function papers over.

    Returns:
        A ``LocalEvidenceLoopResult`` naming the sealed crate, the run
        manifest, the task, the score, and the executing node, plus the
        executor's own terminal outcome/output hash/determinism flag.

    Raises:
        mrr.domain.exceptions.ScoreNotApprovedError: see ``approve_score``
            above.
        ValueError: ``code_revision`` is ``None`` (see above) — raised by
            ``EvidenceCrateSealer.seal``, propagated unmodified.
        Any other typed error a composed service itself raises (e.g.
        ``mrr.crypto.exceptions.SignatureVerificationError``,
        ``mrr.domain.exceptions.CapabilityNotDeclaredError``) propagates
        unmodified — this function adds no new error handling of its own.
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
    resolved_input_artifact_id = (
        input_artifact_id if input_artifact_id is not None else new_urn("artifact")
    )
    resolved_executor: Executor = executor if executor is not None else ReferenceTaskExecutor()

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

    # --- 1. Research Score: create -> submit_for_review -> revise(+approval)
    #        -> [approve -> activate] (docs/spec/06_IMPLEMENTATION_PLAN.md E2
    #        exit criteria; MRR-FR-004's gate).
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

    # --- 2. Register the node's signed capability manifest (CapabilityRegistry;
    #        signature verified INSIDE register(), fails closed on tampering).
    node_manifest = _build_node_manifest(
        node_id=resolved_node_id,
        node_practice_id=resolved_node_practice_id,
        actor=resolved_actor,
        capability_name=capability_name,
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

    # --- 3. Store the reference input bytes; mint the ArtifactRef the bundle declares.
    input_descriptor = artifact_store.put(
        input_bytes,
        media_type="application/octet-stream",
        producer_run_id=resolved_correlation_id,
        classification="PUBLIC",
        created_at=datetime.now(UTC),
    )
    input_artifact_ref = ArtifactRef(
        artifact_id=resolved_input_artifact_id,
        content_hash=input_descriptor.content_hash,
        classification="PUBLIC",
    )

    # --- 4. Create + offer + accept the Task Bundle. create() internally gates on
    #        ensure_can_start_work (MRR-FR-004) and the capability-declared check
    #        (MRR-FR-021) before writing anything; accept() verifies the origin's
    #        signature (MRR-FR-031) before recording the ACCEPTED transition.
    #
    #        research_score_revision records the score's last MATERIAL revision
    #        (the one carrying the approval, `revised.revision`) rather than
    #        whatever status revision is current by the time the bundle is
    #        accepted — TaskBundleService.create only checks the score's CURRENT
    #        status (not this field), so this is documentary provenance about
    #        which content the task was scoped against, not an enforced match.
    bundle = _build_task_bundle(
        origin_practice_id=resolved_origin_practice_id,
        target_node_id=resolved_node_id,
        research_score_id=score.id,
        research_score_revision=revised.revision,
        actor=resolved_actor,
        capability_name=capability_name,
        capability_version=capability_version,
        input_artifact_ref=input_artifact_ref,
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
    # Execute the CONTENT actually persisted (still revision 1 — offer()/accept()
    # are event-only per ADR-0007), not the local `bundle` variable, so the
    # executed bundle is provably the one the origin signed and the node accepted.
    accepted_bundle = TaskBundle.model_validate(accepted.content.body)

    # --- 5. Execute (ReferenceTaskExecutor by default — deterministic, no LLM).
    resolved_inputs: dict[str, bytes] = {
        input_artifact_ref.artifact_id: artifact_store.get(input_descriptor.content_hash)
    }
    started_at = datetime.now(UTC)
    execution_result = resolved_executor.execute(
        accepted_bundle, resolved_inputs, execution_attempt=execution_attempt
    )
    ended_at = datetime.now(UTC)

    # --- 6. Record the Run Manifest — sealed for every terminal outcome (MRR-FR-042/043).
    run_manifest_stored = run_manifest_recorder.record(
        execution_result,
        accepted_bundle,
        practice_id=resolved_node_practice_id,
        executor_id=resolved_node_id,
        executor_role="reference-task-executor",
        started_at=started_at,
        ended_at=ended_at,
        actor=resolved_node_id,
        policy_version=policy_version,
        correlation_id=resolved_correlation_id,
    )
    run_manifest = RunManifest.model_validate(run_manifest_stored.body)

    # --- 7. Seal the Evidence Crate — again, for every terminal outcome (MRR-FR-050).
    artifact_refs: list[ArtifactRef] = []
    if execution_result.output is not None:
        output_descriptor = artifact_store.put(
            execution_result.output,
            media_type="application/octet-stream",
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
    )
    # Round trip through the schema-shaped persisted body, mirroring every
    # other step's own "reconstruct from what was actually stored" pattern —
    # confirms the crate this function reports is exactly what
    # EvidenceCrateSealer persisted, not merely what it was asked to build.
    crate = EvidenceCrate.model_validate(crate_stored.body)

    return LocalEvidenceLoopResult(
        evidence_crate_id=crate.id,
        run_manifest_id=run_manifest.id,
        task_id=accepted_bundle.id,
        research_score_id=score.id,
        node_id=resolved_node_id,
        output_hash=execution_result.output_hash,
        run_state=execution_result.outcome,
        is_deterministic=execution_result.is_deterministic,
    )
