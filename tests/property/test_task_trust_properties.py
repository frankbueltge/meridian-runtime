"""Property test: ``mrr.domain.task_trust.resolve_trusted_task_key`` never
returns a key for any of its four failing preconditions (task-packets/
E5-T04.yaml acceptance test: "fail-closed matrix, each a distinct typed
error, nothing decided/written" — proved here at the resolver level: the
resolver never returns a key for a failing precondition, returns only on
all-hold).

Ed25519 keypairs have no seedable hypothesis strategy exposed by the
``cryptography`` library (mirroring
tests/property/test_manifest_trust_properties.py's own rationale), so each
example generates a fresh keypair directly; ``hypothesis`` drives which of
the six concrete fault kinds is injected into an otherwise fully
self-consistent, genuinely signed scenario. A positive control
(``test_resolver_returns_the_key_when_every_precondition_holds``) proves the
same construction, with no fault injected, DOES resolve — so the property
below is not vacuously true of a construction that always fails regardless
of input.

Local, deliberate duplicate of tests/unit/domain/test_task_trust.py's own
fixture builders — this codebase's established convention for independent
test tiers.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from hypothesis import given
from hypothesis import strategies as st
from mrr.contracts.practice import Practice
from mrr.contracts.task_bundle import TaskBundle
from mrr.crypto.exceptions import SignatureVerificationError
from mrr.crypto.keys import derive_key_id, encode_public_key, generate_ed25519_keypair
from mrr.domain.exceptions import (
    TaskKeyNotValidError,
    TaskSignerMismatchError,
    UnknownKeyIdError,
)
from mrr.domain.hashing_policy import sign_object
from mrr.domain.identity import new_urn
from mrr.domain.task_trust import practice_key_ring, resolve_trusted_task_key

_NOW = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)
_VALID_FROM = _NOW - timedelta(days=1)
_VALID_UNTIL = _NOW + timedelta(days=365)

#: The six concrete ways ``resolve_trusted_task_key`` must fail closed — the
#: packet's own named fail-closed matrix, minus the happy path.
_FAULT_KINDS = (
    "unknown_kid",
    "revoked",
    "rotated",
    "expired",
    "not_yet_valid",
    "signer_mismatch",
    "tampered",
)


def _key_entry(
    public_key: Ed25519PublicKey,
    *,
    valid_from: datetime = _VALID_FROM,
    valid_until: datetime = _VALID_UNTIL,
    state: str = "active",
) -> dict[str, Any]:
    return {
        "kid": derive_key_id(public_key),
        "algorithm": "Ed25519",
        "encoded_public_key": encode_public_key(public_key),
        "valid_from": valid_from,
        "valid_until": valid_until,
        "state": state,
    }


def _practice(*, practice_id: str, keys: list[dict[str, Any]]) -> Practice:
    data: dict[str, Any] = {
        "id": practice_id,
        "api_version": "mrr/v1alpha1",
        "kind": "Practice",
        "practice_id": practice_id,
        "revision": 1,
        "created_at": _NOW,
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "name": "Fixture Practice",
        "description": "Fixture practice for task trust property tests.",
        "keys": keys,
        "governance_contacts": ["mailto:governance@fixture.invalid"],
        "supported_policy_versions": ["policy-2026-07-01"],
        "disclosure": {"max_disclosure": "PUBLIC", "trust_statement": "fixture"},
    }
    return Practice.model_validate(data)


def _bundle(*, signer_practice_id: str, key_id: str) -> TaskBundle:
    now = _NOW
    data: dict[str, Any] = {
        "id": new_urn("task-bundle"),
        "api_version": "mrr/v1alpha1",
        "kind": "TaskBundle",
        "practice_id": signer_practice_id,
        "revision": 1,
        "created_at": now,
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "origin_practice_id": signer_practice_id,
        "target_node_id": new_urn("node"),
        "research_score_id": new_urn("research-score"),
        "research_score_revision": 1,
        "branch_id": new_urn("branch"),
        "capability": {"name": "statistics.recompute", "version": "1.0.0"},
        "purpose": "Recompute the summary statistics for the confirmatory branch.",
        "instructions": {"note": "run the standard pipeline"},
        "inputs": [],
        "data_access_mode": "read_local",
        "execution": {
            "image_digest": "sha256:" + "c" * 64,
            "entrypoint": ["run.sh"],
        },
        "resource_limits": {"cpu": 1.0, "memory_mb": 512, "disk_mb": 100, "timeout_seconds": 60},
        "network_policy": {"mode": "deny_all", "allowlist": []},
        "output_schema": "urn:mrr:schema:evidence-crate:1",
        "classification": "PUBLIC",
        "approval_requirement": "automatic",
        "expires_at": now + timedelta(days=1),
        "nonce": "n" * 16,
        "signature": {
            "signer_practice_id": signer_practice_id,
            "key_id": key_id,
            "algorithm": "Ed25519",
            "signed_at": now,
            "value": "0" * 44,
        },
        "status": "CREATED",
    }
    return TaskBundle.model_validate(data)


def _sign(bundle: TaskBundle, private_key: Ed25519PrivateKey) -> TaskBundle:
    signature_value = sign_object(
        private_key, json.loads(bundle.model_dump_json(exclude_none=True))
    )
    return bundle.model_copy(
        update={"signature": bundle.signature.model_copy(update={"value": signature_value})}
    )


@given(fault=st.sampled_from(_FAULT_KINDS))
def test_resolver_never_returns_a_key_for_a_failing_precondition(fault: str) -> None:
    private_key, public_key = generate_ed25519_keypair()
    practice_id = new_urn("practice")

    if fault == "expired":
        entry = _key_entry(
            public_key, valid_from=_NOW - timedelta(days=10), valid_until=_NOW - timedelta(days=1)
        )
    elif fault == "not_yet_valid":
        entry = _key_entry(
            public_key, valid_from=_NOW + timedelta(days=1), valid_until=_NOW + timedelta(days=10)
        )
    elif fault == "revoked":
        entry = _key_entry(public_key, state="revoked")
    elif fault == "rotated":
        entry = _key_entry(public_key, state="rotated")
    else:
        entry = _key_entry(public_key)

    practice = _practice(practice_id=practice_id, keys=[entry])
    bundle = _bundle(signer_practice_id=practice_id, key_id=entry["kid"])
    signed = _sign(bundle, private_key)
    ring = practice_key_ring(practice)
    trusted_practice_id = practice_id

    expected_error: type[Exception]
    if fault == "unknown_kid":
        signed = signed.model_copy(
            update={
                "signature": signed.signature.model_copy(update={"key_id": "kid:entirely-unknown"})
            }
        )
        expected_error = UnknownKeyIdError
    elif fault in ("revoked", "rotated", "expired", "not_yet_valid"):
        expected_error = TaskKeyNotValidError
    elif fault == "signer_mismatch":
        trusted_practice_id = new_urn("practice")
        expected_error = TaskSignerMismatchError
    elif fault == "tampered":
        signed = signed.model_copy(update={"purpose": "mutated after signing"})
        expected_error = SignatureVerificationError
    else:  # pragma: no cover - defensive; _FAULT_KINDS is the only source
        raise AssertionError(f"unhandled fault kind: {fault!r}")

    with pytest.raises(expected_error):
        resolve_trusted_task_key(signed, trusted_practice_id, ring, at=_NOW)


def test_resolver_returns_the_key_when_every_precondition_holds() -> None:
    """Positive control for the property above: the identical construction,
    with no fault injected, DOES resolve — proving the fault branches are
    what causes rejection, not an always-failing construction.
    """
    private_key, public_key = generate_ed25519_keypair()
    practice_id = new_urn("practice")
    entry = _key_entry(public_key)
    practice = _practice(practice_id=practice_id, keys=[entry])
    bundle = _bundle(signer_practice_id=practice_id, key_id=entry["kid"])
    signed = _sign(bundle, private_key)
    ring = practice_key_ring(practice)

    resolved = resolve_trusted_task_key(signed, practice_id, ring, at=_NOW)

    assert resolved.public_bytes_raw() == public_key.public_bytes_raw()
