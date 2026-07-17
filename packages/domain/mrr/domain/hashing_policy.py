"""Two-stage field-selection policy for canonical hashing and signing, per
docs/spec/02_DOMAIN_MODEL.md sections 1.2-1.3.

Every first-class MRR object carries its own ``content_hash`` field
(schemas/common.schema.json ``$defs.baseObject``), and cross-practice objects
carry a ``signature`` (singular) or, where more than one practice signs the
same object, a ``signatures`` field. Two different byte strings are derived
from the same object:

- the **hashed payload**: the object with ``content_hash`` and
  ``signature``/``signatures`` excluded (section 1.2 — "Content hashes are
  computed over canonical JSON with signatures ... excluded");
- the **signed payload**: the object with only ``signature``/``signatures``
  excluded, so ``content_hash`` remains present (section 1.3 — "The
  signature covers the canonical payload and content hash").

Both exclusions are shallow (top-level fields of the object being hashed or
signed only). A nested reference such as an ``artifactRef.content_hash``
inside a list of artifacts is semantic data about a *different* object and
must stay in the payload; only the object's own top-level identity/signature
metadata is stripped.

This module is the only place that knows the field-selection policy; it
composes ``mrr.crypto.canonical`` and ``mrr.crypto.signatures`` /
``mrr.crypto.hashing`` for the actual byte-level operations, and stays
framework-independent like the rest of ``mrr.domain`` (MRR-NFR-010,
enforced by the import-linter contract in pyproject.toml).
"""

from __future__ import annotations

from collections.abc import Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from mrr.crypto.canonical import JSONValue, canonicalize
from mrr.crypto.hashing import content_hash as _content_hash
from mrr.crypto.signatures import sign as _sign
from mrr.crypto.signatures import verify as _verify

#: Fields excluded when building the payload that gets *hashed*.
_HASH_EXCLUDED_FIELDS = frozenset({"content_hash", "signature", "signatures"})

#: Fields excluded when building the payload that gets *signed*. Unlike the
#: hashed payload, `content_hash` is deliberately kept — the signature must
#: cover it (docs/spec/02_DOMAIN_MODEL.md section 1.3).
_SIGNATURE_EXCLUDED_FIELDS = frozenset({"signature", "signatures"})


def prepare_for_hash(obj: Mapping[str, JSONValue]) -> dict[str, JSONValue]:
    """Return a shallow copy of ``obj`` without its ``content_hash`` or
    ``signature``/``signatures`` fields.
    """
    return {key: value for key, value in obj.items() if key not in _HASH_EXCLUDED_FIELDS}


def prepare_for_signature(obj: Mapping[str, JSONValue]) -> dict[str, JSONValue]:
    """Return a shallow copy of ``obj`` without only its
    ``signature``/``signatures`` field(s).

    ``content_hash`` is intentionally retained: per
    docs/spec/02_DOMAIN_MODEL.md section 1.3, "the signature covers the
    canonical payload and content hash".
    """
    return {key: value for key, value in obj.items() if key not in _SIGNATURE_EXCLUDED_FIELDS}


def compute_content_hash(obj: Mapping[str, JSONValue]) -> str:
    """Compute the object's ``sha256:<hex>`` content hash: canonicalize the
    hashed payload (``prepare_for_hash``) and hash the resulting bytes.
    """
    canonical_bytes = canonicalize(prepare_for_hash(obj))
    return _content_hash(canonical_bytes)


def sign_object(
    private_key: Ed25519PrivateKey,
    obj: Mapping[str, JSONValue],
    *,
    algorithm: str = "Ed25519",
) -> str:
    """Sign the object's signed payload (``prepare_for_signature``) and
    return the resulting standard-base64 signature value.

    Key management (generation, storage, rotation, trust) is out of scope
    here; ``private_key`` is caller-supplied.
    """
    canonical_bytes = canonicalize(prepare_for_signature(obj))
    return _sign(private_key, canonical_bytes, algorithm=algorithm)


def verify_object_signature(
    public_key: Ed25519PublicKey,
    obj: Mapping[str, JSONValue],
    signature_value: str,
    *,
    algorithm: str,
) -> None:
    """Verify ``signature_value`` against the object's signed payload
    (``prepare_for_signature``).

    Raises the same ``mrr.crypto`` exceptions as
    ``mrr.crypto.signatures.verify`` (``UnsupportedAlgorithmError``,
    ``SignatureVerificationError``); there is no boolean-returning form.
    """
    canonical_bytes = canonicalize(prepare_for_signature(obj))
    _verify(public_key, canonical_bytes, signature_value, algorithm=algorithm)
