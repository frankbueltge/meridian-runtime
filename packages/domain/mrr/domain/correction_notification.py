"""Trust-anchored resolution of a received ``CorrectionNotification``'s
signer key — task-packets/E6-T03.yaml.

docs/spec/01_SYSTEM_SPEC.md MRR-FR-094 ("Every affected practice MUST
receive a signed notification or a durable pending-delivery record") and
MRR-NFR-007 ("Cross-practice objects MUST be authenticated, authorized,
signed, hashed, and replay-protected") describe the trust-anchoring step
this module closes for ``mrr.contracts.correction_notification.
CorrectionNotification`` — the fifth trust resolver in this codebase, a
fourth-condition, fail-closed clone of
``mrr.domain.task_trust.resolve_trusted_task_key`` (itself already mirrored
by ``mrr.domain.crate_trust.resolve_trusted_crate_key`` and
``mrr.domain.transfer_trust.resolve_trusted_transfer_key``). This module
closes the gap at the OBJECT layer only — no network, no persisted practice
registry (task-packets/E6-T03.yaml forbidden_changes: the four existing
near-identical resolvers, manifest_trust/task_trust/crate_trust/
transfer_trust, are NOT refactored into one shared function even though this
one looks near-identical; a future DRY of the now-five resolvers is a
separate, reviewed cleanup).

--- The accept rule: all four conditions must hold, fail closed ------------

:func:`resolve_trusted_correction_notification_key` returns a trusted
``Ed25519PublicKey`` ONLY when ALL of the following hold, checked in this
order, each with its own DISTINCT typed error — never collapsed into one
generic failure (AGENTS.md's prohibited-shortcuts list: "collapsing
``unknown``, ``not_found``, ``contradicted``, and ``failed`` into one
generic error"):

(a) ``notification.signature.signer_practice_id ==
    trusted_notifying_practice_id`` — else
    :class:`mrr.domain.exceptions.CorrectionNotificationSignerMismatchError`.
(b) ``notification.signature.key_id`` resolves to a descriptor in ``ring`` —
    else :class:`mrr.domain.exceptions.UnknownKeyIdError` (reused verbatim —
    see that error's own docstring for why, unlike the signer-mismatch
    error, it is shared rather than mirrored by a distinctly-named sibling).
(c) that descriptor is ``ring.is_valid_at(evaluation_instant, kid)`` — active
    AND inside its validity window, evaluated at the EVALUATION instant
    (default ``datetime.now(UTC)``, caller-overridable via ``at``, mirroring
    ``resolve_trusted_task_key``'s own ``at`` parameter) — else
    :class:`mrr.domain.exceptions.CorrectionNotificationKeyNotValidError`.
    This is what makes a revoked, rotated, or expired key fail closed even
    though it still resolves in step (b) — trust anchoring beyond raw crypto
    (docs/spec/04 section 8.4: "New objects are rejected after revocation").
(d) ``mrr.domain.hashing_policy.verify_object_signature`` (E1-T02,
    UNCHANGED) passes, over the exact ADR-0004 ``exclude_none`` canonical
    form, under the RESOLVED key — else the same
    ``mrr.crypto.exceptions.SignatureVerificationError`` /
    ``UnsupportedAlgorithmError`` that function already raises; no new type
    is needed for "bad signature" because that function already fails
    closed with its own typed errors. Decoding the RESOLVED descriptor's own
    key (never any key the notification itself claims) is what makes a
    key-substitution attack fail: an attacker who signs with their own key
    while claiming a trusted kid still fails verification against the
    RING's real key for that kid.

Exactly like ``resolve_trusted_task_key``/``resolve_trusted_crate_key``,
there is no analog of ``resolve_trusted_manifest_key``'s condition (d) ("the
descriptor's key is one of the manifest's own declared ``public_keys``")
here — a ``CorrectionNotification`` carries no ``public_keys`` list of its
own to check against.

Every precondition is checked BEFORE the next, and the function returns only
after all four hold — there is no path that returns a key for a failing
precondition (proved directly by
``tests/unit/domain/test_correction_notification.py``'s own fail-closed
matrix).

--- Direction: the RECIPIENT authenticates the NOTIFYING practice ----------

This resolver has exactly one direction: the RECEIVING practice calls it
with the NOTIFYING practice's own id + ring, to authenticate a notification
that practice signed (docs/spec/01_SYSTEM_SPEC.md MRR-FR-094).
``trusted_notifying_practice_id`` is caller-supplied, exactly as every prior
resolver's trusted practice is — the trust decision about WHICH practice to
trust as this notification's signer is the caller's, not this function's.

--- What this module deliberately does NOT do -------------------------------

No persistence, no I/O, no network — a pure function over already-in-memory
values, CI-testable with no database. It does not decide whether a practice
itself is trusted (that remains caller-supplied); it does not build, load,
or persist a practice registry; it enforces no replay or validity-window
check over the notification's OWN ``nonce``/``sent_at``/``expires_at`` (that
is ``mrr.services.correction.service.CorrectionImpactService.
receive_correction_notification``'s job, mirroring
``mrr.domain.envelope_validation.validate_inbound_envelope``'s own division
of "signature trust here, replay/validity-window check at the call site");
and it never decides accept/adapt/reject/defer toward the correction itself
(E6-T04) or performs any local impact recomputation (also the service's
job) — this module answers exactly one question: is this notification's
signature trustworthy.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from mrr.contracts.correction_notification import CorrectionNotification
from mrr.crypto.keys import decode_public_key
from mrr.domain.exceptions import (
    CorrectionNotificationKeyNotValidError,
    CorrectionNotificationSignerMismatchError,
    UnknownKeyIdError,
)
from mrr.domain.hashing_policy import verify_object_signature
from mrr.domain.key_management import KeyRing

__all__ = ["resolve_trusted_correction_notification_key"]


def resolve_trusted_correction_notification_key(
    notification: CorrectionNotification,
    trusted_notifying_practice_id: str,
    ring: KeyRing,
    *,
    at: datetime | None = None,
) -> Ed25519PublicKey:
    """Resolve ``notification``'s signature to a trusted Ed25519 verifying
    key, anchored to ``trusted_notifying_practice_id``'s own ``ring``. See
    the module docstring for the full four-condition accept rule and its
    typed failure family.

    Args:
        notification: the received, not-yet-trusted ``CorrectionNotification``.
        trusted_notifying_practice_id: the id of the practice the caller
            actually trusts as THIS notification's signer (mirrors
            ``resolve_trusted_task_key``'s own caller-supplied
            ``trusted_signer_practice_id`` — the trust decision about WHICH
            practice to trust is the caller's, not this function's).
        ring: ``trusted_notifying_practice_id``'s own trusted ``KeyRing``
            (build one from a ``mrr.contracts.practice.Practice`` with
            ``mrr.domain.manifest_trust.practice_key_ring`` — reused
            unchanged).
        at: the evaluation instant for the key-validity check. Defaults to
            ``datetime.now(UTC)`` and is caller-overridable for
            deterministic testing, mirroring ``resolve_trusted_task_key``'s
            own ``at`` parameter.

    Returns:
        The trusted ``Ed25519PublicKey`` — returned ONLY once every one of
        the four accept-rule conditions has held; never returned for any
        failing precondition.

    Raises:
        mrr.domain.exceptions.CorrectionNotificationSignerMismatchError:
            condition (a) fails — the notification's claimed signer is not
            ``trusted_notifying_practice_id``.
        mrr.domain.exceptions.UnknownKeyIdError: condition (b) fails — the
            notification's claimed key id does not resolve in ``ring`` at
            all.
        mrr.domain.exceptions.CorrectionNotificationKeyNotValidError:
            condition (c) fails — the resolved descriptor is not valid at
            the evaluation instant (revoked, rotated, expired, or not yet
            valid).
        mrr.crypto.exceptions.SignatureVerificationError: condition (d)
            fails — the Ed25519 signature does not verify (bad or tampered
            signature, including a substituted signing key) under the
            resolved key.
        mrr.crypto.exceptions.UnsupportedAlgorithmError: condition (d) fails
            — ``notification.signature.algorithm`` is not ``"Ed25519"``.
    """
    if notification.signature.signer_practice_id != trusted_notifying_practice_id:
        raise CorrectionNotificationSignerMismatchError(
            claimed_signer_practice_id=notification.signature.signer_practice_id,
            trusted_practice_id=trusted_notifying_practice_id,
        )

    kid = notification.signature.key_id
    descriptor = ring.get(kid)
    if descriptor is None:
        raise UnknownKeyIdError(kid)

    evaluation_instant = at if at is not None else datetime.now(UTC)
    if not ring.is_valid_at(evaluation_instant, kid):
        raise CorrectionNotificationKeyNotValidError(kid, at=evaluation_instant)

    # Decode the RESOLVED descriptor's own key — never any key the
    # notification itself claims — so a substituted signing key cannot be
    # accepted even if it happens to claim a trusted kid.
    verifying_key = decode_public_key(descriptor.encoded_public_key)
    verify_object_signature(
        verifying_key,
        json.loads(notification.model_dump_json(exclude_none=True)),
        notification.signature.value,
        algorithm=notification.signature.algorithm,
    )
    return verifying_key
