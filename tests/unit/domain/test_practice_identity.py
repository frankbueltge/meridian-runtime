"""Unit tests for mrr.domain.practice_identity (task-packets/E5-T11.yaml).

Covers this module's own named acceptance criteria at the domain layer,
mirroring tests/unit/domain/test_envelope_signing.py's structure for its
own sibling module:

- No ``kid``/``key_id``/``encoded_public_key`` parameter exists on
  ``build_self_signed_practice`` at all — inspected via ``inspect.
  signature``, the only key input being the private key itself.
- The produced ``kid``/``keys[0].encoded_public_key`` equal
  ``derive_key_id``/``encode_public_key`` of ``private_key.public_key()``,
  both recomputed independently in this test, never read back from the
  function's own output and merely re-asserted.
- The self-signature verifies against the UNCHANGED
  ``verify_object_signature`` under the public half of the SAME private
  key, ``signature.signer_practice_id`` equals the practice's own ``id``
  (and its own ``practice_id``), and ``signature.key_id`` equals
  ``keys[0].kid``.
- ``content_hash`` equals ``compute_content_hash`` of the produced object,
  recomputed independently in this test.
- Reproducibility: identical inputs (including ``practice_id`` and
  ``created_at``) yield a byte-identical document — no wall clock, no
  randomness anywhere in this module.
- Every owner-content field is a required keyword argument with no
  default — omitting one is a plain ``TypeError``.
- No private key material (raw bytes or PEM text) appears anywhere in the
  produced document's own serialized form.
- Contract-level validation (an invalid validity window, an empty name)
  propagates unwrapped as ``pydantic.ValidationError``.
- The produced document round-trips through the UNCHANGED
  ``practice_key_ring`` — the small, direct proof that this module's output
  is shaped exactly as that existing consumer expects (the FULL sharp-case
  proof, through a real signed bundle, lives in
  tests/integration/services/test_practice_accepts_own_bundle.py).
"""

from __future__ import annotations

import ast
import base64
import inspect
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.contracts.practice import Practice
from mrr.contracts.research_score import MaxDisclosure
from mrr.crypto.keys import (
    decode_public_key,
    derive_key_id,
    encode_public_key,
    generate_ed25519_keypair,
)
from mrr.domain import practice_identity
from mrr.domain.hashing_policy import compute_content_hash, verify_object_signature
from mrr.domain.identity import new_urn
from mrr.domain.manifest_trust import practice_key_ring
from mrr.domain.practice_identity import build_self_signed_practice
from pydantic import ValidationError

_NOW = datetime(2026, 7, 26, 9, 0, 0, tzinfo=UTC)
_VALID_FROM = _NOW - timedelta(days=1)
_VALID_UNTIL = _NOW + timedelta(days=365)


# ---------------------------------------------------------------------------
# Fixture builder (deliberately local — this codebase's own convention of
# duplicating small fixture builders per test tier; see
# tests/unit/domain/test_envelope_signing.py's own precedent).
# ---------------------------------------------------------------------------


def _build(
    private_key: Ed25519PrivateKey,
    *,
    practice_id: str | None = None,
    created_at: datetime = _NOW,
    created_by: str | None = None,
    name: str = "Fixture Practice",
    description: str = "A fixture practice for domain-layer unit tests.",
    governance_contacts: list[str] | None = None,
    supported_policy_versions: list[str] | None = None,
    max_disclosure: MaxDisclosure = "PUBLIC",
    trust_statement: str = "fixture",
    valid_from: datetime = _VALID_FROM,
    valid_until: datetime = _VALID_UNTIL,
    capability_registry_endpoint: str | None = None,
) -> Practice:
    return build_self_signed_practice(
        private_key,
        practice_id=practice_id if practice_id is not None else new_urn("practice"),
        created_at=created_at,
        created_by=created_by if created_by is not None else new_urn("agent-role"),
        name=name,
        description=description,
        governance_contacts=(
            governance_contacts
            if governance_contacts is not None
            else ["mailto:governance@fixture.invalid"]
        ),
        supported_policy_versions=(
            supported_policy_versions
            if supported_policy_versions is not None
            else ["policy-2026-07-01"]
        ),
        max_disclosure=max_disclosure,
        trust_statement=trust_statement,
        valid_from=valid_from,
        valid_until=valid_until,
        capability_registry_endpoint=capability_registry_endpoint,
    )


# ---------------------------------------------------------------------------
# The one hard rule: no kid/key_id/encoded_public_key parameter exists.
# ---------------------------------------------------------------------------


