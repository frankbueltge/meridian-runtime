"""Shared core of the five trust-anchored signer-key resolvers — task-packets/
E9-T00b.yaml (behavior-preserving DRY consolidation).

``mrr.domain.manifest_trust.resolve_trusted_manifest_key`` (E5-T02),
``mrr.domain.task_trust.resolve_trusted_task_key`` (E5-T04),
``mrr.domain.crate_trust.resolve_trusted_crate_key`` (E5-T05),
``mrr.domain.transfer_trust.resolve_trusted_transfer_key`` (E6-T01), and
``mrr.domain.correction_notification.resolve_trusted_correction_notification_key``
(E6-T03) each independently implemented the identical three-condition,
fail-closed accept rule below, differing only in (i) which pair of
distinctly-named typed errors to raise for the signer-mismatch/key-not-valid
conditions (a deliberate, KEPT design choice — AGENTS.md's prohibited-
shortcuts list forbids collapsing distinct failure kinds into one generic
type) and (ii) ``manifest_trust``'s own extra, genuinely unique fourth
condition (the declared-``public_keys`` check), which has no analog in the
other four and stays in ``manifest_trust.py`` itself. Every task packet that
added the second through fifth resolver flagged this exact duplication as a
deliberate, deferred cleanup (task-packets/E5-T05.yaml, E6-T01.yaml,
E6-T03.yaml forbidden_changes, echoed in ``transfer_trust.py``'s and
``correction_notification.py``'s own module docstrings) — this module, and
the five callers' rewrite to delegate into it, is that cleanup.

--- The shared accept rule: three conditions, fail closed --------------------

:func:`resolve_trusted_signer_key` returns a trusted ``Ed25519PublicKey``
ONLY when ALL of the following hold, checked in this order, each with its own
DISTINCT typed error:

(a) ``obj.signature.signer_practice_id == trusted_practice_id`` — else the
    caller-supplied ``signer_mismatch_error`` factory is called with
    ``claimed_signer_practice_id``/``trusted_practice_id`` and the result is
    raised (each of the five callers binds its own distinctly-named
    ``<X>SignerMismatchError``).
(b) ``obj.signature.key_id`` resolves to a descriptor in ``ring`` — else
    :class:`mrr.domain.exceptions.UnknownKeyIdError` (the one condition all
    five callers already shared before this consolidation — see that error's
    own docstring for why it is reused verbatim rather than mirrored by a
    per-object sibling).
(c) that descriptor is ``ring.is_valid_at(evaluation_instant, kid)`` — active
    AND inside its validity window, evaluated at ``at`` if given, else
    ``datetime.now(UTC)`` — else the caller-supplied ``key_not_valid_error``
    factory is called with ``kid``/``at`` and the result is raised (each
    caller binds its own distinctly-named ``<X>KeyNotValidError``). This is
    what makes a revoked, rotated, or expired key fail closed even though it
    still resolves in step (b) (docs/spec/04_SECURITY_AND_POLICY.md section
    8.4: "New objects are rejected after revocation").

Once all three hold, the RESOLVED descriptor's own key is decoded
(``mrr.crypto.keys.decode_public_key``) and returned — never any key ``obj``
itself claims outside what the ring resolved, which is what makes a
key-substitution attack fail even when the attacker's forged signature
claims a trusted ``kid``: verification (:func:`verify_trusted_signature`)
always happens against the ring's own key for that kid, not the attacker's.

:func:`verify_trusted_signature` is the final step — verifying the ALREADY-
RESOLVED key against ``obj``'s own signed payload
(``mrr.domain.hashing_policy.verify_object_signature``, E1-T02, UNCHANGED,
over the exact ADR-0004 ``exclude_none`` canonical form). It is a separate
function, not folded into ``resolve_trusted_signer_key``, because
``manifest_trust`` needs to run its own unique declared-``public_keys``
condition BETWEEN key resolution and signature verification — the other four
callers simply call it immediately after ``resolve_trusted_signer_key``.

--- Object-type-agnostic by a minimal structural Protocol -------------------

:class:`SignedObject` requires only what this shared logic actually touches:
``.signature`` (the shared ``mrr.contracts.common.Signature`` Pydantic type
every one of the five contracts already declares this field as, confirmed by
direct grep of all five contracts' own ``signature: Signature`` field
declarations) and ``.model_dump_json(*, exclude_none=...)`` (which every
Pydantic ``BaseModel`` already provides). Nothing here imports any of the
five objects' own contract modules — the whole point of a shared core is
that it does not need to know which of the five it was given.

--- Error identity is injected, never invented here --------------------------

:class:`SignerMismatchErrorFactory` and :class:`KeyNotValidErrorFactory` are
the two small per-call Protocols a caller's own typed error CLASSES already
satisfy structurally (a class is callable with its ``__init__`` signature
minus ``self``): ``ManifestSignerMismatchError``, ``TaskSignerMismatchError``,
``CrateSignerMismatchError``, ``TransferSignerMismatchError``, and
``CorrectionNotificationSignerMismatchError`` all share the EXACT constructor
keyword shape (confirmed by direct reading of
``mrr.domain.exceptions``), and likewise for the five ``<X>KeyNotValidError``
siblings — this module never constructs, imports, or references any of those
ten error classes itself; it only calls whichever pair its caller bound.
``mrr.domain.exceptions`` is not touched by this consolidation at all: no
error class is added, removed, renamed, or given a changed constructor
signature.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from mrr.contracts.common import Signature
from mrr.crypto.keys import decode_public_key
from mrr.domain.exceptions import DomainError, UnknownKeyIdError
from mrr.domain.hashing_policy import verify_object_signature
from mrr.domain.key_management import KeyRing

__all__ = [
    "KeyNotValidErrorFactory",
    "SignedObject",
    "SignerMismatchErrorFactory",
    "resolve_trusted_signer_key",
    "verify_trusted_signature",
]


class SignedObject(Protocol):
    """The minimal structural shape :func:`resolve_trusted_signer_key` and
    :func:`verify_trusted_signature` need from a cross-practice object —
    every one of ``NodeManifest``/``TaskBundle``/``EvidenceCrate``/
    ``TransferContract``/``CorrectionNotification`` already satisfies this
    (each declares ``signature: Signature`` and, as a Pydantic
    ``BaseModel``, already provides ``model_dump_json``); no object-specific
    contracts module is imported here to check that.
    """

    signature: Signature

    def model_dump_json(self, *, exclude_none: bool = ...) -> str: ...


class SignerMismatchErrorFactory(Protocol):
    """The constructor shape every ``<X>SignerMismatchError`` in
    ``mrr.domain.exceptions`` already has — a caller binds its own class
    (e.g. ``ManifestSignerMismatchError``) as this argument; calling the
    class itself is calling its ``__init__``.
    """

    def __call__(
        self, *, claimed_signer_practice_id: str, trusted_practice_id: str
    ) -> DomainError: ...


class KeyNotValidErrorFactory(Protocol):
    """The constructor shape every ``<X>KeyNotValidError`` in
    ``mrr.domain.exceptions`` already has — a caller binds its own class
    (e.g. ``ManifestKeyNotValidError``) as this argument.
    """

    def __call__(self, kid: str, *, at: datetime) -> DomainError: ...


def resolve_trusted_signer_key(
    obj: SignedObject,
    trusted_practice_id: str,
    ring: KeyRing,
    *,
    at: datetime | None,
    signer_mismatch_error: SignerMismatchErrorFactory,
    key_not_valid_error: KeyNotValidErrorFactory,
) -> Ed25519PublicKey:
    """Resolve ``obj``'s signature to a trusted Ed25519 verifying key,
    anchored to ``trusted_practice_id``'s own ``ring``. See the module
    docstring for the full three-condition shared accept rule; a caller with
    its own extra condition (only ``manifest_trust`` has one today) runs it
    between this call and :func:`verify_trusted_signature`.

    Args:
        obj: the received, not-yet-trusted signed object.
        trusted_practice_id: the id of the practice the caller actually
            trusts as ``obj``'s signer — the trust decision about WHICH
            practice to trust is the caller's, never derived or looked up
            here.
        ring: ``trusted_practice_id``'s own trusted ``KeyRing``.
        at: the evaluation instant for the key-validity check, or ``None``
            to use ``datetime.now(UTC)`` — mirrors every one of the five
            callers' own ``at`` parameter, which is what actually carries
            the ``None``-means-now default at the public API surface.
        signer_mismatch_error: the typed-error constructor to call (with
            ``claimed_signer_practice_id``/``trusted_practice_id``) when
            condition (a) fails.
        key_not_valid_error: the typed-error constructor to call (with
            ``kid``/``at``) when condition (c) fails.

    Returns:
        The RESOLVED descriptor's own decoded ``Ed25519PublicKey`` — never
        any key ``obj`` itself claims beyond what ``ring`` resolved for its
        claimed ``kid``. Returned only once all three conditions hold; the
        signature itself is NOT yet verified at this point (see
        :func:`verify_trusted_signature`).

    Raises:
        The caller-bound ``signer_mismatch_error`` result: condition (a)
            fails — ``obj``'s claimed signer is not ``trusted_practice_id``.
        mrr.domain.exceptions.UnknownKeyIdError: condition (b) fails —
            ``obj``'s claimed key id does not resolve in ``ring`` at all.
        The caller-bound ``key_not_valid_error`` result: condition (c)
            fails — the resolved descriptor is not valid at the evaluation
            instant (revoked, rotated, expired, or not yet valid).
    """
    if obj.signature.signer_practice_id != trusted_practice_id:
        raise signer_mismatch_error(
            claimed_signer_practice_id=obj.signature.signer_practice_id,
            trusted_practice_id=trusted_practice_id,
        )

    kid = obj.signature.key_id
    descriptor = ring.get(kid)
    if descriptor is None:
        raise UnknownKeyIdError(kid)

    evaluation_instant = at if at is not None else datetime.now(UTC)
    if not ring.is_valid_at(evaluation_instant, kid):
        raise key_not_valid_error(kid, at=evaluation_instant)

    # Decode the RESOLVED descriptor's own key — never any key obj itself
    # claims — so a substituted signing key cannot be accepted even if it
    # happens to claim a trusted kid (each of the five callers' own
    # key-substitution acceptance test).
    return decode_public_key(descriptor.encoded_public_key)


def verify_trusted_signature(obj: SignedObject, verifying_key: Ed25519PublicKey) -> None:
    """Verify ``obj``'s signature under the ALREADY-RESOLVED
    ``verifying_key`` (the return value of :func:`resolve_trusted_signer_key`,
    possibly after a caller-side extra condition has run in between — see
    ``manifest_trust.resolve_trusted_manifest_key``).

    Raises:
        mrr.crypto.exceptions.SignatureVerificationError: the Ed25519
            signature does not verify (bad or tampered signature, including
            a substituted signing key) under ``verifying_key``.
        mrr.crypto.exceptions.UnsupportedAlgorithmError:
            ``obj.signature.algorithm`` is not ``"Ed25519"``.
    """
    verify_object_signature(
        verifying_key,
        json.loads(obj.model_dump_json(exclude_none=True)),
        obj.signature.value,
        algorithm=obj.signature.algorithm,
    )
