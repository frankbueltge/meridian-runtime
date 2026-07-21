"""Trust-anchored resolution of a ``TransferContract``'s signer key —
task-packets/E6-T01.yaml.

docs/spec/01_SYSTEM_SPEC.md MRR-FR-080 ("A transfer between practices MUST
use a versioned TransferContract referencing exact source objects by
identifier and hash") and MRR-FR-081 ("The receiving practice MUST respond
with accepted, adapted, rejected, deferred, or unresolved") describe the
cross-practice negotiation ``mrr.services.transfer.service.TransferService``
implements. Exactly like ``TaskBundle`` (task-packets/E5-T04.yaml
``mrr.domain.task_trust``) and ``EvidenceCrate`` (task-packets/E5-T05.yaml
``mrr.domain.crate_trust``), a ``TransferContract`` is a cross-practice,
origin-signed object (MRR-NFR-007: "Cross-practice objects MUST be
authenticated, authorized, signed, hashed, and replay-protected") whose
signer key must be resolved against a TRUSTED practice's own key material,
not a caller-supplied bare verifying key with no anchoring at all. This
module closes that gap at the OBJECT layer only — no network, no persisted
practice registry (task-packets/E6-T01.yaml forbidden_changes: "reuse
key_management.KeyRing and manifest_trust.practice_key_ring unchanged; do
not refactor them into one shared resolver"): it is the third near-identical
sibling of ``resolve_trusted_task_key``/``resolve_trusted_crate_key``,
applied here to ``TransferContract``.

--- The accept rule: all four conditions must hold, fail closed ------------

:func:`resolve_trusted_transfer_key` returns a trusted ``Ed25519PublicKey``
ONLY when ALL of the following hold, checked in this order, each with its
own DISTINCT typed error — never collapsed into one generic failure
(AGENTS.md's prohibited-shortcuts list: "collapsing ``unknown``,
``not_found``, ``contradicted``, and ``failed`` into one generic error"):

(a) ``contract.signature.signer_practice_id == trusted_signer_practice_id``
    — the practice the caller actually trusts as THIS contract's SENDER —
    else :class:`mrr.domain.exceptions.TransferSignerMismatchError`.
(b) ``contract.signature.key_id`` resolves to a descriptor in ``ring`` —
    else :class:`mrr.domain.exceptions.UnknownKeyIdError` (reused verbatim —
    see that error's own docstring for why, unlike the signer-mismatch
    error, it is shared rather than mirrored by a distinctly-named
    sibling).
(c) that descriptor is ``ring.is_valid_at(evaluation_instant, kid)`` — active
    AND inside its validity window, evaluated at the EVALUATION instant
    (default ``datetime.now(UTC)``, caller-overridable via ``at``, mirroring
    ``resolve_trusted_task_key``'s/``resolve_trusted_crate_key``'s own
    ``at`` parameter) — else
    :class:`mrr.domain.exceptions.TransferKeyNotValidError`. This is what
    makes a revoked, rotated, or expired key fail closed even though it
    still resolves in step (b) — trust anchoring beyond raw crypto
    (docs/spec/04 section 8.4: "New objects are rejected after
    revocation").
(d) ``mrr.domain.hashing_policy.verify_object_signature`` (E1-T02,
    UNCHANGED) passes, over the exact ADR-0004 ``exclude_none`` canonical
    form, under the RESOLVED descriptor's key — else the same
    ``mrr.crypto.exceptions.SignatureVerificationError`` /
    ``UnsupportedAlgorithmError`` that function already raises; no new type
    is needed for "bad signature" because that function already fails
    closed with its own typed errors. Decoding the RESOLVED descriptor's
    own key (never any key the contract itself claims) is what makes a
    key-substitution attack fail: an attacker who signs with their own key
    while claiming a trusted kid still fails verification against the
    RING's real key for that kid.

Unlike :func:`mrr.domain.manifest_trust.resolve_trusted_manifest_key`'s five
conditions, there is no analog of that resolver's condition (d) ("the
descriptor's key is one of the manifest's own declared ``public_keys``")
here — a ``TransferContract`` carries no ``public_keys`` list of its own to
check against, exactly like ``TaskBundle``/``EvidenceCrate`` before it.

Every precondition is checked BEFORE the next, and the function returns only
after all four hold — there is no path that returns a key for a failing
precondition (proved directly by
``tests/property/test_transfer_trust_properties.py``).

--- Direction: always the RECEIVER verifying the SENDER ---------------------

Unlike ``resolve_trusted_task_key`` (symmetric — the identical function
authenticates either leg of the MRR-FR-022/023 task negotiation round trip,
because either party can end up being the one who signed the CURRENT
content), ``resolve_trusted_transfer_key`` has exactly one direction: the
RECEIVING practice calls it, with the SENDER's own practice id and ring, to
authenticate the transfer offer before recording ANY of the five
MRR-FR-081 outcomes (accepted, adapted, rejected, deferred, unresolved) —
including the four non-accept outcomes, not only acceptance
(task-packets/E6-T01.yaml derived_decisions (c): "a forged or tampered offer
must not become recordable as 'rejected' any more than as 'accepted'").
There is no recipient-side counter-signature in this task's scope (see
``mrr.contracts.transfer_contract``'s own module docstring, "signature is
singular" section) for this resolver to authenticate in the other
direction.

--- Practice -> KeyRing -----------------------------------------------------

:func:`mrr.domain.manifest_trust.practice_key_ring` (task-packets/
E5-T02.yaml) is reused UNCHANGED — it is not specific to ``NodeManifest`` in
any way; it only ever looks at ``practice.keys``. Not re-exported here
(mirroring ``mrr.domain.crate_trust``'s own choice, not
``mrr.domain.task_trust``'s re-export — see that module's own docstring for
the "not multiplying where the same one function is importable from"
rationale, which applies identically to this, the third sibling resolver):
a caller needing it imports ``mrr.domain.manifest_trust.practice_key_ring``
directly.

--- What this module deliberately does NOT do -------------------------------

No persistence, no I/O, no network — a pure function over already-in-memory
values, CI-testable with no database (task-packets/E6-T01.yaml: "framework-
free at the domain layer"). It does not decide whether a practice itself is
trusted (that remains caller-supplied, exactly as every prior resolver's own
``trusted_*_practice_id`` is); it does not build, load, or persist a
practice registry; it enforces no replay, expiry, or cross-runtime
revocation SWEEP over already-accepted objects (E5-T07's scope) — only
whether the key resolves and is valid AT the evaluation instant this call is
given; and it is not refactored into one shared resolver with
``resolve_trusted_task_key``/``resolve_trusted_crate_key`` even though the
three are now near-identical — task-packets/E6-T01.yaml forbidden_changes:
"a future DRY of the now-four near-identical trust resolvers is a separate,
reviewed cleanup, matching the E5-T05/E5-T06 precedent."
"""