def test_no_kid_or_key_id_or_encoded_public_key_parameter_exists() -> None:
    """task-packets/E5-T11.yaml acceptance criteria: "the domain function's
    signature is inspected in a test and accepts no kid, no key id, and no
    encoded public key; the only key input is the private key." Inspected
    directly against the REAL function signature, not merely asserted.
    """
    parameters = inspect.signature(build_self_signed_practice).parameters
    assert "kid" not in parameters
    assert "key_id" not in parameters
    assert "encoded_public_key" not in parameters

    key_typed_parameters = [
        name
        for name, param in parameters.items()
        if param.annotation in ("Ed25519PrivateKey", Ed25519PrivateKey)
    ]
    assert key_typed_parameters == ["private_key"]


# ---------------------------------------------------------------------------
# kid/encoded_public_key are derived, independently recomputed here.
# ---------------------------------------------------------------------------


def test_kid_and_encoded_public_key_are_derived_from_the_private_key_alone() -> None:
    private_key, public_key = generate_ed25519_keypair()

    practice = _build(private_key)

    assert len(practice.keys) == 1
    key = practice.keys[0]
    assert key.kid == derive_key_id(public_key)
    assert key.encoded_public_key == encode_public_key(public_key)


# ---------------------------------------------------------------------------
# The self-signature verifies under the UNCHANGED verify_object_signature.
# ---------------------------------------------------------------------------


def test_self_signature_verifies_and_carries_the_right_identities() -> None:
    private_key, public_key = generate_ed25519_keypair()

    practice = _build(private_key)

    assert practice.signature is not None
    assert practice.signature.signer_practice_id == practice.id
    assert practice.practice_id == practice.id
    assert practice.signature.key_id == practice.keys[0].kid

    body = practice.model_dump(mode="json", exclude_none=True)
    # Should not raise: the same UNCHANGED function every receiver-side
    # trust resolver in this codebase composes with a resolved key.
    verify_object_signature(
        public_key, body, practice.signature.value, algorithm=practice.signature.algorithm
    )


def test_self_signature_does_not_verify_under_an_unrelated_key() -> None:
    private_key, _ = generate_ed25519_keypair()
    _, unrelated_public_key = generate_ed25519_keypair()

    practice = _build(private_key)
    assert practice.signature is not None
    body = practice.model_dump(mode="json", exclude_none=True)

    with pytest.raises(Exception):  # noqa: B017 - mrr.crypto's own SignatureVerificationError
        verify_object_signature(
            unrelated_public_key,
            body,
            practice.signature.value,
            algorithm=practice.signature.algorithm,
        )


# ---------------------------------------------------------------------------
# content_hash is independently recomputed, never merely read back.
# ---------------------------------------------------------------------------


def test_content_hash_equals_independently_recomputed_compute_content_hash() -> None:
    private_key, _ = generate_ed25519_keypair()

    practice = _build(private_key)

    body = practice.model_dump(mode="json", exclude_none=True)
    assert practice.content_hash == compute_content_hash(body)


# ---------------------------------------------------------------------------
# Reproducibility: no clock, no randomness anywhere in this module.
# ---------------------------------------------------------------------------


def test_identical_inputs_yield_byte_identical_practice() -> None:
    private_key, _ = generate_ed25519_keypair()
    practice_id = new_urn("practice")
    created_by = new_urn("agent-role")

    practice_a = _build(
        private_key, practice_id=practice_id, created_at=_NOW, created_by=created_by
    )
    practice_b = _build(
        private_key, practice_id=practice_id, created_at=_NOW, created_by=created_by
    )

    assert practice_a.model_dump_json(exclude_none=True) == practice_b.model_dump_json(
        exclude_none=True
    )
    assert practice_a.content_hash == practice_b.content_hash
    assert practice_a.signature is not None
    assert practice_b.signature is not None
    assert practice_a.signature.value == practice_b.signature.value


def test_module_reads_no_wall_clock_and_mints_no_identity() -> None:
    """AGENTS.md rule 3 / task-packets/E5-T11.yaml acceptance criteria: "No
    wall clock and no randomness inside the domain function." Checked
    against the module's own ACTUAL source (via ``ast``), not merely
    asserted in prose.
    """
    tree = ast.parse(inspect.getsource(practice_identity))
    call_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                call_names.add(func.attr)
            elif isinstance(func, ast.Name):
                call_names.add(func.id)

    assert "now" not in call_names
    assert "new_urn" not in call_names
    assert "uuid4" not in call_names

    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_names.add(node.module)
    assert "uuid" not in imported_names
    assert "secrets" not in imported_names
    assert "ulid" not in imported_names


