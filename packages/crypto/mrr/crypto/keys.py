"""Ed25519 keypair generation, public-key encode/decode, and deterministic
key-identifier (kid) derivation, per task-packets/E5-T01.yaml and
docs/spec/04_SECURITY_AND_POLICY.md section 4.1 ("Keys stored outside
application databases ... Key use and rotation are audited").

This module is ADDITIVE to ``mrr.crypto``: it does not change
``mrr.crypto.signatures`` or ``mrr.crypto.hashing`` behavior at all —
``signatures.py``'s own docstring already reserved "key generation, storage,
rotation, and trust/revocation ... explicitly out of scope for this module
(E5)"; this is that task.

--- Private key handling (AGENTS.md rule 11, docs/spec/04 section 4.1) ------

``generate_ed25519_keypair`` returns the PRIVATE key to the caller only —
the caller is responsible for storing it outside any MRR database. Nothing
in this module, or in ``mrr.domain.key_management`` built on top of it,
ever accepts, stores, logs, or serializes a private key: every other
function here (``encode_public_key``, ``decode_public_key``,
``derive_key_id``) takes or returns only an ``Ed25519PublicKey`` or a public
encoded string.

--- Public-key encoding reuses the ADR-0003 base64 convention -------------

``encode_public_key``/``decode_public_key`` use the exact same standard
base64 (RFC 4648 section 4, with padding) convention
``mrr.crypto.signatures`` already uses for ``signature.value``
(docs/spec/adr/ADR-0003-SIGNATURE-VALUE-ENCODING.md) — one canonical
byte-string encoding for every base64 blob this codebase produces, not a
second, independently-chosen one for keys.

--- kid derivation is a pure function of the key's own raw bytes -----------

``derive_key_id`` returns ``"kid:" + standard-base64(SHA-256(raw 32 public
key bytes))`` — deterministic (same key -> same kid on every call),
collision-resistant (SHA-256), and never a function of any mutable label —
the same identity discipline ``mrr.domain.identity.new_urn``'s own
docstring documents for URNs, except a kid is not randomly minted: the same
public key must always produce the same kid, so relabelling a
``mrr.contracts.practice.Practice`` (renaming it, changing its contacts,
...) can never change any of its keys' kids.
"""

from __future__ import annotations

import base64
import binascii
import hashlib

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from mrr.crypto.exceptions import InvalidPublicKeyError

#: Prefix on every kid this module derives, so a kid string is
#: self-describing (never confusable with, say, a bare base64 signature
#: value or an unrelated identifier).
_KID_PREFIX = "kid:"


def generate_ed25519_keypair() -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    """Generate a fresh Ed25519 keypair.

    The PRIVATE key is returned to the CALLER, who is responsible for
    storing it outside any MRR database (docs/spec/04_SECURITY_AND_POLICY.md
    section 4.1) — this function does not persist, log, or otherwise retain
    it, and no downstream ``mrr.domain.key_management`` type can hold one
    (every field there is a string or a public key object).
    """
    private_key = Ed25519PrivateKey.generate()
    return private_key, private_key.public_key()


def encode_public_key(public_key: Ed25519PublicKey) -> str:
    """Encode ``public_key``'s raw 32 bytes as standard base64 (RFC 4648
    section 4, with padding) — the same encoding convention
    ADR-0003-SIGNATURE-VALUE-ENCODING.md pins for ``signature.value``.
    """
    return base64.b64encode(public_key.public_bytes_raw()).decode("ascii")


def decode_public_key(encoded: str) -> Ed25519PublicKey:
    """Decode a standard-base64-encoded raw Ed25519 public key.

    Raises:
        InvalidPublicKeyError: if ``encoded`` is not valid base64, or does
            not decode to a well-formed 32-byte Ed25519 public key. Fails
            closed rather than returning ``None`` or a partially-built key.
    """
    try:
        raw_bytes = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidPublicKeyError(f"public key value is not valid base64: {exc}") from exc

    try:
        return Ed25519PublicKey.from_public_bytes(raw_bytes)
    except ValueError as exc:
        raise InvalidPublicKeyError(f"not a valid Ed25519 public key: {exc}") from exc


def derive_key_id(public_key: Ed25519PublicKey) -> str:
    """Deterministically derive a key identifier (kid) from ``public_key``'s
    raw bytes only.

    Format: ``"kid:" + standard-base64(SHA-256(raw 32-byte public key))`` —
    stable across calls for the same key, different for different keys
    (SHA-256 collision resistance), and never a function of any label, name,
    or other mutable data (only the key's own raw bytes feed the hash).
    """
    digest = hashlib.sha256(public_key.public_bytes_raw()).digest()
    return f"{_KID_PREFIX}{base64.b64encode(digest).decode('ascii')}"


__all__ = [
    "decode_public_key",
    "derive_key_id",
    "encode_public_key",
    "generate_ed25519_keypair",
]
