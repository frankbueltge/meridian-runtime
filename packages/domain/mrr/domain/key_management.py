"""Per-practice public-key descriptors, lifecycle state, and rotation —
task-packets/E5-T01.yaml.

docs/spec/02_DOMAIN_MODEL.md section 1.3 defines what a signature carries
(signer practice identifier, key identifier, algorithm, signature, signed-at
timestamp, optional trust-chain reference) without saying how a verifier
resolves that key identifier to actual key material, or judges whether it
was still valid when a given signature was produced.
docs/spec/04_SECURITY_AND_POLICY.md section 4.1 ("Keys stored outside
application databases ... Key use and rotation are audited") and section 8.4
("A practice can revoke a node or key. New objects are rejected after
revocation. Existing objects remain historically attributable ...") describe
the lifecycle this module represents.

This module is the DATA side only: it answers "is this key, as recorded,
valid at time T" and lets a caller mint a rotation or a revocation. It does
NOT wire that answer into any runtime accept/reject path — that fail-closed
ENFORCEMENT is task-packets/E5-T07.yaml's scope, explicitly excluded by this
task's own forbidden_changes — and it holds no persistence of its own (a
future service, E5-T02+, is responsible for loading/storing a ``KeyRing``).

Only PUBLIC key material is ever represented here — ``PublicKeyDescriptor
.encoded_public_key`` is the ADR-0003 standard-base64 encoding of a raw
Ed25519 PUBLIC key (``mrr.crypto.keys.encode_public_key``). No field
anywhere in this module can hold a private key:
``mrr.crypto.keys.generate_ed25519_keypair`` hands the private half only to
its own caller, who is responsible for storing it outside any MRR database
(docs/spec/04 section 4.1) — this module never sees it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from types import MappingProxyType
from typing import Literal

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from mrr.crypto.exceptions import InvalidPublicKeyError
from mrr.crypto.keys import decode_public_key, derive_key_id, encode_public_key
from mrr.crypto.signatures import SUPPORTED_ALGORITHMS
from mrr.domain.exceptions import UnknownKeyIdError

#: docs/spec/04_SECURITY_AND_POLICY.md section 8.4's lifecycle for one key:
#: "active" (currently trusted), "rotated" (superseded by a successor key
#: but kept historically addressable, never deleted), "revoked" (the owning
#: practice revoked it; objects it already signed remain historically
#: attributable). A plain ``Literal`` string vocabulary, matching this
#: codebase's established convention for a closed vocabulary (e.g.
#: ``mrr.domain.model_adapter.OperationKind``) — no module under packages/
#: uses ``enum.Enum``.
KeyState = Literal["active", "rotated", "revoked"]


@dataclass(frozen=True, slots=True, kw_only=True)
class PublicKeyDescriptor:
    """One PUBLIC key's identity, validity window, and lifecycle state.

    ``kid`` MUST be exactly ``mrr.crypto.keys.derive_key_id``'s own output
    for ``encoded_public_key`` — verified in ``__post_init__`` rather than
    trusted from the caller, so a descriptor can never be constructed with a
    kid that does not actually correspond to its own key material (the same
    "never derived from, or accidentally tied to, untrusted/mutable input"
    discipline ``mrr.domain.identity.new_urn``'s own docstring documents for
    URNs). Prefer :func:`new_descriptor` over calling this constructor
    directly — it derives both fields the one correct way instead of
    leaving a caller to assemble them by hand.
    """

    kid: str
    algorithm: str
    encoded_public_key: str
    valid_from: datetime
    valid_until: datetime
    state: KeyState

    def __post_init__(self) -> None:
        if self.algorithm not in SUPPORTED_ALGORITHMS:
            raise ValueError(f"unsupported key algorithm: {self.algorithm!r}")

        try:
            public_key = decode_public_key(self.encoded_public_key)
        except InvalidPublicKeyError as exc:
            raise ValueError(
                f"encoded_public_key is not a valid Ed25519 public key: {exc}"
            ) from exc

        expected_kid = derive_key_id(public_key)
        if self.kid != expected_kid:
            raise ValueError(
                f"kid {self.kid!r} does not match the deterministic kid derived from "
                f"encoded_public_key ({expected_kid!r}) — a kid must never be assigned "
                "independently of its own key material"
            )

        if self.valid_from.tzinfo is None or self.valid_until.tzinfo is None:
            raise ValueError("valid_from and valid_until must be aware datetimes")
        if not self.valid_from < self.valid_until:
            raise ValueError(
                f"valid_from ({self.valid_from.isoformat()}) must be strictly before "
                f"valid_until ({self.valid_until.isoformat()})"
            )


def new_descriptor(
    public_key: Ed25519PublicKey,
    *,
    valid_from: datetime,
    valid_until: datetime,
    state: KeyState = "active",
    algorithm: str = "Ed25519",
) -> PublicKeyDescriptor:
    """Build a :class:`PublicKeyDescriptor` for ``public_key``, deriving
    ``kid`` and ``encoded_public_key`` the one correct way
    (``mrr.crypto.keys.derive_key_id``/``encode_public_key``) instead of
    leaving callers to assemble those two fields by hand and risk a
    kid/key mismatch.
    """
    return PublicKeyDescriptor(
        kid=derive_key_id(public_key),
        algorithm=algorithm,
        encoded_public_key=encode_public_key(public_key),
        valid_from=valid_from,
        valid_until=valid_until,
        state=state,
    )


@dataclass(frozen=True, slots=True)
class KeyRing:
    """A practice's kid -> :class:`PublicKeyDescriptor` lookup table.

    Immutable value object: :func:`rotate`/:func:`revoke` below return a NEW
    ``KeyRing`` rather than mutating this one in place — mirrors
    ``mrr.domain.lifecycles.StateMachine``'s own "holds no mutable state of
    its own" precedent. Loading/storing a ``KeyRing`` for a practice is a
    future persistence layer's job (E5-T02+, out of this task's scope); this
    module only defines the shape and the pure lookup/transition operations
    on it.
    """

    descriptors: Mapping[str, PublicKeyDescriptor]

    def __post_init__(self) -> None:
        for kid, descriptor in self.descriptors.items():
            if descriptor.kid != kid:
                raise ValueError(
                    f"KeyRing entry keyed {kid!r} carries a descriptor whose own kid is "
                    f"{descriptor.kid!r} — the map key must equal descriptor.kid"
                )
        # Defensive copy: freezes this ring against later mutation of
        # whatever mutable mapping the caller passed in, and against
        # mutation of the mapping this ring itself exposes.
        object.__setattr__(self, "descriptors", MappingProxyType(dict(self.descriptors)))

    def get(self, kid: str) -> PublicKeyDescriptor | None:
        """Resolve ``kid`` to its descriptor, or ``None`` if this ring has
        never held that kid. Never raises — callers that need fail-closed
        behavior for an unknown kid use :meth:`is_valid_at`, which already
        treats "not found" as invalid.
        """
        return self.descriptors.get(kid)

    def is_valid_at(self, now: datetime, kid: str) -> bool:
        """``True`` ONLY when ``kid`` resolves to a descriptor, that
        descriptor's state is ``"active"``, AND
        ``valid_from <= now < valid_until``.

        Fails closed for every other case — unknown kid, revoked, rotated,
        before ``valid_from``, or at/after ``valid_until`` — by returning
        ``False`` rather than raising, so a caller can use this directly as
        an accept gate without a try/except.
        """
        descriptor = self.descriptors.get(kid)
        if descriptor is None:
            return False
        if descriptor.state != "active":
            return False
        return descriptor.valid_from <= now < descriptor.valid_until

    def with_descriptor(self, descriptor: PublicKeyDescriptor) -> KeyRing:
        """Return a new ``KeyRing`` with ``descriptor`` added, or replacing
        whatever this ring currently holds for its kid. Pure — this ring is
        left unchanged.
        """
        updated = dict(self.descriptors)
        updated[descriptor.kid] = descriptor
        return KeyRing(descriptors=updated)


def rotate(
    ring: KeyRing,
    *,
    prior_kid: str,
    successor_public_key: Ed25519PublicKey,
    valid_from: datetime,
    valid_until: datetime,
    algorithm: str = "Ed25519",
) -> KeyRing:
    """Mint a new ``active`` descriptor for ``successor_public_key``,
    supersede ``prior_kid``, and return a NEW ring holding both.

    docs/spec/04_SECURITY_AND_POLICY.md section 8.4: the prior key moves to
    ``"rotated"`` — it is NEVER deleted or dropped from the ring, so any
    object it already signed stays historically resolvable through the ring.

    Raises:
        mrr.domain.exceptions.UnknownKeyIdError: if ``prior_kid`` does not
            resolve to a descriptor in ``ring`` — there is nothing to
            supersede.
    """
    prior = ring.get(prior_kid)
    if prior is None:
        raise UnknownKeyIdError(prior_kid)

    successor = new_descriptor(
        successor_public_key,
        valid_from=valid_from,
        valid_until=valid_until,
        algorithm=algorithm,
    )
    rotated_prior = replace(prior, state="rotated")
    return ring.with_descriptor(rotated_prior).with_descriptor(successor)


def revoke(ring: KeyRing, *, kid: str) -> KeyRing:
    """Mark ``kid``'s descriptor ``"revoked"`` and return a NEW ring; the
    prior descriptor is never deleted (docs/spec/04_SECURITY_AND_POLICY.md
    section 8.4: "Existing objects remain historically attributable").

    Raises:
        mrr.domain.exceptions.UnknownKeyIdError: if ``kid`` does not resolve
            to a descriptor in ``ring``.
    """
    descriptor = ring.get(kid)
    if descriptor is None:
        raise UnknownKeyIdError(kid)
    return ring.with_descriptor(replace(descriptor, state="revoked"))


__all__ = [
    "KeyRing",
    "KeyState",
    "PublicKeyDescriptor",
    "new_descriptor",
    "revoke",
    "rotate",
]
