"""Unit tests for mrr.domain.trust_resolution (task-packets/E9-T00b.yaml),
the shared core the five trust resolvers (manifest_trust/task_trust/
crate_trust/transfer_trust/correction_notification) now delegate to.

Deliberately DB-free and independent of any of the five real contracts: a
minimal, non-Pydantic ``_FakeSignedObject`` stands in for the ``SignedObject``
Protocol (only ``.signature``/``.model_dump_json`` are ever touched), and two
throwaway ``DomainError`` subclasses stand in for the two injected
error-constructor Protocols — proving the shared core's own condition order
and fail-closed behavior in isolation, something none of the five per-module
test suites exercises on its own (each of those only ever calls its own
resolver, with its own bound error classes, never the shared core directly).

Covers this packet's own acceptance_tests: condition order (signer mismatch
checked before the ring is even consulted for a kid), the shared
``UnknownKeyIdError`` for an unresolvable kid, the injected
``key_not_valid_error`` for revoked/rotated/expired/not-yet-valid, that
``resolve_trusted_signer_key`` returns a key WITHOUT verifying any signature
(a separate step, ``verify_trusted_signature``), and that verification is
always against the RESOLVED ring key — never anything the object itself
claims — so a key-substitution attack fails closed.
"""

from __future__ import annotations

import base64
import dataclasses
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from mrr.contracts.common import Signature
from mrr.crypto.exceptions import SignatureVerificationError
from mrr.crypto.keys import generate_ed25519_keypair
from mrr.domain.exceptions import DomainError, UnknownKeyIdError
from mrr.domain.hashing_policy import sign_object
from mrr.domain.identity import new_urn
from mrr.domain.key_management import KeyRing, KeyState, PublicKeyDescriptor, new_descriptor
from mrr.domain.trust_resolution import resolve_trusted_signer_key, verify_trusted_signature

_NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)
_VALID_FROM = _NOW - timedelta(days=1)
_VALID_UNTIL = _NOW + timedelta(days=365)


# ---------------------------------------------------------------------------
# The minimal SignedObject stand-in, and the two injected-error fakes.
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _FakeSignedObject:
    """Satisfies ``mrr.domain.trust_resolution.SignedObject`` structurally —
    ``.signature`` (a real ``mrr.contracts.common.Signature``, the same
    shared type every one of the five real contracts carries) and
    ``.model_dump_json`` — and NOTHING else. Deliberately not one of the
    five real Pydantic contracts, to prove the shared core imports none of
    them.
    """

    signature: Signature
    payload: dict[str, Any] = dataclasses.field(default_factory=dict)

    def model_dump_json(self, *, exclude_none: bool = False) -> str:
        body: dict[str, Any] = dict(self.payload)
        body["signature"] = json.loads(self.signature.model_dump_json(exclude_none=exclude_none))
        return json.dumps(body)


class _FakeSignerMismatchError(DomainError):
    """A throwaway stand-in for the ``SignerMismatchErrorFactory`` Protocol
    — proves the shared core raises whatever CLASS the caller binds, never
    one of its own choosing.
    """

    def __init__(self, *, claimed_signer_practice_id: str, trusted_practice_id: str) -> None:
        self.claimed_signer_practice_id = claimed_signer_practice_id
        self.trusted_practice_id = trusted_practice_id
        super().__init__("fake signer mismatch")


class _FakeKeyNotValidError(DomainError):
    """A throwaway stand-in for the ``KeyNotValidErrorFactory`` Protocol."""

    def __init__(self, kid: str, *, at: datetime) -> None:
        self.kid = kid
        self.at = at
        super().__init__("fake key not valid")


# ---------------------------------------------------------------------------
# Fixture builders.
# ---------------------------------------------------------------------------


def _ring_with(descriptor: PublicKeyDescriptor) -> KeyRing:
    return KeyRing(descriptors={descriptor.kid: descriptor})


