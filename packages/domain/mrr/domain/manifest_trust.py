"""Trust-anchored resolution of a received ``NodeManifest``'s signer key —
task-packets/E5-T02.yaml.

docs/spec/01_SYSTEM_SPEC.md section 7.3 ("Capability Registry: Stores signed
node manifests ... It does not grant permission."),
docs/spec/04_SECURITY_AND_POLICY.md section 8.1 ("Trust is per practice and
capability, not universal") and section 8.4 ("A practice can revoke a node
or key. New objects are rejected after revocation. Existing objects remain
historically attributable ...") describe a trust-anchoring step
``mrr.services.capability_registry.service.CapabilityRegistry.register``
(E2-T02) deliberately left open: that method verifies a manifest's signature
against a CALLER-SUPPLIED bare public key, with no resolution of the
manifest's own claimed signer practice / key id against a trusted key set at
all — any raw Ed25519-valid signature is accepted, however the verifying key
was obtained. This module closes that gap at the OBJECT layer only — no
network, no persisted practice registry (task-packets/E5-T02.yaml
forbidden_changes): the trusted sender's identity (``trusted_practice_id``)
and its key material (``mrr.domain.key_management.KeyRing``, built by
:func:`practice_key_ring` below from a caller-supplied
``mrr.contracts.practice.Practice``) are both supplied by the caller, exactly
as E2-T02's ``register`` already takes a caller-supplied verifying key.

--- The accept rule: all five conditions must hold, fail closed ------------

:func:`resolve_trusted_manifest_key` returns a trusted ``Ed25519PublicKey``
ONLY when ALL of the following hold, checked in this order, each with its
own DISTINCT typed error — never collapsed into one generic failure
(AGENTS.md's prohibited-shortcuts list: "collapsing ``unknown``,
``not_found``, ``contradicted``, and ``failed`` into one generic error"):

(a) ``manifest.signature.signer_practice_id == trusted_practice_id`` — else
    :class:`mrr.domain.exceptions.ManifestSignerMismatchError`.
(b) ``manifest.signature.key_id`` resolves to a descriptor in ``ring`` —
    else :class:`mrr.domain.exceptions.UnknownKeyIdError`.
(c) that descriptor is ``ring.is_valid_at(evaluation_instant, kid)`` — active
    AND inside its validity window, evaluated at the RECEIPT instant
    (default ``datetime.now(UTC)``, caller-overridable via ``at``, mirroring
    ``CapabilityRegistry.get_current_manifest``'s own ``at`` parameter) —
    else :class:`mrr.domain.exceptions.ManifestKeyNotValidError`. This is
    what makes a revoked, rotated, or expired key fail closed even though it
    still resolves in step (b) — trust anchoring beyond raw crypto
    (docs/spec/04 section 8.4: "New objects are rejected after revocation").
(d) the descriptor's own ``encoded_public_key`` is one of the manifest's own
    declared ``public_keys`` — else
    :class:`mrr.domain.exceptions.ManifestKeyNotDeclaredError`. The node
    must actually list the key it signed with; a practice cannot be tricked
    into anchoring a manifest to a key the manifest itself never claims.
(e) ``mrr.domain.hashing_policy.verify_object_signature`` (E1-T02, UNCHANGED)
    passes, over the exact ADR-0004 ``exclude_none`` canonical form, under
    the resolved key — else the same
    ``mrr.crypto.exceptions.SignatureVerificationError`` /
    ``UnsupportedAlgorithmError`` that function already raises; no new type
    is needed for "bad signature" because that function already fails
    closed with its own typed errors.

Every precondition is checked BEFORE the next, and the function returns only
after all five hold — there is no path that returns a key for a failing
precondition (proved directly by
``tests/property/test_manifest_trust_properties.py``).

--- Practice -> KeyRing ------------------------------------------------------

:func:`practice_key_ring` is a small, pure conversion: it re-derives one
``mrr.domain.key_management.PublicKeyDescriptor`` (a frozen dataclass) from
each ``mrr.contracts.practice.PublicKeyDescriptor`` (a Pydantic model)
already carried by ``practice.keys`` — the two share the exact same four
semantic fields plus lifecycle state (see
``mrr.contracts.practice.PublicKeyDescriptor``'s own docstring: "the same
four semantic fields ``mrr.domain.key_management.PublicKeyDescriptor``
defines, reusing its ``KeyState`` vocabulary directly"), so no data is
invented here. The domain descriptor's own ``__post_init__`` re-verifies
kid/key correspondence and validity-window ordering as a second, independent
check — this codebase's established "enforced twice" precedent (see that
same contracts docstring). ``mrr.domain`` importing ``mrr.contracts`` is
already precedented (``mrr.domain.independence`` imports
``mrr.contracts.verification_result.IndependenceProfile``) and is not
forbidden by either import-linter contract in pyproject.toml — only
framework/provider imports and importing ``mrr.services`` are forbidden for
core packages, and this module opens no network and imports no provider SDK.

--- What this module deliberately does NOT do -------------------------------

No persistence, no I/O, no network — a pure function pair over
already-in-memory values, CI-testable with no database
(task-packets/E5-T02.yaml: "Fully CI-testable without PostgreSQL at the
domain layer"). It does not decide whether a ``Practice`` itself is trusted
(that remains caller-supplied, exactly as E2-T02's bare verifying key was);
it does not build, load, or persist a practice registry
(task-packets/E5-T02.yaml forbidden_changes); and it enforces no replay,
expiry, or cross-runtime revocation SWEEP over already-accepted objects
(E5-T07's scope) — only whether the key resolves and is valid AT the
evaluation instant this call is given.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from mrr.contracts.node_manifest import NodeManifest
from mrr.contracts.practice import Practice
from mrr.crypto.keys import decode_public_key
from mrr.domain.exceptions import (
    ManifestKeyNotDeclaredError,
    ManifestKeyNotValidError,
    ManifestSignerMismatchError,
    UnknownKeyIdError,
)
from mrr.domain.hashing_policy import verify_object_signature
from mrr.domain.key_management import KeyRing
from mrr.domain.key_management import PublicKeyDescriptor as DomainPublicKeyDescriptor

__all__ = [
    "practice_key_ring",
    "resolve_trusted_manifest_key",
]


def practice_key_ring(practice: Practice) -> KeyRing:
    """Build a :class:`mrr.domain.key_management.KeyRing` from
    ``practice.keys`` — the small "Practice -> KeyRing" helper this task
    packet asks for.

    Every entry in ``practice.keys`` (``mrr.contracts.practice.
    PublicKeyDescriptor``, already validated at Pydantic construction time —
    its own ``kid`` is checked to match ``encoded_public_key`` and its
    validity window checked to be ordered) is converted into the equivalent
    ``mrr.domain.key_management.PublicKeyDescriptor``, which re-derives and
    re-checks the same two invariants independently in its own
    ``__post_init__``. The resulting ring's kids and descriptors therefore
    always match the practice's own keys exactly — this function invents no
    data and drops none.
    """
    descriptors = {
        key.kid: DomainPublicKeyDescriptor(
            kid=key.kid,
            algorithm=key.algorithm,
            encoded_public_key=key.encoded_public_key,
            valid_from=key.valid_from,
            valid_until=key.valid_until,
            state=key.state,
        )
        for key in practice.keys
    }
    return KeyRing(descriptors=descriptors)


def resolve_trusted_manifest_key(
    manifest: NodeManifest,
    trusted_practice_id: str,
    ring: KeyRing,
    *,
    at: datetime | None = None,
) -> Ed25519PublicKey:
    """Resolve ``manifest``'s signature to a trusted Ed25519 verifying key,
    anchored to ``trusted_practice_id``'s own ``ring``. See the module
    docstring for the full five-condition accept rule and its typed failure
    family.

    Args:
        manifest: the received, not-yet-trusted ``NodeManifest``.
        trusted_practice_id: the id of the practice the caller actually
            trusts as this manifest's sender (mirrors E2-T02's
            caller-supplied verifying key — the trust decision about WHICH
            practice to trust is the caller's, not this function's).
        ring: ``trusted_practice_id``'s own trusted ``KeyRing`` (build one
            from a ``Practice`` with :func:`practice_key_ring`).
        at: the evaluation instant for validity-window/lifecycle-state
            checks. Defaults to ``datetime.now(UTC)`` — the RECEIPT instant,
            per docs/spec/04 section 8.4 — and is caller-overridable for
            deterministic testing, mirroring
            ``CapabilityRegistry.get_current_manifest``'s own ``at``
            parameter.

    Returns:
        The trusted ``Ed25519PublicKey`` — returned ONLY once every one of
        the five accept-rule conditions has held; never returned for any
        failing precondition.

    Raises:
        mrr.domain.exceptions.ManifestSignerMismatchError: condition (a)
            fails — the manifest's claimed signer is not
            ``trusted_practice_id``.
        mrr.domain.exceptions.UnknownKeyIdError: condition (b) fails — the
            manifest's claimed key id does not resolve in ``ring`` at all.
        mrr.domain.exceptions.ManifestKeyNotValidError: condition (c) fails
            — the resolved descriptor is not valid at the evaluation
            instant (revoked, rotated, expired, or not yet valid).
        mrr.domain.exceptions.ManifestKeyNotDeclaredError: condition (d)
            fails — the resolved descriptor's public key is not among the
            manifest's own declared ``public_keys``.
        mrr.crypto.exceptions.SignatureVerificationError: condition (e)
            fails — the Ed25519 signature does not verify (bad or tampered
            signature) under the resolved key.
        mrr.crypto.exceptions.UnsupportedAlgorithmError: condition (e) fails
            — ``manifest.signature.algorithm`` is not ``"Ed25519"``.
    """
    if manifest.signature.signer_practice_id != trusted_practice_id:
        raise ManifestSignerMismatchError(
            claimed_signer_practice_id=manifest.signature.signer_practice_id,
            trusted_practice_id=trusted_practice_id,
        )

    kid = manifest.signature.key_id
    descriptor = ring.get(kid)
    if descriptor is None:
        raise UnknownKeyIdError(kid)

    evaluation_instant = at if at is not None else datetime.now(UTC)
    if not ring.is_valid_at(evaluation_instant, kid):
        raise ManifestKeyNotValidError(kid, at=evaluation_instant)

    if descriptor.encoded_public_key not in manifest.public_keys:
        raise ManifestKeyNotDeclaredError(kid)

    verifying_key = decode_public_key(descriptor.encoded_public_key)
    verify_object_signature(
        verifying_key,
        json.loads(manifest.model_dump_json(exclude_none=True)),
        manifest.signature.value,
        algorithm=manifest.signature.algorithm,
    )
    return verifying_key
