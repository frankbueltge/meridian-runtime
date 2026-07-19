"""Property test: the ADR-0004 "gap 2" proof (task-packets/E5-T00.yaml and
its completeness follow-up, task-packets/E5-T00b.yaml).

ADR-0004 (docs/spec/adr/ADR-0004-CANONICAL-OBJECT-SERIALIZATION.md) pins one
canonical pre-canonicalization form -- schema-conformant JSON with absent or
``None`` optional fields OMITTED (``exclude_none=True``), never emitted as
JSON ``null`` -- for hashing, signing, AND persistence of every first-class
MRR object. Before task-packets/E5-T00.yaml, the three cross-practice signed
objects (``NodeManifest``, ``TaskBundle``, ``EvidenceCrate``) signed over a
DIFFERENT, null-including form than their own persisted ``body`` used --
"gap 2" in the ADR's own words. This module proves the gap is closed, for
each of the three signed object types, with at least one optional field left
unset:

- the canonical bytes the signature covers EQUAL the canonical bytes of the
  persisted body with ``signature`` removed -- proved directly by
  ``verify_object_signature`` succeeding against the persisted body itself
  (Ed25519 verification only succeeds against the exact message that was
  signed, so success IS the byte-equality proof, not merely implied by it);
- the persisted body contains no JSON ``null`` for the absent optional;
- the object still verifies.

It also proves the deliberate, tested break the ADR-0004 flip introduces: a
signature produced over the OLD null-including ``model_dump(mode="json")``
form does NOT verify under the new (``exclude_none=True``) verify path --
no silent dual-accept of the old form.

These tests exercise the REAL production sign call sites --
``mrr.services.cli.orchestration._build_node_manifest``/``_build_task_bundle``
and ``mrr.services.node_runtime.evidence_crate.EvidenceCrateSealer.seal`` --
not a hand-rolled reimplementation of the signing recipe, so a regression at
any of those three sign sites would fail this test. ``EvidenceCrate`` has no
production verify path yet (its verify wiring is E5-T05's scope, per
task-packets/E5-T00.yaml's forbidden_changes); ``verify_object_signature`` is
called directly here as the "local check" the packet's own acceptance test
describes -- never a production call site.

``ResearchScore`` carries no ``signature`` field, so it was never one of
the three signed objects E5-T00 unified -- but its own ``content_hash``
was, until E5-T00b, still computed over the null-including form by the CLI
orchestration helper ``_finalize_content_hash``, diverging from its
``exclude_none`` persisted body (ADR-0004's "gap 2" for a non-signed
object). The tests at the bottom of this module exercise the real
``_build_research_score`` and prove ``content_hash`` now equals
``compute_content_hash`` of the object's own ``exclude_none`` body, plus a
regression proving the old null-including hash genuinely differs (the fix
is not a no-op).

Ed25519 keys are generated once at module scope, not per hypothesis example
-- the same rationale tests/property/test_signature_roundtrip_properties.py
documents: the property under test is about the canonicalize/sign/verify
pipeline, not key generation, and reusing keypairs keeps the run fast.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from _json_strategies import json_text
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from hypothesis import given
from hypothesis import strategies as st
from mrr.contracts import ArtifactRef, EvidenceCrate, NodeManifest, RunManifest, TaskBundle
from mrr.crypto.exceptions import SignatureVerificationError
from mrr.domain.hashing_policy import compute_content_hash, sign_object, verify_object_signature
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.cli.orchestration import (
    _build_node_manifest,
    _build_research_score,
    _build_task_bundle,
)
from mrr.services.node_runtime.evidence_crate import EvidenceCrateSealer
from mrr.services.node_runtime.executor import ExecutionResult, ResourceUsage
from mrr.services.node_runtime.run_manifest import RunManifestRecorder

_POLICY_VERSION = "policy-2026-07-19"
_CAPABILITY_NAME = "reference.deterministic-transform"

_NODE_SIGNING_KEY = Ed25519PrivateKey.generate()
_ORIGIN_SIGNING_KEY = Ed25519PrivateKey.generate()
_CRATE_SIGNING_KEY = Ed25519PrivateKey.generate()


# ---------------------------------------------------------------------------
# Minimal DB-free fake unit of work for EvidenceCrateSealer/RunManifestRecorder
# -- local duplicate of tests/unit/services/node_runtime/test_evidence_crate.py's
# own fake (this codebase's established per-test-module convention; see that
# module's docstring).
# ---------------------------------------------------------------------------


class _FakeUnitOfWork:
    def __call__(
        self,
        obj: StoredObject,
        expected_current_revision: int | None,
        event: DomainEvent,
    ) -> tuple[StoredObject, AppendedEvent]:
        appended = AppendedEvent(
            event=event,
            sequence=1,
            content_hash=f"sha256:{'b' * 64}",
            prev_hash=None,
        )
        return obj, appended


# ---------------------------------------------------------------------------
# NodeManifest: sign site is _build_node_manifest. Its own only object-level
# optional field (``data_residency``) and the two BaseObject optionals
# (``supersedes``, ``labels``) are never set by that helper -- always
# absent, never emitted as JSON null.
# ---------------------------------------------------------------------------


def _fresh_node_manifest(*, capability_version: str, node_key_id: str) -> NodeManifest:
    return _build_node_manifest(
        node_id=new_urn("node"),
        node_practice_id=new_urn("practice"),
        actor=new_urn("agent-role"),
        capability_name=_CAPABILITY_NAME,
        capability_version=capability_version,
        node_signing_key=_NODE_SIGNING_KEY,
        node_key_id=node_key_id,
    )


@given(capability_version=json_text(min_size=1), node_key_id=json_text(min_size=1))
def test_node_manifest_signed_bytes_equal_persisted_body_minus_signature(
    capability_version: str, node_key_id: str
) -> None:
    manifest = _fresh_node_manifest(capability_version=capability_version, node_key_id=node_key_id)
    persisted_body = json.loads(manifest.model_dump_json(exclude_none=True))

    for absent_optional in ("data_residency", "supersedes", "labels"):
        assert absent_optional not in persisted_body  # omitted, never null

    # The gap-2 proof: verify_object_signature only succeeds against the
    # exact canonical bytes that were signed, so success here proves those
    # bytes equal the persisted body's own canonical bytes (minus signature).
    verify_object_signature(
        _NODE_SIGNING_KEY.public_key(),
        persisted_body,
        manifest.signature.value,
        algorithm=manifest.signature.algorithm,
    )


def test_node_manifest_old_null_including_signature_does_not_verify() -> None:
    manifest = _fresh_node_manifest(capability_version="1.0.0", node_key_id="node-key-old-form")
    persisted_body = json.loads(manifest.model_dump_json(exclude_none=True))

    # A signature produced over the OLD null-including form.
    old_form_signature = sign_object(_NODE_SIGNING_KEY, manifest.model_dump(mode="json"))

    with pytest.raises(SignatureVerificationError):
        verify_object_signature(
            _NODE_SIGNING_KEY.public_key(),
            persisted_body,
            old_form_signature,
            algorithm="Ed25519",
        )


# ---------------------------------------------------------------------------
# TaskBundle: sign site is _build_task_bundle. ``execution.code_revision``
# is a real, exposed optional -- hypothesis toggles it None/set directly.
# ---------------------------------------------------------------------------


def _fresh_task_bundle(
    *,
    capability_version: str,
    origin_key_id: str,
    code_revision: str | None,
    timeout_seconds: int,
) -> TaskBundle:
    return _build_task_bundle(
        origin_practice_id=new_urn("practice"),
        target_node_id=new_urn("node"),
        research_score_id=new_urn("research-score"),
        research_score_revision=1,
        actor=new_urn("agent-role"),
        capability_name=_CAPABILITY_NAME,
        capability_version=capability_version,
        input_artifact_ref=ArtifactRef(
            artifact_id=new_urn("artifact"),
            content_hash="sha256:" + "a" * 64,
            classification="PUBLIC",
        ),
        timeout_seconds=timeout_seconds,
        origin_signing_key=_ORIGIN_SIGNING_KEY,
        origin_key_id=origin_key_id,
        code_revision=code_revision,
    )


@given(
    capability_version=json_text(min_size=1),
    origin_key_id=json_text(min_size=1),
    code_revision=st.one_of(st.none(), json_text(min_size=1)),
    timeout_seconds=st.integers(min_value=1, max_value=600),
)
def test_task_bundle_signed_bytes_equal_persisted_body_minus_signature(
    capability_version: str,
    origin_key_id: str,
    code_revision: str | None,
    timeout_seconds: int,
) -> None:
    bundle = _fresh_task_bundle(
        capability_version=capability_version,
        origin_key_id=origin_key_id,
        code_revision=code_revision,
        timeout_seconds=timeout_seconds,
    )
    persisted_body = json.loads(bundle.model_dump_json(exclude_none=True))

    for absent_optional in ("supersedes", "labels"):
        assert absent_optional not in persisted_body  # omitted, never null
    if code_revision is None:
        assert "code_revision" not in persisted_body["execution"]
    else:
        assert persisted_body["execution"]["code_revision"] == code_revision

    verify_object_signature(
        _ORIGIN_SIGNING_KEY.public_key(),
        persisted_body,
        bundle.signature.value,
        algorithm=bundle.signature.algorithm,
    )


def test_task_bundle_old_null_including_signature_does_not_verify() -> None:
    bundle = _fresh_task_bundle(
        capability_version="1.0.0",
        origin_key_id="origin-key-old-form",
        code_revision=None,  # the absent optional the old form would emit as null
        timeout_seconds=30,
    )
    persisted_body = json.loads(bundle.model_dump_json(exclude_none=True))

    old_form_signature = sign_object(_ORIGIN_SIGNING_KEY, bundle.model_dump(mode="json"))

    with pytest.raises(SignatureVerificationError):
        verify_object_signature(
            _ORIGIN_SIGNING_KEY.public_key(),
            persisted_body,
            old_form_signature,
            algorithm="Ed25519",
        )


# ---------------------------------------------------------------------------
# EvidenceCrate: sign site is EvidenceCrateSealer.seal. Every top-level
# property is schema-required except the two BaseObject optionals
# (``supersedes``, ``labels``), which the sealer never sets -- always
# absent. No production verify path exists yet (E5-T05); the checks below
# are the "local check" the acceptance test describes.
# ---------------------------------------------------------------------------


def _minimal_task_bundle() -> TaskBundle:
    now = datetime.now(UTC)
    return TaskBundle.model_validate(
        {
            "id": new_urn("task-bundle"),
            "api_version": "mrr/v1alpha1",
            "kind": "TaskBundle",
            "practice_id": new_urn("practice"),
            "revision": 1,
            "created_at": now,
            "created_by": new_urn("agent-role"),
            "content_hash": "sha256:" + "a" * 64,
            "origin_practice_id": new_urn("practice"),
            "target_node_id": new_urn("node"),
            "research_score_id": new_urn("research-score"),
            "research_score_revision": 1,
            "branch_id": new_urn("branch"),
            "capability": {"name": _CAPABILITY_NAME, "version": "1.0.0"},
            "purpose": "Run the bounded, deterministic reference computation.",
            "instructions": {"operation": "noop"},
            "inputs": [],
            "data_access_mode": "none",
            "execution": {
                "image_digest": "sha256:" + "c" * 64,
                "entrypoint": ["run.sh"],
                "code_revision": "git:property-test-fixture",
            },
            "resource_limits": {"cpu": 1.0, "memory_mb": 64, "disk_mb": 16, "timeout_seconds": 5},
            "network_policy": {"mode": "deny_all", "allowlist": []},
            "output_schema": "urn:mrr:schema:evidence-crate:1",
            "classification": "PUBLIC",
            "approval_requirement": "automatic",
            "expires_at": now + timedelta(days=1),
            "nonce": "n" * 16,
            "signature": {
                "signer_practice_id": new_urn("practice"),
                "key_id": "origin-key",
                "algorithm": "Ed25519",
                "signed_at": now,
                "value": "0" * 44,
            },
            "status": "RUNNING",
        }
    )


def _fresh_evidence_crate_body(*, node_key_id: str, known_unknowns: list[str]) -> dict[str, Any]:
    bundle = _minimal_task_bundle()
    result = ExecutionResult(
        outcome="completed",
        output=b"property-test-output",
        output_hash="sha256:" + "e" * 64,
        is_deterministic=True,
        execution_attempt=1,
        task_id=bundle.id,
        task_revision=bundle.revision,
        resource_usage=ResourceUsage(wall_time_seconds=0.1),
        detail=None,
    )
    now = datetime.now(UTC)
    manifest_stored = RunManifestRecorder(_FakeUnitOfWork()).record(
        result,
        bundle,
        practice_id=new_urn("practice"),
        executor_id=new_urn("executor"),
        executor_role="reference-task-executor",
        started_at=now,
        ended_at=now + timedelta(seconds=1),
        actor=new_urn("executor"),
        policy_version=_POLICY_VERSION,
        correlation_id=new_urn("research-run"),
    )
    run_manifest = RunManifest.model_validate(manifest_stored.body)

    crate_stored = EvidenceCrateSealer(_FakeUnitOfWork()).seal(
        run_manifest,
        result,
        bundle,
        node_signing_key=_CRATE_SIGNING_KEY,
        node_key_id=node_key_id,
        signer_practice_id=new_urn("practice"),
        actor=new_urn("executor"),
        policy_version=_POLICY_VERSION,
        correlation_id=new_urn("research-run"),
        known_unknowns=known_unknowns,
    )
    return crate_stored.body


@given(
    node_key_id=json_text(min_size=1), known_unknowns=st.lists(json_text(min_size=1), max_size=3)
)
def test_evidence_crate_signed_bytes_equal_persisted_body_minus_signature(
    node_key_id: str, known_unknowns: list[str]
) -> None:
    persisted_body = _fresh_evidence_crate_body(
        node_key_id=node_key_id, known_unknowns=known_unknowns
    )

    for absent_optional in ("supersedes", "labels"):
        assert absent_optional not in persisted_body  # omitted, never null

    # Local check only -- EvidenceCrate has no production verify path yet
    # (E5-T05). Success proves the gap-2 byte-equality for this object type.
    verify_object_signature(
        _CRATE_SIGNING_KEY.public_key(),
        persisted_body,
        persisted_body["signature"]["value"],
        algorithm=persisted_body["signature"]["algorithm"],
    )


def test_evidence_crate_old_null_including_signature_does_not_verify() -> None:
    persisted_body = _fresh_evidence_crate_body(node_key_id="node-key-old-form", known_unknowns=[])
    crate = EvidenceCrate.model_validate(persisted_body)

    old_form_signature = sign_object(_CRATE_SIGNING_KEY, crate.model_dump(mode="json"))

    with pytest.raises(SignatureVerificationError):
        verify_object_signature(
            _CRATE_SIGNING_KEY.public_key(),
            persisted_body,
            old_form_signature,
            algorithm="Ed25519",
        )


# ---------------------------------------------------------------------------
# ResearchScore (task-packets/E5-T00b.yaml, the completeness follow-up):
# content-hash site is _build_research_score. ResearchScore carries no
# signature field, so this is a content_hash-only property, not a
# signed-bytes one -- but the same gap-2 shape applies: content_hash must
# equal compute_content_hash of the exclude_none persisted body.
# ---------------------------------------------------------------------------


def test_research_score_content_hash_equals_hash_of_its_own_persisted_body() -> None:
    score = _build_research_score(practice_id=new_urn("practice"), actor=new_urn("agent-role"))
    persisted_body = json.loads(score.model_dump_json(exclude_none=True))

    # revision 1's supersedes/labels (inherited BaseObject optionals) are
    # never set by _build_research_score -- always absent, never JSON null.
    for absent_optional in ("supersedes", "labels"):
        assert absent_optional not in persisted_body

    assert score.content_hash == compute_content_hash(persisted_body)


def test_research_score_old_null_including_hash_no_longer_matches_the_stored_hash() -> None:
    """Regression: proves the E5-T00b fix is real, not a no-op -- the OLD
    null-including hash a signer/auditor on the pre-fix code would have
    computed genuinely differs from the NEW stored content_hash, for a
    ResearchScore with an absent optional (``supersedes``/``labels``).
    """
    score = _build_research_score(practice_id=new_urn("practice"), actor=new_urn("agent-role"))

    old_form_hash = compute_content_hash(score.model_dump(mode="json"))

    assert old_form_hash != score.content_hash
