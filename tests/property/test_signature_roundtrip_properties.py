"""Property test: sign -> verify round trip holds for arbitrary JSON-safe
payloads (E1-T02 task-packet acceptance test).

The keypair is generated once at module scope rather than per example: the
property under test is about the canonicalize/sign/verify pipeline behaving
consistently across arbitrary payloads, not about key generation, and
reusing one keypair keeps the hypothesis run fast.
"""

from __future__ import annotations

from _json_strategies import json_values
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from hypothesis import given
from mrr.crypto.canonical import JSONValue, canonicalize
from mrr.crypto.signatures import sign, verify

_PRIVATE_KEY = Ed25519PrivateKey.generate()
_PUBLIC_KEY = _PRIVATE_KEY.public_key()


@given(json_values())
def test_sign_then_verify_round_trip_holds_for_arbitrary_json_payloads(
    payload: JSONValue,
) -> None:
    canonical_bytes = canonicalize(payload)

    signature_value = sign(_PRIVATE_KEY, canonical_bytes)

    verify(_PUBLIC_KEY, canonical_bytes, signature_value, algorithm="Ed25519")  # must not raise