def _sign_fake(obj: _FakeSignedObject, private_key: Ed25519PrivateKey) -> _FakeSignedObject:
    """Sign over the ADR-0004 ``exclude_none=True`` form — the same
    canonical body ``verify_trusted_signature`` verifies against.
    """
    value = sign_object(private_key, json.loads(obj.model_dump_json(exclude_none=True)))
    return dataclasses.replace(obj, signature=obj.signature.model_copy(update={"value": value}))


def _trusted_scenario(
    *,
    key_state: KeyState = "active",
    valid_from: datetime = _VALID_FROM,
    valid_until: datetime = _VALID_UNTIL,
) -> tuple[_FakeSignedObject, str, KeyRing, Ed25519PrivateKey]:
    """A fully self-consistent scenario: a trusted practice with one key in
    ``key_state``, and a fake signed object genuinely signed by that key,
    naming the practice as signer.
    """
    private_key, public_key = generate_ed25519_keypair()
    trusted_practice_id = new_urn("practice")
    descriptor = new_descriptor(
        public_key, valid_from=valid_from, valid_until=valid_until, state=key_state
    )
    signature = Signature(
        signer_practice_id=trusted_practice_id,
        key_id=descriptor.kid,
        algorithm="Ed25519",
        signed_at=_NOW,
        value="0" * 44,
    )
    obj = _FakeSignedObject(signature=signature, payload={"note": "fixture payload"})
    signed = _sign_fake(obj, private_key)
    return signed, trusted_practice_id, _ring_with(descriptor), private_key


def _resolve(
    obj: _FakeSignedObject, trusted_practice_id: str, ring: KeyRing, *, at: datetime | None
) -> Ed25519PublicKey:
    return resolve_trusted_signer_key(
        obj,
        trusted_practice_id,
        ring,
        at=at,
        signer_mismatch_error=_FakeSignerMismatchError,
        key_not_valid_error=_FakeKeyNotValidError,
    )


# ---------------------------------------------------------------------------
# Happy path: resolve, then verify, as a real caller does.
# ---------------------------------------------------------------------------


def test_happy_path_resolves_the_trusted_verifying_key() -> None:
    obj, trusted_practice_id, ring, private_key = _trusted_scenario()

    key = _resolve(obj, trusted_practice_id, ring, at=_NOW)

    assert key.public_bytes_raw() == private_key.public_key().public_bytes_raw()
    verify_trusted_signature(obj, key)  # does not raise


def test_at_none_defaults_to_now() -> None:
    """The shared core, not any caller, applies the ``None``-means-``now``
    fallback (derived_decisions (b)) — a key valid across a wide window
    resolves fine when ``at=None`` is passed straight through.
    """
    obj, trusted_practice_id, ring, private_key = _trusted_scenario(
        valid_from=datetime(2000, 1, 1, tzinfo=UTC), valid_until=datetime(2100, 1, 1, tzinfo=UTC)
    )

    key = _resolve(obj, trusted_practice_id, ring, at=None)

    assert key.public_bytes_raw() == private_key.public_key().public_bytes_raw()


# ---------------------------------------------------------------------------
# resolve_trusted_signer_key never inspects the signature VALUE — only
# verify_trusted_signature does. The split is real, not just documented.
# ---------------------------------------------------------------------------


def test_resolve_returns_a_key_even_for_a_garbage_signature_value() -> None:
    obj, trusted_practice_id, ring, _ = _trusted_scenario()
    # Well-formed base64, wrong content — decodes fine, just is not the real
    # signature; proves resolve_trusted_signer_key never looks at it at all.
    garbage_value = base64.b64encode(b"\x00" * 64).decode("ascii")
    garbage_signed = dataclasses.replace(
        obj, signature=obj.signature.model_copy(update={"value": garbage_value})
    )

    key = _resolve(garbage_signed, trusted_practice_id, ring, at=_NOW)

    assert key is not None
    with pytest.raises(SignatureVerificationError):
        verify_trusted_signature(garbage_signed, key)


