"""Ed25519 signing and verification over raw bytes, per
docs/spec/02_DOMAIN_MODEL.md section 1.3 and the ``signature`` object defined
in schemas/common.schema.json (``algorithm`` is a JSON Schema ``const``:
exactly ``"Ed25519"``).

Key generation, storage, rotation, and trust/revocation are explicitly out of
scope for this module (E5); callers supply already-loaded
``cryptography`` Ed25519 key objects.

Encoding note (unresolved specification question — see PR description): the
specification does not normatively state how ``signature.value`` bytes are
encoded as a JSON string. This module uses standard base64 (RFC 4648 section
4, with padding) of the 64 raw Ed25519 signature bytes, which is the encoding
inferred from the 88-character placeholder ``value`` fields in
examples/*.json (64 raw bytes -> 88 base64 characters with padding).
"""

from __future__ import annotations

import base64
import binascii

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from mrr.crypto.exceptions import SignatureVerificationError, UnsupportedAlgorithmError

#: Algorithm strings this module will sign or verify with. Membership is
#: checked with exact, case-sensitive string equality against the schema's
#: ``const "Ed25519"`` — "ed25519", "ED25519", etc. are rejected, not
#: normalized.
SUPPORTED_ALGORITHMS: frozenset[str] = frozenset({"Ed25519"})


def sign(private_key: Ed25519PrivateKey, payload: bytes, *, algorithm: str = "Ed25519") -> str:
    """Sign ``payload`` with ``private_key`` and return the signature as
    standard base64 (RFC 4648 section 4, with padding) text.

    Raises:
        UnsupportedAlgorithmError: if ``algorithm`` is not exactly
            ``"Ed25519"``. Checked before any cryptographic call.
    """
    _require_supported_algorithm(algorithm)
    raw_signature = private_key.sign(payload)
    return base64.b64encode(raw_signature).decode("ascii")


def verify(
    public_key: Ed25519PublicKey,
    payload: bytes,
    signature_value: str,
    *,
    algorithm: str,
) -> None:
    """Verify ``signature_value`` (standard base64, with padding) against
    ``payload`` using ``public_key``.

    Raises:
        UnsupportedAlgorithmError: if ``algorithm`` is not exactly
            ``"Ed25519"`` — checked before ``signature_value`` is decoded or
            any cryptographic call is made, so an unsupported algorithm
            fails closed rather than silently attempting verification.
        SignatureVerificationError: if ``signature_value`` is not valid
            base64, or the signature does not verify against ``payload``
            under ``public_key``. There is no boolean-returning form: a
            failed verification always raises.
    """
    _require_supported_algorithm(algorithm)

    try:
        raw_signature = base64.b64decode(signature_value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SignatureVerificationError(f"signature value is not valid base64: {exc}") from exc

    try:
        public_key.verify(raw_signature, payload)
    except InvalidSignature as exc:
        raise SignatureVerificationError(
            "signature does not verify against the given payload and key"
        ) from exc


def _require_supported_algorithm(algorithm: str) -> None:
    if algorithm not in SUPPORTED_ALGORITHMS:
        raise UnsupportedAlgorithmError(f"unsupported signature algorithm: {algorithm!r}")
