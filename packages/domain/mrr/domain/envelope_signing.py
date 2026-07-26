"""Pure, payload-agnostic construction of a signed ``NodeMessageEnvelope``
from an arbitrary, already-content-hashed payload — task-packets/E5-T10.yaml,
deliberately built as the envelope-shaped SIGNING twin of
``mrr.domain.envelope_validation``'s own VALIDATING side: this module
mirrors that module's construction, docstring density, and discipline
closely on purpose.

--- The gap this closes ------------------------------------------------

Before this packet, the only place in the whole repository that turned a
payload into a signed ``NodeMessageEnvelope`` was
``services/control_plane/mrr/services/correction/service.py``'s PRIVATE
``CorrectionImpactService._build_and_sign_envelope`` — bound to a single
payload kind (``CorrectionNotification``) and reachable only from inside
that one service, never from a CLI (docs/design/2026-07-26-e5-t10-
derivation-envelope-kante.md's fact-lock: "the federation can today carry
exactly one payload type, and only from inside a service"). Every other
kind of payload — a dissent, a question, a result — had a documented
``content_hash`` and nowhere to go.

:func:`build_signed_envelope` below lifts that private method's own SIGNING
PROCEDURE verbatim (see "The signing procedure, lifted verbatim" further
down) and generalizes only the three bindings that were correction-specific:
``payload_kind`` and ``payload`` become ordinary parameters, and
``payload_content_hash`` is READ from the payload itself rather than from a
``CorrectionNotification.content_hash`` attribute. The correction service
itself is UNCHANGED (task-packets/E5-T10.yaml forbidden_changes) — it keeps
its own working, tested path; the resulting duplication between that private
method and this public function is named here, not hidden, and a DRY
consolidation (if ever wanted) is its own future packet, exactly as
task-packets/E9-T00b.yaml did for an unrelated prior duplication.

--- The one hard rule: refuse what the receiver would reject ------------

``mrr.domain.envelope_validation.validate_inbound_envelope``'s condition 3
(see that module's own docstring, verbatim) treats ``envelope.
payload_content_hash`` as a CONSISTENCY check against the payload's own
carried ``content_hash`` field — explicitly NOT an independent
recomputation from payload bytes, because that layer is payload-agnostic
and has no opinion on any specific payload kind's own hashing policy. That
fact fixes, without any remaining choice, how THIS module obtains
``payload_content_hash``:

- it is READ from ``payload["content_hash"]`` — never recomputed (a fresh
  computation could disagree with what the payload itself already carries,
  which is exactly what a receiver's condition 3 compares against) and
  never accepted as a free caller parameter (that would let a caller build
  an envelope whose declared hash disagrees with its own carried payload,
  which condition 3 is then guaranteed to reject);
- a payload with no ``content_hash`` field at all (or an explicit ``None``)
  could never satisfy condition 3 no matter what ``payload_content_hash``
  were set to, so :func:`build_signed_envelope` refuses to build one at
  all — :class:`mrr.domain.exceptions.EnvelopePayloadMissingContentHashError`,
  raised BEFORE any cryptographic operation runs. The sender refuses, at
  build time, exactly what the receiver would refuse to accept at
  validation time — the same rule applied on both sides of the exchange,
  once as a build precondition, once as an accept precondition.

--- Payload-agnostic by construction, exactly like its validating twin ---

No import of ``mrr.contracts.correction_notification.CorrectionNotification``
or any other specific payload contract anywhere in this module, and no
closed set of allowed ``payload_kind`` values. ``payload_kind`` is only ever
a caller-supplied, non-empty string (``NodeMessageEnvelope.payload_kind``'s
own ``Field(min_length=1)`` is the sole constraint this module relies on),
and ``payload`` is only ever a caller-supplied ``dict[str, Any]`` this
module reads exactly one key from (``"content_hash"``) and otherwise
carries verbatim into the envelope's own ``payload`` field.

--- The domain -> contracts import is not a new cycle --------------------

Importing ``mrr.contracts.node_message_envelope.NodeMessageEnvelope`` from
``mrr.domain`` is precedented, not novel: ``mrr.domain.envelope_validation``
(line 104 as of this packet) already does exactly this, for the identical
reason — a domain module operating on a contracts-layer entity. The
import-linter contract "Nothing inward imports the services layer" (E2-T01/
E2-T04) is unaffected either way: this module imports only ``mrr.contracts``
and ``mrr.crypto``/``mrr.domain`` modules, the same layers its validating
twin already imports.

--- Determinism: no clock, no randomness anywhere here --------------------

Every identity, timestamp, and the signing key itself is caller-supplied —
``message_id`` included. Calling :func:`build_signed_envelope` twice with
identical arguments yields a byte-identical ``NodeMessageEnvelope``: there
is no ``datetime.now(UTC)``, no ``secrets``/``uuid`` call anywhere in this
module, mirroring ``mrr.domain.offline_bundle.build_outbox_bundle``'s own
"every identifying/temporal field is CALLER-supplied" discipline.

--- The signing procedure, lifted verbatim --------------------------------

1. Refuse if ``payload`` carries no own ``content_hash``
   (:class:`mrr.domain.exceptions.EnvelopePayloadMissingContentHashError`)
   — checked FIRST, before any cryptography, generalizing
   ``_build_and_sign_envelope``'s own implicit precondition ("the
   notification's own content_hash", there guaranteed by
   ``CorrectionNotification``'s contract; here checked explicitly, because
   an arbitrary payload dict carries no such guarantee).
2. Build a DRAFT ``NodeMessageEnvelope`` with a placeholder transport
   signature (``mrr.contracts.common.Signature`` with a dummy ``value``
   long enough to satisfy that field's own ``min_length=40`` — mirrors
   ``mrr.domain.offline_bundle``'s own ``_PLACEHOLDER_SIGNATURE_VALUE``
   convention, redefined locally rather than imported since that name is
   private to its own module).
3. ``json.loads(draft.model_dump_json(exclude_none=True))`` — the exact
   ADR-0004 canonical body ``mrr.domain.hashing_policy.sign_object`` signs
   over, the SAME body a receiver's ``validate_inbound_envelope`` verifies
   against (``envelope.model_dump_json(exclude_none=True)``, that
   function's own condition 5).
4. ``sign_object(signing_key, envelope_body)`` — the UNCHANGED
   ``mrr.domain.hashing_policy`` function, never a second signing recipe.
5. Replace the placeholder signature's ``value`` with the real one and run
   ``NodeMessageEnvelope.model_validate`` on the result once more — proving
   the SIGNED envelope still satisfies every contract-level invariant
   (``_sent_at_before_expires_at``, ``_signer_practice_matches_sender``)
   with its real signature in place, not merely its placeholder draft.

No step here is invented: every one mirrors
``CorrectionImpactService._build_and_sign_envelope``
(services/control_plane/mrr/services/correction/service.py, around line
2105) line for line, generalized only at the three call sites named above.

--- What this module deliberately does NOT do -----------------------------

No persistence, no I/O, no network — a pure function over already-in-memory
values, CI-testable with no database. It does not decide whether a payload
is well-formed BEYOND carrying its own ``content_hash``: any other
malformed field (an invalid URN, ``sent_at`` not strictly before
``expires_at``, a ``payload_content_hash`` that is present but not a
well-formed ``sha256:<hex>`` string, ...) is left to ``NodeMessageEnvelope``'s
own contract-level ``pydantic.ValidationError`` to reject, unwrapped — this
module does not soften, catch, or reinterpret any of those checks. It does
not mint an identity, a key, or a practice for anyone (task-packets/
E5-T10.yaml explicitly_not: "no Practice, no key, no node id minted for
Meridian and certainly none for Ulysses") — every one of those is a
caller-supplied argument. And it does not perform an exchange: no
``mrr.domain.offline_bundle.build_outbox_bundle`` call, no transport, no
trust decision about who receives the resulting envelope live here at all —
this module only builds and signs the one artifact that edge was missing.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.contracts.common import Signature
from mrr.contracts.node_message_envelope import NodeMessageEnvelope
from mrr.domain.exceptions import EnvelopePayloadMissingContentHashError
from mrr.domain.hashing_policy import sign_object

__all__ = ["build_signed_envelope"]

#: Placeholder ``Signature.value`` used only while assembling the draft
#: envelope below, before the real signature is computed. Never the value
#: actually signed over or returned: ``mrr.domain.hashing_policy.
#: prepare_for_signature`` strips the entire ``signature`` field before
#: signing, so this placeholder can never leak into what gets hashed or
#: signed. Mirrors ``mrr.domain.offline_bundle``'s/
#: ``mrr.services.node_runtime.evidence_crate``'s own identical
#: ``_PLACEHOLDER_SIGNATURE_VALUE`` convention (``min_length=40`` on
#: ``mrr.contracts.common.Signature.value``), redefined locally rather than
#: imported since that name is private to each of those modules.
_PLACEHOLDER_SIGNATURE_VALUE = "0" * 44


def build_signed_envelope(
    payload: dict[str, Any],
    *,
    payload_kind: str,
    message_id: str,
    sender_node_id: str,
    sender_practice_id: str,
    recipient_node_id: str,
    sent_at: datetime,
    expires_at: datetime,
    signing_key: Ed25519PrivateKey,
    key_id: str,
    algorithm: Literal["Ed25519"] = "Ed25519",
) -> NodeMessageEnvelope:
    """Build and sign a ``NodeMessageEnvelope`` carrying ``payload``. See
    the module docstring for the full design and "The signing procedure,
    lifted verbatim" from
    ``CorrectionImpactService._build_and_sign_envelope``.

    Args:
        payload: the payload body to carry, verbatim, in the envelope's own
            ``payload`` field. MUST carry its own ``content_hash`` (see
            Raises below) — never mutated by this function.
        payload_kind: a free-form, non-empty tag identifying the carried
            payload's shape (``NodeMessageEnvelope.payload_kind``'s own
            sole constraint is ``min_length=1`` — no closed set of allowed
            values is enforced or assumed here).
        message_id: this envelope's own identifier. Caller-supplied, like
            every other identity below — never minted here (see the module
            docstring's determinism section).
        sender_node_id: the sending node's own id.
        sender_practice_id: the sending node's own practice id — becomes
            both the envelope's own ``sender_practice_id`` field and the
            transport signature's ``signer_practice_id``, so the resulting
            envelope satisfies its own contract-level
            ``_signer_practice_matches_sender`` check.
        recipient_node_id: the single recipient node this envelope is
            addressed to.
        sent_at: this envelope's own send instant. Also used as the
            transport signature's own ``signed_at`` — mirrors
            ``_build_and_sign_envelope``'s identical choice; no separate
            ``signed_at`` parameter exists here.
        expires_at: this envelope's own expiry instant (must be strictly
            after ``sent_at`` — enforced by ``NodeMessageEnvelope``'s own
            contract-level ``_sent_at_before_expires_at`` validator).
        signing_key: the sending node's Ed25519 private key.
        key_id: the sending node's key id for ``signing_key``.
        algorithm: the signature algorithm. Defaults to ``"Ed25519"``, the
            only value ``mrr.contracts.common.Signature.algorithm`` accepts.

    Returns:
        The fully assembled, signed ``NodeMessageEnvelope``.

    Raises:
        mrr.domain.exceptions.EnvelopePayloadMissingContentHashError:
            ``payload`` carries no own ``content_hash`` (absent, or
            explicitly ``None``) — checked FIRST, before any cryptographic
            operation, so refusal is fast and no key ever touches an
            envelope that could not pass a receiver's own condition 3.
        pydantic.ValidationError: propagated, unwrapped, from
            ``NodeMessageEnvelope``'s own contract-level validation — e.g.
            ``payload_content_hash`` is present but not a well-formed
            ``sha256:<hex>`` string, an identity is not a well-formed URN,
            or ``sent_at`` is not strictly before ``expires_at``. This
            function does not soften, catch, or reinterpret any of those
            checks.
    """
    payload_content_hash = payload.get("content_hash")
    if payload_content_hash is None:
        raise EnvelopePayloadMissingContentHashError(payload_kind)

    placeholder_signature = Signature(
        signer_practice_id=sender_practice_id,
        key_id=key_id,
        algorithm=algorithm,
        signed_at=sent_at,
        value=_PLACEHOLDER_SIGNATURE_VALUE,
    )
    draft = NodeMessageEnvelope(
        message_id=message_id,
        sender_node_id=sender_node_id,
        sender_practice_id=sender_practice_id,
        recipient_node_id=recipient_node_id,
        sent_at=sent_at,
        expires_at=expires_at,
        payload_kind=payload_kind,
        payload_content_hash=payload_content_hash,
        payload=payload,
        signature=placeholder_signature,
    )

    # ADR-0004: hash and sign over the SAME exclude_none=True body a caller
    # would persist or transmit — never a second, null-including
    # representation. The same body a receiver's validate_inbound_envelope
    # verifies against (its own condition 5).
    envelope_body: dict[str, Any] = json.loads(draft.model_dump_json(exclude_none=True))
    signature_value = sign_object(signing_key, envelope_body)
    signature = Signature(
        signer_practice_id=sender_practice_id,
        key_id=key_id,
        algorithm=algorithm,
        signed_at=sent_at,
        value=signature_value,
    )
    envelope_body["signature"] = signature.model_dump(mode="json")
    return NodeMessageEnvelope.model_validate(envelope_body)