# ---------------------------------------------------------------------------
# Owner content: required, no defaults.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing",
    [
        "practice_id",
        "created_at",
        "created_by",
        "name",
        "description",
        "governance_contacts",
        "supported_policy_versions",
        "max_disclosure",
        "trust_statement",
        "valid_from",
        "valid_until",
    ],
)
def test_omitting_any_owner_content_field_is_a_typed_refusal(missing: str) -> None:
    private_key, _ = generate_ed25519_keypair()
    kwargs: dict[str, Any] = {
        "practice_id": new_urn("practice"),
        "created_at": _NOW,
        "created_by": new_urn("agent-role"),
        "name": "Fixture Practice",
        "description": "A fixture practice.",
        "governance_contacts": ["mailto:governance@fixture.invalid"],
        "supported_policy_versions": ["policy-2026-07-01"],
        "max_disclosure": "PUBLIC",
        "trust_statement": "fixture",
        "valid_from": _VALID_FROM,
        "valid_until": _VALID_UNTIL,
    }
    del kwargs[missing]

    with pytest.raises(TypeError):
        build_self_signed_practice(private_key, **kwargs)


def test_capability_registry_endpoint_defaults_to_none_and_is_carried_when_given() -> None:
    private_key, _ = generate_ed25519_keypair()

    without_endpoint = _build(private_key)
    assert without_endpoint.capability_registry_endpoint is None

    with_endpoint = _build(
        private_key, capability_registry_endpoint="https://fixture.invalid/capability-registry"
    )
    assert (
        with_endpoint.capability_registry_endpoint == "https://fixture.invalid/capability-registry"
    )


# ---------------------------------------------------------------------------
# No private key material anywhere in the produced document.
# ---------------------------------------------------------------------------


def test_no_private_key_material_appears_in_the_produced_document() -> None:
    private_key, _ = generate_ed25519_keypair()
    raw_private_bytes = private_key.private_bytes_raw()

    practice = _build(private_key)

    serialized = practice.model_dump_json(exclude_none=True)
    assert raw_private_bytes.hex() not in serialized
    assert base64.b64encode(raw_private_bytes).decode("ascii") not in serialized
    assert "PRIVATE KEY" not in serialized


# ---------------------------------------------------------------------------
# Contract-level validation propagates unwrapped, never softened.
# ---------------------------------------------------------------------------


def test_valid_until_not_strictly_after_valid_from_raises_validation_error() -> None:
    private_key, _ = generate_ed25519_keypair()

    with pytest.raises(ValidationError):
        _build(private_key, valid_from=_VALID_UNTIL, valid_until=_VALID_FROM)


def test_empty_name_raises_validation_error() -> None:
    private_key, _ = generate_ed25519_keypair()

    with pytest.raises(ValidationError):
        _build(private_key, name="")


def test_invalid_max_disclosure_raises_validation_error() -> None:
    """``max_disclosure`` is typed ``MaxDisclosure`` (a closed ``Literal``)
    on both ``_build`` and ``build_self_signed_practice`` themselves — a
    static type checker already rejects an out-of-vocabulary value at the
    call site. This test deliberately bypasses that with ``cast`` to prove
    the SAME value is also rejected at RUNTIME, by ``Practice``'s own
    contract-level validation, for a caller that does not go through a type
    checker (e.g. a value read from JSON/argparse, exactly as
    ``mrr.services.cli.practice_main`` does for ``--max-disclosure``).
    """
    private_key, _ = generate_ed25519_keypair()

    with pytest.raises(ValidationError):
        _build(private_key, max_disclosure=cast(MaxDisclosure, "NOT-A-REAL-LEVEL"))


# ---------------------------------------------------------------------------
# The produced document is shaped exactly as the UNCHANGED practice_key_ring
# expects (the full sharp-case proof lives at the integration tier).
# ---------------------------------------------------------------------------


def test_produced_practice_round_trips_through_the_unchanged_practice_key_ring() -> None:
    private_key, public_key = generate_ed25519_keypair()

    practice = _build(private_key)
    ring = practice_key_ring(practice)

    kid = practice.keys[0].kid
    descriptor = ring.get(kid)
    assert descriptor is not None
    assert decode_public_key(descriptor.encoded_public_key).public_bytes_raw() == (
        public_key.public_bytes_raw()
    )
    assert ring.is_valid_at(_NOW, kid)
