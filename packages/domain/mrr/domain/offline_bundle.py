"""Deterministic, content-addressed export/import of a signed
``OfflineBundle`` — task-packets/E5-T06.yaml, the OFFLINE counterpart of
E5-T03's online single-envelope path (``mrr.domain.envelope_validation``).

docs/spec/01_SYSTEM_SPEC.md section 8.4 ("Federated offline: Air-gapped or
intermittent nodes use signed inbox/outbox bundles. Import and export MUST
verify signatures, expiry, replay protection, and object hashes") and
docs/spec/03_API_AND_EVENTS.md section 4.2 ("A recipient MUST reject
expired, replayed, misaddressed, untrusted, or hash-invalid envelopes before
deserializing untrusted nested content beyond what is required for
verification") describe the accept rule this module implements at the
OBJECT layer only — no network, no persistence, no durable processed-id
store (task-packets/E5-T06.yaml forbidden_changes: that store, and any
revocation sweep, is task-packets/E5-T07.yaml's scope).

--- Export: :func:`build_outbox_bundle` --------------------------------------

Assembles an :class:`mrr.contracts.offline_bundle.OfflineBundle` from
already-signed :class:`mrr.contracts.node_message_envelope.NodeMessageEnvelope`
objects destined for ONE recipient node: one :class:`~mrr.contracts.
offline_bundle.BundleEntry` per envelope (``message_id``/``payload_kind``
copied verbatim, ``envelope_content_hash`` computed via the UNCHANGED
``mrr.domain.hashing_policy.compute_content_hash`` over that envelope's own
ADR-0004 ``exclude_none=True`` canonical form), then the SENDING node signs
the whole assembled bundle's own ``exclude_none=True`` canonical form
(``mrr.domain.hashing_policy.sign_object``, UNCHANGED) — covering
``entries``, ``envelopes``, ``recipient_node_id``, the validity window, and
every other field at once, which is what authenticates the batch's
membership, order, recipient, and validity window as a single unit (task-
packets/E5-T06.yaml derived_decisions).

Every identifying/temporal field (``bundle_id``, ``bundle_nonce``,
``created_at``, ``expires_at``, and ``signed_at``) is CALLER-supplied, never
auto-generated inside this function — unlike, say,
``mrr.services.node_runtime.evidence_crate.EvidenceCrateSealer.seal``
(which mints its own id/timestamp because it is a service with exactly one
call site per real run), this is a pure domain function callers may invoke
repeatedly with the SAME metadata to get the SAME bundle back
(task-packets/E5-T06.yaml acceptance test: "round-trip determinism ...
yields a byte-identical canonical exclude_none form and the same bundle
content hash"). ``signed_at`` defaults to ``created_at`` when omitted (the
sender signs at the bundle's own creation instant) rather than
``datetime.now(UTC)``, so that determinism holds without every caller
having to pass it explicitly.

--- Import: :func:`validate_inbound_bundle` ----------------------------------

Returns the verified ``list[NodeMessageEnvelope]`` (in order) ONLY when ALL
of the following hold, checked in this order, each with its own DISTINCT
typed error — never collapsed into one generic failure (AGENTS.md's
prohibited-shortcuts list: "collapsing ``unknown``, ``not_found``,
``contradicted``, and ``failed`` into one generic error"):

1. ``bundle.recipient_node_id == this_node_id`` — else
   :class:`mrr.domain.exceptions.BundleRecipientMismatchError`.
2. ``bundle.created_at <= at < bundle.expires_at`` (``at`` defaults to the
   RECEIPT instant, ``datetime.now(UTC)``, caller-overridable, mirroring
   ``validate_inbound_envelope``'s own ``at`` parameter) — else
   :class:`mrr.domain.exceptions.BundleNotWithinValidityWindowError`.
3. ``already_processed(bundle.bundle_id)`` is ``False`` — else
   :class:`mrr.domain.exceptions.BundleAlreadyProcessedError`. Replay is a
   CHECK only: ``already_processed`` is a caller-supplied predicate over
   whatever store the caller maintains (a SEPARATE namespace from
   ``mrr.domain.envelope_validation.AlreadyProcessed``'s own per-envelope
   ``message_id`` predicate — a bundle id and a message id are different
   identifier spaces); this module builds no persistence of its own
   (task-packets/E5-T07.yaml's scope).
4. the bundle signature verifies under the sender's TRUSTED key, resolved
   from ``ring`` exactly as E5-T02/E5-T03/E5-T04/E5-T05 resolve a signer:
   ``bundle.signature.signer_practice_id == trusted_sender_practice_id``
   (else :class:`mrr.domain.exceptions.BundleSignerMismatchError`),
   ``bundle.signature.key_id`` resolves in ``ring`` (else
   :class:`mrr.domain.exceptions.UnknownKeyIdError`, reused verbatim — see
   its own docstring), that descriptor is ``ring.is_valid_at(at, kid)``
   (else :class:`mrr.domain.exceptions.BundleKeyNotValidError`), and
   ``mrr.domain.hashing_policy.verify_object_signature`` passes over the
   exact ADR-0004 ``exclude_none`` canonical form under the RESOLVED
   descriptor's key — never a key the bundle itself claims (else the same
   ``mrr.crypto.exceptions.SignatureVerificationError``/
   ``UnsupportedAlgorithmError`` that function already raises). Because
   this signature covers ``entries``/``envelopes``/``recipient_node_id``/
   the validity window all at once, an added, dropped, reordered, or
   retargeted entry (or carried envelope) breaks THIS check, not a
   separate one (task-packets/E5-T06.yaml's "batch-tamper matrix"
   acceptance test).
5. EVERY entry's declared ``envelope_content_hash`` equals the ACTUAL
   content hash of its carried envelope at that same position (recomputed
   via the UNCHANGED ``mrr.domain.hashing_policy.compute_content_hash``,
   never trusted from the entries list alone) — else
   :class:`mrr.domain.exceptions.BundleEntryHashMismatchError`. Checked
   LAST, only once the bundle signature has already verified: a
   defense-in-depth check independent of the signature (AGENTS.md rule 9,
   "No cross-practice object may be accepted without signature AND hash
   verification") — see that exception's own docstring for why this
   cannot simply be inferred from step 4 alone. ``bundle.entries`` and
   ``bundle.envelopes`` are already guaranteed to correspond 1:1, in
   order, by ``OfflineBundle``'s own contract-level
   ``_entries_correspond_to_envelopes`` validator for any genuinely
   ``model_validate``-constructed bundle — this step only recomputes and
   compares the HASH value itself, which no contract-level validator can
   do (that requires the hashing policy, a domain-layer concern).

Every precondition is checked BEFORE the next, and the function returns
only after all five hold — there is no path that returns normally for a
failing precondition.

--- Reuse, not reimplementation: per-envelope validation stays E5-T03's ----

:func:`validate_inbound_bundle` authenticates the BATCH only — recipient,
validity window, replay, bundle signature, and entry hashes. It does
**not** re-implement, wrap, or shortcut
``mrr.domain.envelope_validation.validate_inbound_envelope`` for the
envelopes it returns (task-packets/E5-T06.yaml forbidden_changes: "reuse
UNCHANGED ... it does not re-model, re-sign, or re-validate an individual
envelope"). A caller that accepts a bundle is expected to hand EACH
returned envelope to ``validate_inbound_envelope`` exactly as it would an
online-delivered one — proving the offline path reuses every online
verification rather than bypassing it (see
``tests/unit/domain/test_offline_bundle_chain.py`` for the full
bundle -> envelope -> crate demonstration).

--- Coarse rejection reason (docs/spec/04 section 8.3) ----------------------

:data:`BundleRejectionReason` and :func:`coarse_bundle_rejection_reason`
mirror ``mrr.domain.envelope_validation.EnvelopeRejectionReason``/
``coarse_rejection_reason``'s identical translation from a specific typed
failure to a coarse, non-leaking category.

--- What this module deliberately does NOT do -------------------------------

No persistence, no I/O, no network — pure functions over already-in-memory
values, CI-testable with no database. No real encryption/decryption is
implemented: ``bundle.encryption`` is carried and copied verbatim, never
inspected or acted on by either function here (docs/spec/04 section 4.1 is
explicitly deferred — task-packets/E5-T06.yaml forbidden_changes). No
physical air-gap transport medium (file/USB/media byte I/O) is built or
assumed — both functions operate on already-in-memory ``OfflineBundle``
objects. No durable processed-id store or revocation sweep is built
(E5-T07). This module does not decide what to DO with an accepted bundle's
envelopes beyond returning them — the task decision (E5-T04) and result
flow (E5-T05) remain entirely downstream, exactly as they are for an
online-delivered envelope.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Literal

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.contracts.common import Signature
from mrr.contracts.node_message_envelope import NodeMessageEnvelope
from mrr.contracts.offline_bundle import BundleEncryption, BundleEntry, OfflineBundle
from mrr.crypto.exceptions import SignatureVerificationError, UnsupportedAlgorithmError
from mrr.crypto.keys import decode_public_key
from mrr.domain.exceptions import (
    BundleAlreadyProcessedError,
    BundleEntryHashMismatchError,
    BundleKeyNotValidError,
    BundleNotWithinValidityWindowError,
    BundleRecipientMismatchError,
    BundleSignerMismatchError,
    UnknownKeyIdError,
)
from mrr.domain.hashing_policy import compute_content_hash, sign_object, verify_object_signature
from mrr.domain.key_management import KeyRing

__all__ = [
    "BUNDLE_VALIDATION_FAILURES",
    "BundleAlreadyProcessed",
    "BundleRejectionReason",
    "build_outbox_bundle",
    "coarse_bundle_rejection_reason",
    "validate_inbound_bundle",
]

#: The caller-supplied replay-check predicate: ``True`` means "this
#: bundle_id has already been processed" (fail closed). A separate
#: namespace from ``mrr.domain.envelope_validation.AlreadyProcessed``'s own
#: per-envelope ``message_id`` predicate — no durable implementation is
#: provided here (task-packets/E5-T07.yaml's scope); a caller passes a
#: closure over whatever store (or, in tests, an in-memory set) it has.
BundleAlreadyProcessed = Callable[[str], bool]

#: Placeholder ``Signature.value`` used only while assembling the draft
#: bundle below, before the real signature is computed. Never the value
#: actually signed over or returned: ``mrr.domain.hashing_policy.
#: prepare_for_signature`` strips the entire ``signature`` field before
#: signing, so this placeholder can never leak into what gets hashed or
#: signed. Mirrors ``mrr.services.node_runtime.evidence_crate``'s own
#: ``_PLACEHOLDER_SIGNATURE_VALUE`` (``min_length=40`` on
#: ``mrr.contracts.common.Signature.value``).
_PLACEHOLDER_SIGNATURE_VALUE = "0" * 44


def build_outbox_bundle(
    envelopes: Sequence[NodeMessageEnvelope],
    *,
    bundle_id: str,
    bundle_nonce: str,
    sender_node_id: str,
    sender_practice_id: str,
    recipient_node_id: str,
    created_at: datetime,
    expires_at: datetime,
    signing_key: Ed25519PrivateKey,
    key_id: str,
    encryption: BundleEncryption | None = None,
    signed_at: datetime | None = None,
    algorithm: Literal["Ed25519"] = "Ed25519",
) -> OfflineBundle:
    """Assemble and sign an :class:`OfflineBundle` batching ``envelopes``
    (already-signed E5-T03 ``NodeMessageEnvelope`` objects) for delivery to
    ``recipient_node_id``. See the module docstring for the full export
    design.

    Args:
        envelopes: the already-signed envelopes to batch, in the exact
            order they will appear in the bundle's ``entries``/``envelopes``
            lists. Never re-signed or otherwise modified by this function.
        bundle_id: this bundle's own identifier (e.g.
            ``mrr.domain.identity.new_urn("offline-bundle")`` — minting it
            is the caller's decision, not this function's, so the same
            call with the same ``bundle_id`` reproducibly yields the same
            bundle).
        bundle_nonce: this bundle's own replay nonce (``min_length=16``).
        sender_node_id: the sending node's own id.
        sender_practice_id: the sending node's own practice id — must equal
            ``key_id``'s practice for the resulting bundle's own
            ``_signer_practice_matches_sender`` contract check to pass.
        recipient_node_id: the single recipient node this bundle is
            addressed to.
        created_at: this bundle's own creation instant.
        expires_at: this bundle's own expiry instant (must be strictly
            after ``created_at`` — enforced by ``OfflineBundle``'s own
            contract-level validator).
        signing_key: the sending node's Ed25519 private key.
        key_id: the sending node's key id for ``signing_key``.
        encryption: structural encryption metadata (docs/spec/04 section
            4.1). Defaults to ``BundleEncryption(scheme="none")`` — an
            honest description of what this function actually does (no
            encryption) rather than a placeholder implying one exists.
        signed_at: the signature's own ``signed_at`` timestamp. Defaults to
            ``created_at`` (never ``datetime.now(UTC)``) so that calling
            this function twice with identical arguments reproducibly
            yields a byte-identical bundle (task-packets/E5-T06.yaml
            acceptance test: "round-trip determinism").
        algorithm: the signature algorithm. Defaults to ``"Ed25519"``, the
            only value ``mrr.contracts.common.Signature.algorithm`` accepts.

    Returns:
        The fully assembled, signed ``OfflineBundle``.
    """
    resolved_encryption = encryption if encryption is not None else BundleEncryption(scheme="none")
    resolved_signed_at = signed_at if signed_at is not None else created_at

    entries = [
        BundleEntry(
            message_id=envelope.message_id,
            payload_kind=envelope.payload_kind,
            envelope_content_hash=compute_content_hash(
                json.loads(envelope.model_dump_json(exclude_none=True))
            ),
        )
        for envelope in envelopes
    ]

    placeholder_signature = Signature(
        signer_practice_id=sender_practice_id,
        key_id=key_id,
        algorithm=algorithm,
        signed_at=resolved_signed_at,
        value=_PLACEHOLDER_SIGNATURE_VALUE,
    )
    draft = OfflineBundle(
        bundle_id=bundle_id,
        bundle_nonce=bundle_nonce,
        sender_node_id=sender_node_id,
        sender_practice_id=sender_practice_id,
        recipient_node_id=recipient_node_id,
        created_at=created_at,
        expires_at=expires_at,
        entries=entries,
        envelopes=list(envelopes),
        encryption=resolved_encryption,
        signature=placeholder_signature,
    )

    # ADR-0004: sign over the SAME exclude_none=True body a caller would
    # persist or transmit — never a second, null-including representation.
    # mrr.domain.hashing_policy.sign_object's prepare_for_signature strips
    # the entire "signature" field before signing, so the placeholder
    # value above never influences what gets signed (mirrors
    # mrr.services.node_runtime.evidence_crate.EvidenceCrateSealer.seal's
    # own draft-then-resign convention).
    body = json.loads(draft.model_dump_json(exclude_none=True))
    signature_value = sign_object(signing_key, body, algorithm=algorithm)
    signature = Signature(
        signer_practice_id=sender_practice_id,
        key_id=key_id,
        algorithm=algorithm,
        signed_at=resolved_signed_at,
        value=signature_value,
    )
    body["signature"] = signature.model_dump(mode="json")
    return OfflineBundle.model_validate(body)


def validate_inbound_bundle(
    bundle: OfflineBundle,
    *,
    this_node_id: str,
    trusted_sender_practice_id: str,
    ring: KeyRing,
    already_processed: BundleAlreadyProcessed,
    at: datetime | None = None,
) -> list[NodeMessageEnvelope]:
    """Validate ``bundle`` for inbound acceptance at ``this_node_id`` and
    return its carried envelopes, in order. See the module docstring for
    the full five-condition accept rule and its typed failure family.

    Args:
        bundle: the received, not-yet-trusted ``OfflineBundle``.
        this_node_id: this receiving node's own id — the bundle is
            accepted only if it is addressed here.
        trusted_sender_practice_id: the id of the practice the caller
            actually trusts as this bundle's sender (mirrors E5-T03's
            caller-supplied ``trusted_sender_practice_id`` — the trust
            decision about WHICH practice to trust is the caller's, not
            this function's).
        ring: ``trusted_sender_practice_id``'s own trusted ``KeyRing``
            (build one from a ``mrr.contracts.practice.Practice`` with
            ``mrr.domain.manifest_trust.practice_key_ring`` — reused
            unchanged).
        already_processed: a caller-supplied replay-check predicate over
            ``bundle_id``; see :data:`BundleAlreadyProcessed`.
        at: the evaluation instant for the validity-window and
            key-validity checks. Defaults to ``datetime.now(UTC)`` — the
            RECEIPT instant — and is caller-overridable for deterministic
            testing, mirroring ``validate_inbound_envelope``'s own ``at``
            parameter.

    Returns:
        ``list(bundle.envelopes)`` — the verified envelopes, in order —
        returned ONLY once every one of the five accept-rule conditions has
        held; never returned for any failing precondition. Each envelope
        still requires its OWN ``validate_inbound_envelope`` call before
        its payload is acted on (see the module docstring's "Reuse, not
        reimplementation" section).

    Raises:
        mrr.domain.exceptions.BundleRecipientMismatchError: condition 1
            fails — ``bundle.recipient_node_id`` is not ``this_node_id``.
        mrr.domain.exceptions.BundleNotWithinValidityWindowError: condition
            2 fails — the evaluation instant is before ``created_at`` or
            at/after ``expires_at``.
        mrr.domain.exceptions.BundleAlreadyProcessedError: condition 3
            fails — ``already_processed(bundle.bundle_id)`` is ``True``.
        mrr.domain.exceptions.BundleSignerMismatchError: condition 4 fails
            — the bundle's claimed signer is not
            ``trusted_sender_practice_id``.
        mrr.domain.exceptions.UnknownKeyIdError: condition 4 fails — the
            bundle's claimed key id does not resolve in ``ring`` at all.
        mrr.domain.exceptions.BundleKeyNotValidError: condition 4 fails —
            the resolved descriptor is not valid at the evaluation instant.
        mrr.crypto.exceptions.SignatureVerificationError: condition 4
            fails — the Ed25519 signature does not verify (bad or
            tampered signature, including a substituted signing key, or a
            batch tampered by adding/dropping/reordering/retargeting an
            entry) under the resolved key.
        mrr.crypto.exceptions.UnsupportedAlgorithmError: condition 4 fails
            — ``bundle.signature.algorithm`` is not ``"Ed25519"``.
        mrr.domain.exceptions.BundleEntryHashMismatchError: condition 5
            fails — some entry's declared ``envelope_content_hash`` does
            not equal the actual content hash of its carried envelope.
    """
    if bundle.recipient_node_id != this_node_id:
        raise BundleRecipientMismatchError(bundle.bundle_id, bundle.recipient_node_id, this_node_id)

    evaluation_instant = at if at is not None else datetime.now(UTC)
    if not (bundle.created_at <= evaluation_instant < bundle.expires_at):
        raise BundleNotWithinValidityWindowError(
            bundle.bundle_id, bundle.created_at, bundle.expires_at, evaluation_instant
        )

    if already_processed(bundle.bundle_id):
        raise BundleAlreadyProcessedError(bundle.bundle_id)

    if bundle.signature.signer_practice_id != trusted_sender_practice_id:
        raise BundleSignerMismatchError(
            claimed_signer_practice_id=bundle.signature.signer_practice_id,
            trusted_practice_id=trusted_sender_practice_id,
        )

    kid = bundle.signature.key_id
    descriptor = ring.get(kid)
    if descriptor is None:
        raise UnknownKeyIdError(kid)

    if not ring.is_valid_at(evaluation_instant, kid):
        raise BundleKeyNotValidError(kid, at=evaluation_instant)

    verifying_key = decode_public_key(descriptor.encoded_public_key)
    verify_object_signature(
        verifying_key,
        json.loads(bundle.model_dump_json(exclude_none=True)),
        bundle.signature.value,
        algorithm=bundle.signature.algorithm,
    )

    for entry, envelope in zip(bundle.entries, bundle.envelopes, strict=True):
        actual_hash = compute_content_hash(json.loads(envelope.model_dump_json(exclude_none=True)))
        if entry.envelope_content_hash != actual_hash:
            raise BundleEntryHashMismatchError(
                bundle.bundle_id, entry.message_id, entry.envelope_content_hash, actual_hash
            )

    return list(bundle.envelopes)


#: docs/spec/04_SECURITY_AND_POLICY.md section 8.3's "a coarse reason code",
#: mirroring ``mrr.domain.envelope_validation.EnvelopeRejectionReason``.
#: Each member names exactly one of ``validate_inbound_bundle``'s five
#: fail-closed conditions; the SPECIFIC reason stays on the raised typed
#: exception and is never meant to be persisted/echoed to the sender — only
#: this coarse category is.
BundleRejectionReason = Literal[
    "wrong_recipient",
    "outside_validity_window",
    "already_processed",
    "signer_mismatch",
    "unknown_key",
    "key_not_valid",
    "signature_invalid",
    "entry_hash_mismatch",
]

#: Maps each of ``validate_inbound_bundle``'s eight distinct typed failure
#: exceptions to its coarse ``BundleRejectionReason`` — mirrors
#: ``mrr.domain.envelope_validation._REJECTION_REASON_BY_ERROR_TYPE``'s
#: identical structure and its identical choice to map both
#: ``SignatureVerificationError``/``UnsupportedAlgorithmError`` to
#: ``"signature_invalid"``.
_BUNDLE_REJECTION_REASON_BY_ERROR_TYPE: dict[type[Exception], BundleRejectionReason] = {
    BundleRecipientMismatchError: "wrong_recipient",
    BundleNotWithinValidityWindowError: "outside_validity_window",
    BundleAlreadyProcessedError: "already_processed",
    BundleSignerMismatchError: "signer_mismatch",
    UnknownKeyIdError: "unknown_key",
    BundleKeyNotValidError: "key_not_valid",
    SignatureVerificationError: "signature_invalid",
    UnsupportedAlgorithmError: "signature_invalid",
    BundleEntryHashMismatchError: "entry_hash_mismatch",
}

#: The exact exception types ``validate_inbound_bundle`` can raise — the
#: tuple form a caller's ``except`` needs, kept as the single source of
#: truth alongside ``_BUNDLE_REJECTION_REASON_BY_ERROR_TYPE`` so the two can
#: never drift apart (mirrors ``mrr.domain.envelope_validation.
#: ENVELOPE_VALIDATION_FAILURES``'s identical precedent).
BUNDLE_VALIDATION_FAILURES: tuple[type[Exception], ...] = tuple(
    _BUNDLE_REJECTION_REASON_BY_ERROR_TYPE.keys()
)


def coarse_bundle_rejection_reason(exc: Exception) -> BundleRejectionReason:
    """Translate one of ``validate_inbound_bundle``'s typed failures to its
    coarse :data:`BundleRejectionReason` (docs/spec/04_SECURITY_AND_POLICY.md
    section 8.3: "a coarse reason code" — detail stays on the typed
    exception; only this coarse category is meant to be persisted/echoed to
    the sender).

    Raises:
        KeyError: if ``exc`` is not one of ``validate_inbound_bundle``'s own
            typed failures — never silently mapped to a made-up category.
    """
    return _BUNDLE_REJECTION_REASON_BY_ERROR_TYPE[type(exc)]
