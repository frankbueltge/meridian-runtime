"""Deterministic, fail-closed inbound validation of a received
``NodeMessageEnvelope`` — task-packets/E5-T03.yaml.

docs/spec/04_SECURITY_AND_POLICY.md section 8.2 ("Task and result envelopes
include nonces, expiry, recipient identity, content hashes, and signatures.
Processed envelope IDs are retained for replay detection according to
policy") and section 8.3 ("A node refusal must not leak sensitive policy
details. It may return a coarse reason code ...") describe the accept rule
this module implements at the OBJECT layer only — no network, no
persistence, no durable processed-id store (task-packets/E5-T03.yaml
forbidden_changes: that store is task-packets/E5-T07.yaml's scope). This
module is the envelope-shaped sibling of ``mrr.domain.manifest_trust``
(task-packets/E5-T02.yaml): the same "resolve the sender's trusted key,
fail closed with a distinct typed error per condition" discipline, applied
to a ``mrr.contracts.node_message_envelope.NodeMessageEnvelope`` instead of
a ``mrr.contracts.node_manifest.NodeManifest``.

--- The accept rule: all five conditions must hold, fail closed ------------

:func:`validate_inbound_envelope` returns (``None``) ONLY when ALL of the
following hold, checked in this order, each with its own DISTINCT typed
error — never collapsed into one generic failure (AGENTS.md's
prohibited-shortcuts list: "collapsing ``unknown``, ``not_found``,
``contradicted``, and ``failed`` into one generic error"):

1. ``envelope.recipient_node_id == this_node_id`` — else
   :class:`mrr.domain.exceptions.EnvelopeRecipientMismatchError`.
2. ``envelope.sent_at <= at < envelope.expires_at`` (``at`` defaults to the
   RECEIPT instant, ``datetime.now(UTC)``, caller-overridable for
   deterministic testing, mirroring
   ``resolve_trusted_manifest_key``'s own ``at`` parameter) — else
   :class:`mrr.domain.exceptions.EnvelopeNotWithinValidityWindowError`.
3. the carried payload's own ``content_hash`` (``envelope.payload.get(
   "content_hash")``) equals ``envelope.payload_content_hash`` — else
   :class:`mrr.domain.exceptions.EnvelopePayloadContentHashMismatchError`.
   This is a CONSISTENCY check (the envelope's declared hash agrees with
   whatever hash the payload itself already carries), not an independent
   recomputation from payload bytes — this module is payload-agnostic and
   has no opinion on any specific payload kind's own hashing policy
   (task-packets/E5-T04/E5-T05's concern).
4. ``already_processed(envelope.message_id)`` is ``False`` — else
   :class:`mrr.domain.exceptions.EnvelopeAlreadyProcessedError`. Replay is a
   CHECK only: ``already_processed`` is a caller-supplied predicate over
   whatever store the caller maintains; this module builds no persistence
   of its own (task-packets/E5-T07.yaml's scope).
5. the transport signature verifies under the sender's TRUSTED key,
   resolved from ``ring`` exactly as E5-T02 resolves a manifest signer:
   ``envelope.signature.signer_practice_id == trusted_sender_practice_id``
   (else :class:`mrr.domain.exceptions.EnvelopeSignerMismatchError`),
   ``envelope.signature.key_id`` resolves in ``ring`` (else
   :class:`mrr.domain.exceptions.UnknownKeyIdError`, reused verbatim — see
   its own docstring), that descriptor is ``ring.is_valid_at(at, kid)``
   (else :class:`mrr.domain.exceptions.EnvelopeKeyNotValidError`), and
   ``mrr.domain.hashing_policy.verify_object_signature`` passes over the
   exact ADR-0004 ``exclude_none`` canonical form under the resolved key
   (else the same ``mrr.crypto.exceptions.SignatureVerificationError``/
   ``UnsupportedAlgorithmError`` that function already raises).

   Unlike E5-T02's five-condition resolver, there is no analog of condition
   (d) ("the descriptor's key is one of the manifest's own declared
   public_keys") here — task-packets/E5-T03.yaml derived_decisions is
   explicit that this manifest-specific check does NOT apply to an
   envelope and is deliberately omitted; an envelope carries no
   ``public_keys`` list of its own to check against.

Every precondition is checked BEFORE the next, and the function returns
(``None``) only after all five hold — there is no path that returns
normally for a failing precondition (proved directly by
``tests/property/test_envelope_validation_properties.py``).

--- Coarse rejection reason (docs/spec/04 section 8.3) ----------------------

:data:`EnvelopeRejectionReason` and :func:`coarse_rejection_reason` mirror
``mrr.services.capability_registry.service.ManifestRejectionReason``/
``_REJECTION_REASON_BY_ERROR_TYPE``'s identical translation from a specific
typed failure to a coarse, non-leaking category — provided here, at the
DOMAIN layer rather than a service, because task-packets/E5-T03.yaml's
allowed_paths do not include ``services/``. A future node-runtime service
(task-packets/E5-T04.yaml or E5-T07.yaml) is expected to wire this into an
actual rejection event exactly as ``CapabilityRegistry.receive`` already
does for manifests; this module only proves the mapping itself is total
over ``validate_inbound_envelope``'s own typed failures.

--- What this module deliberately does NOT do -------------------------------

No persistence, no I/O, no network — a pure function over already-in-memory
values, CI-testable with no database. It does not decide whether a sender
PRACTICE is trusted (caller-supplied, exactly as E5-T02's own
``trusted_practice_id``); it builds no durable processed-message-id store
and enforces no revocation-sweep over already-accepted messages (E5-T07);
and it does not decide what to DO with an accepted envelope's payload (the
task decision is E5-T04, the result flow is E5-T05) — accepting an envelope
here means only "this message is authentic, fresh, and addressed to me",
nothing about the payload it carries.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from mrr.contracts.node_message_envelope import NodeMessageEnvelope
from mrr.crypto.exceptions import SignatureVerificationError, UnsupportedAlgorithmError
from mrr.crypto.keys import decode_public_key
from mrr.domain.exceptions import (
    EnvelopeAlreadyProcessedError,
    EnvelopeKeyNotValidError,
    EnvelopeNotWithinValidityWindowError,
    EnvelopePayloadContentHashMismatchError,
    EnvelopeRecipientMismatchError,
    EnvelopeSignerMismatchError,
    UnknownKeyIdError,
)
from mrr.domain.hashing_policy import verify_object_signature
from mrr.domain.key_management import KeyRing

__all__ = [
    "AlreadyProcessed",
    "EnvelopeRejectionReason",
    "coarse_rejection_reason",
    "validate_inbound_envelope",
]

#: The caller-supplied replay-check predicate: ``True`` means "this
#: message_id has already been processed" (fail closed). No durable
#: implementation is provided here — the store backing this predicate in
#: production is task-packets/E5-T07.yaml's scope; a caller passes a
#: closure over whatever store (or, in tests, an in-memory set) it has.
AlreadyProcessed = Callable[[str], bool]


def validate_inbound_envelope(
    envelope: NodeMessageEnvelope,
    *,
    this_node_id: str,
    trusted_sender_practice_id: str,
    ring: KeyRing,
    already_processed: AlreadyProcessed,
    at: datetime | None = None,
) -> None:
    """Validate ``envelope`` for inbound acceptance at ``this_node_id``. See
    the module docstring for the full five-condition accept rule and its
    typed failure family.

    Args:
        envelope: the received, not-yet-trusted ``NodeMessageEnvelope``.
        this_node_id: this receiving node's own id — the envelope is
            accepted only if it is addressed here.
        trusted_sender_practice_id: the id of the practice the caller
            actually trusts as this envelope's sender (mirrors E5-T02's
            caller-supplied ``trusted_practice_id`` — the trust decision
            about WHICH practice to trust is the caller's, not this
            function's).
        ring: ``trusted_sender_practice_id``'s own trusted ``KeyRing``
            (build one from a ``mrr.contracts.practice.Practice`` with
            ``mrr.domain.manifest_trust.practice_key_ring`` — reused
            unchanged).
        already_processed: a caller-supplied replay-check predicate; see
            :data:`AlreadyProcessed`.
        at: the evaluation instant for the validity-window and
            key-validity checks. Defaults to ``datetime.now(UTC)`` — the
            RECEIPT instant — and is caller-overridable for deterministic
            testing, mirroring ``resolve_trusted_manifest_key``'s own
            ``at`` parameter.

    Returns:
        ``None`` — returned ONLY once every one of the five accept-rule
        conditions has held; never returned for any failing precondition.

    Raises:
        mrr.domain.exceptions.EnvelopeRecipientMismatchError: condition 1
            fails — ``envelope.recipient_node_id`` is not
            ``this_node_id``.
        mrr.domain.exceptions.EnvelopeNotWithinValidityWindowError:
            condition 2 fails — the evaluation instant is before
            ``sent_at`` or at/after ``expires_at``.
        mrr.domain.exceptions.EnvelopePayloadContentHashMismatchError:
            condition 3 fails — the carried payload's own ``content_hash``
            does not equal ``envelope.payload_content_hash``.
        mrr.domain.exceptions.EnvelopeAlreadyProcessedError: condition 4
            fails — ``already_processed(envelope.message_id)`` is ``True``.
        mrr.domain.exceptions.EnvelopeSignerMismatchError: condition 5
            fails — the envelope's claimed signer is not
            ``trusted_sender_practice_id``.
        mrr.domain.exceptions.UnknownKeyIdError: condition 5 fails — the
            envelope's claimed key id does not resolve in ``ring`` at all.
        mrr.domain.exceptions.EnvelopeKeyNotValidError: condition 5 fails —
            the resolved descriptor is not valid at the evaluation instant.
        mrr.crypto.exceptions.SignatureVerificationError: condition 5
            fails — the Ed25519 signature does not verify (bad or
            tampered signature) under the resolved key.
        mrr.crypto.exceptions.UnsupportedAlgorithmError: condition 5 fails
            — ``envelope.signature.algorithm`` is not ``"Ed25519"``.
    """
    if envelope.recipient_node_id != this_node_id:
        raise EnvelopeRecipientMismatchError(
            envelope.message_id, envelope.recipient_node_id, this_node_id
        )

    evaluation_instant = at if at is not None else datetime.now(UTC)
    if not (envelope.sent_at <= evaluation_instant < envelope.expires_at):
        raise EnvelopeNotWithinValidityWindowError(
            envelope.message_id, envelope.sent_at, envelope.expires_at, evaluation_instant
        )

    payload_own_hash = envelope.payload.get("content_hash")
    if payload_own_hash != envelope.payload_content_hash:
        raise EnvelopePayloadContentHashMismatchError(
            envelope.message_id, envelope.payload_content_hash, payload_own_hash
        )

    if already_processed(envelope.message_id):
        raise EnvelopeAlreadyProcessedError(envelope.message_id)

    if envelope.signature.signer_practice_id != trusted_sender_practice_id:
        raise EnvelopeSignerMismatchError(
            claimed_signer_practice_id=envelope.signature.signer_practice_id,
            trusted_practice_id=trusted_sender_practice_id,
        )

    kid = envelope.signature.key_id
    descriptor = ring.get(kid)
    if descriptor is None:
        raise UnknownKeyIdError(kid)

    if not ring.is_valid_at(evaluation_instant, kid):
        raise EnvelopeKeyNotValidError(kid, at=evaluation_instant)

    verifying_key = decode_public_key(descriptor.encoded_public_key)
    verify_object_signature(
        verifying_key,
        json.loads(envelope.model_dump_json(exclude_none=True)),
        envelope.signature.value,
        algorithm=envelope.signature.algorithm,
    )


#: docs/spec/04_SECURITY_AND_POLICY.md section 8.3's "a coarse reason code",
#: without naming the set — this task's own minimal, coarse proposal (not
#: spec-defined), matching ``mrr.services.capability_registry.service.
#: ManifestRejectionReason``'s own precedent and its "flagged as an open
#: specification question" caveat. Each member names exactly one of
#: ``validate_inbound_envelope``'s five fail-closed conditions; the
#: SPECIFIC reason (which node id, which timestamps, the claimed vs.
#: trusted practice id, ...) stays on the raised typed exception and is
#: never meant to be persisted/echoed to the sender — only this coarse
#: category is.
EnvelopeRejectionReason = Literal[
    "wrong_recipient",
    "outside_validity_window",
    "payload_hash_mismatch",
    "already_processed",
    "signer_mismatch",
    "unknown_key",
    "key_not_valid",
    "signature_invalid",
]

#: Maps each of ``validate_inbound_envelope``'s eight distinct typed failure
#: exceptions to its coarse ``EnvelopeRejectionReason`` — the one place this
#: module translates a specific typed reason into the public, coarse
#: category a future rejection event (task-packets/E5-T04.yaml or
#: E5-T07.yaml) would record. ``SignatureVerificationError``/
#: ``UnsupportedAlgorithmError`` both map to ``"signature_invalid"`` —
#: condition 5's final leg failing either because the signature does not
#: verify or because the algorithm is unsupported is, at the coarse-refusal
#: level, the same "this signature is not acceptable" category
#: (docs/spec/04 section 8.3 asks only for a coarse code, not a
#: crypto-internals distinction) — mirroring
#: ``_REJECTION_REASON_BY_ERROR_TYPE``'s identical choice for manifests.
_REJECTION_REASON_BY_ERROR_TYPE: dict[type[Exception], EnvelopeRejectionReason] = {
    EnvelopeRecipientMismatchError: "wrong_recipient",
    EnvelopeNotWithinValidityWindowError: "outside_validity_window",
    EnvelopePayloadContentHashMismatchError: "payload_hash_mismatch",
    EnvelopeAlreadyProcessedError: "already_processed",
    EnvelopeSignerMismatchError: "signer_mismatch",
    UnknownKeyIdError: "unknown_key",
    EnvelopeKeyNotValidError: "key_not_valid",
    SignatureVerificationError: "signature_invalid",
    UnsupportedAlgorithmError: "signature_invalid",
}

#: The exact exception types ``validate_inbound_envelope`` can raise — the
#: tuple form a caller's ``except`` needs, kept as the single source of
#: truth alongside ``_REJECTION_REASON_BY_ERROR_TYPE`` so the two can never
#: drift apart (mirrors ``mrr.services.capability_registry.service.
#: _MANIFEST_TRUST_FAILURES``'s identical precedent).
ENVELOPE_VALIDATION_FAILURES: tuple[type[Exception], ...] = tuple(
    _REJECTION_REASON_BY_ERROR_TYPE.keys()
)


def coarse_rejection_reason(exc: Exception) -> EnvelopeRejectionReason:
    """Translate one of ``validate_inbound_envelope``'s typed failures to
    its coarse :data:`EnvelopeRejectionReason` (docs/spec/04_SECURITY_AND_
    POLICY.md section 8.3: "a coarse reason code" — detail stays on the
    typed exception; only this coarse category is meant to be
    persisted/echoed to the sender).

    Raises:
        KeyError: if ``exc`` is not one of ``validate_inbound_envelope``'s
            own typed failures — never silently mapped to a made-up
            category.
    """
    return _REJECTION_REASON_BY_ERROR_TYPE[type(exc)]