from __future__ import annotations

from datetime import datetime

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from mrr.contracts.transfer_contract import TransferContract
from mrr.domain.exceptions import TransferKeyNotValidError, TransferSignerMismatchError
from mrr.domain.key_management import KeyRing
from mrr.domain.trust_resolution import resolve_trusted_signer_key, verify_trusted_signature

__all__ = [
    "resolve_trusted_transfer_key",
]


def resolve_trusted_transfer_key(
    contract: TransferContract,
    trusted_signer_practice_id: str,
    ring: KeyRing,
    *,
    at: datetime | None = None,
) -> Ed25519PublicKey:
    """Resolve ``contract``'s signature to a trusted Ed25519 verifying key,
    anchored to ``trusted_signer_practice_id``'s own ``ring``. See the
    module docstring for the full four-condition accept rule, its typed
    failure family, and why this resolver has exactly one direction
    (receiver verifying sender), unlike ``resolve_trusted_task_key``'s
    symmetry.

    Args:
        contract: the received, not-yet-trusted ``TransferContract`` — a
            sender-origin-signed offer (MRR-FR-080).
        trusted_signer_practice_id: the id of the practice the caller
            actually trusts as THIS contract's SENDER (mirrors E5-T04's/
            E5-T05's caller-supplied trusted practice id — the trust
            decision about WHICH practice to trust is the caller's, not
            this function's). Always the sender practice here — see the
            module docstring's "Direction" section.
        ring: ``trusted_signer_practice_id``'s own trusted ``KeyRing``
            (build one from a ``mrr.contracts.practice.Practice`` with
            ``mrr.domain.manifest_trust.practice_key_ring``).
        at: the evaluation instant for validity-window/lifecycle-state
            checks. Defaults to ``datetime.now(UTC)`` — the evaluation
            instant, per docs/spec/04 section 8.4 — and is
            caller-overridable for deterministic testing, mirroring
            ``resolve_trusted_task_key``'s/``resolve_trusted_crate_key``'s
            own ``at`` parameter.

    Returns:
        The trusted ``Ed25519PublicKey`` — returned ONLY once every one of
        the four accept-rule conditions has held; never returned for any
        failing precondition.

    Raises:
        mrr.domain.exceptions.TransferSignerMismatchError: condition (a)
            fails — the contract's claimed signer is not
            ``trusted_signer_practice_id``.
        mrr.domain.exceptions.UnknownKeyIdError: condition (b) fails — the
            contract's claimed key id does not resolve in ``ring`` at all.
        mrr.domain.exceptions.TransferKeyNotValidError: condition (c) fails
            — the resolved descriptor is not valid at the evaluation
            instant (revoked, rotated, expired, or not yet valid).
        mrr.crypto.exceptions.SignatureVerificationError: condition (d)
            fails — the Ed25519 signature does not verify (bad or tampered
            signature, including a substituted signing key) under the
            resolved key.
        mrr.crypto.exceptions.UnsupportedAlgorithmError: condition (d) fails
            — ``contract.signature.algorithm`` is not ``"Ed25519"``.
    """
    verifying_key = resolve_trusted_signer_key(
        contract,
        trusted_signer_practice_id,
        ring,
        at=at,
        signer_mismatch_error=TransferSignerMismatchError,
        key_not_valid_error=TransferKeyNotValidError,
    )
    verify_trusted_signature(contract, verifying_key)
    return verifying_key