# ---------------------------------------------------------------------------
# Condition order: (a) signer match, (b) unknown kid, (c) key validity —
# each checked strictly before the next, never speculatively out of order.
# ---------------------------------------------------------------------------


def test_signer_mismatch_is_checked_before_the_ring_is_consulted() -> None:
    """An empty ring (guaranteed unknown kid) plus a wrong trusted practice
    id still raises the SIGNER-mismatch error, not UnknownKeyIdError — proof
    condition (a) runs, and short-circuits, before (b) ever looks at the
    ring.
    """
    obj, _, _, _ = _trusted_scenario()
    empty_ring = KeyRing(descriptors={})
    wrong_practice_id = new_urn("practice")

    with pytest.raises(_FakeSignerMismatchError) as excinfo:
        _resolve(obj, wrong_practice_id, empty_ring, at=_NOW)
    assert excinfo.value.claimed_signer_practice_id == obj.signature.signer_practice_id
    assert excinfo.value.trusted_practice_id == wrong_practice_id


def test_unknown_kid_raises_the_shared_unknown_key_id_error() -> None:
    obj, trusted_practice_id, _, _ = _trusted_scenario()
    empty_ring = KeyRing(descriptors={})

    with pytest.raises(UnknownKeyIdError) as excinfo:
        _resolve(obj, trusted_practice_id, empty_ring, at=_NOW)
    assert excinfo.value.kid == obj.signature.key_id


@pytest.mark.parametrize("key_state", ["revoked", "rotated"])
def test_inactive_key_state_raises_the_injected_key_not_valid_error(key_state: KeyState) -> None:
    obj, trusted_practice_id, ring, _ = _trusted_scenario(key_state=key_state)

    with pytest.raises(_FakeKeyNotValidError) as excinfo:
        _resolve(obj, trusted_practice_id, ring, at=_NOW)
    assert excinfo.value.kid == obj.signature.key_id
    assert excinfo.value.at == _NOW


def test_expired_key_raises_the_injected_key_not_valid_error() -> None:
    obj, trusted_practice_id, ring, _ = _trusted_scenario(
        valid_from=_NOW - timedelta(days=10), valid_until=_NOW - timedelta(days=1)
    )

    with pytest.raises(_FakeKeyNotValidError):
        _resolve(obj, trusted_practice_id, ring, at=_NOW)


def test_not_yet_valid_key_raises_the_injected_key_not_valid_error() -> None:
    obj, trusted_practice_id, ring, _ = _trusted_scenario(
        valid_from=_NOW + timedelta(days=1), valid_until=_NOW + timedelta(days=10)
    )

    with pytest.raises(_FakeKeyNotValidError):
        _resolve(obj, trusted_practice_id, ring, at=_NOW)


# ---------------------------------------------------------------------------
# Key-substitution attack: verification is always against the RESOLVED
# ring key, never anything the object itself claims.
# ---------------------------------------------------------------------------


def test_key_substitution_attack_fails_closed_at_verification() -> None:
    """An attacker without the trusted private key cannot forge acceptance
    by signing with their OWN key while claiming the victim's trusted kid:
    resolve_trusted_signer_key still returns the RING's own key for that
    kid (never anything derived from the object), so verification fails.
    """
    obj, trusted_practice_id, ring, _ = _trusted_scenario()
    attacker_private_key, _ = generate_ed25519_keypair()
    forged = _sign_fake(obj, attacker_private_key)

    key = _resolve(forged, trusted_practice_id, ring, at=_NOW)

    with pytest.raises(SignatureVerificationError):
        verify_trusted_signature(forged, key)


def test_tampered_payload_fails_closed_at_verification() -> None:
    obj, trusted_practice_id, ring, _ = _trusted_scenario()
    key = _resolve(obj, trusted_practice_id, ring, at=_NOW)
    tampered = dataclasses.replace(obj, payload={"note": "not what was signed"})

    with pytest.raises(SignatureVerificationError):
        verify_trusted_signature(tampered, key)
