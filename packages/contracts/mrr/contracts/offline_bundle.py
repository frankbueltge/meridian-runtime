"""Mirrors schemas/offline-bundle.schema.json (docs/spec/01_SYSTEM_SPEC.md
section 8.4, "Federated offline"; docs/spec/03_API_AND_EVENTS.md section 4.2,
"Store-and-forward envelopes"). Eighteenth schema/model pair in this
repository; sixth task of Epic E5 (task-packets/E5-T06.yaml).

``OfflineBundle`` is the OFFLINE counterpart of E5-T03's online
``NodeMessageEnvelope``: instead of delivering ONE already-signed envelope
point-to-point, a bundle batches MANY already-signed
``mrr.contracts.node_message_envelope.NodeMessageEnvelope`` objects destined
for ONE recipient node into a single, content-addressed, sender-signed
container suitable for air-gapped/store-and-forward transfer. Deliberately
NOT a ``BaseObject`` subclass, for the identical reason
``NodeMessageEnvelope`` is not one (see that module's own docstring): a
bundle is a transport container describing one delivery batch, not a
first-class, revisioned MRR domain object.

--- Carries E5-T03 envelopes UNCHANGED, wraps rather than re-models --------

``envelopes`` is a plain ``list[NodeMessageEnvelope]`` — the exact E5-T03
contract, reused verbatim (task-packets/E5-T06.yaml forbidden_changes: "the
E5-T03 NodeMessageEnvelope contract ... reuse UNCHANGED"). This module does
not re-sign, re-model, or otherwise touch an individual envelope's own
shape or signature.

--- Entries: an ordered, content-addressed index over ``envelopes`` --------

``entries`` is an ORDERED list of ``BundleEntry`` — ``message_id`` +
``payload_kind`` + ``envelope_content_hash`` — one per carried envelope, in
the SAME order. ``envelope_content_hash`` is the carried envelope's own
content hash, computed via the UNCHANGED
``mrr.domain.hashing_policy.compute_content_hash`` over the envelope's own
ADR-0004 ``exclude_none=True`` canonical form (excluding the envelope's own
``signature`` field, mirroring how a ``BaseObject``'s own ``content_hash``
excludes ``signature`` — see ``mrr.domain.hashing_policy``'s module
docstring). Building this hash correctly is
``mrr.domain.offline_bundle.build_outbox_bundle``'s job; checking it against
the ACTUAL carried envelope's hash is
``mrr.domain.offline_bundle.validate_inbound_bundle``'s job — this contract
only carries the value, it does not compute or verify it.

``_entries_correspond_to_envelopes`` is a plain structural sanity check —
mirroring ``NodeMessageEnvelope``'s own ``_sent_at_before_expires_at``/
``_signer_practice_matches_sender`` precedent — so a self-inconsistent
bundle (an ``entries`` list that could not possibly describe ``envelopes``
in order) is rejected at construction time, before it ever reaches
``validate_inbound_bundle``. It does not, and cannot, check
``envelope_content_hash`` correctness itself (that requires the hashing
policy, a domain-layer concern, not a contract-layer one) — only that the
two lists have the same length and that each entry's own ``message_id``
names the envelope at its own position.

--- Bundle-level signature: the SENDER authenticates the WHOLE batch -------

``signature`` is the SENDING node's signature over this bundle's own
ADR-0004 ``exclude_none=True`` canonical form (built/verified via the
UNCHANGED ``mrr.domain.hashing_policy``, never a second signing recipe) —
covering ``entries``, ``envelopes``, ``recipient_node_id``,
``created_at``/``expires_at``, and every other field at once. This is what
authenticates the batch's own membership, order, recipient, and validity
window as a whole (task-packets/E5-T06.yaml derived_decisions): an attacker
cannot add, drop, reorder, or retarget an entry (or its carried envelope)
without breaking this signature. ``_signer_practice_matches_sender`` mirrors
``NodeMessageEnvelope``'s own identically-named check: the signature's own
``signer_practice_id`` claim must agree with this bundle's own explicit
``sender_practice_id`` field.

--- Encryption metadata is STRUCTURAL only — no scheme is implemented ------

``encryption`` reserves a place in the bundle's shape for docs/spec/04
section 4.1's "Envelope encryption for cross-practice offline bundles"
without implementing any encryption or decryption here (task-packets/
E5-T06.yaml forbidden_changes: "real envelope encryption / KMS / decryption
... carry encryption metadata as a STRUCTURAL field only"). ``scheme`` names
whichever scheme (if any) protects ``envelopes``' bytes for this bundle;
``"none"`` is the only value this codebase's own ``build_outbox_bundle``
ever produces today — an honest description of what it actually does
(nothing), not a placeholder awaiting silent removal.
"""

from __future__ import annotations

from typing import Self

from mrr.contracts.common import MRRModel, Sha256, Signature, Urn
from mrr.contracts.node_message_envelope import NodeMessageEnvelope
from pydantic import AwareDatetime, Field, model_validator

__all__ = ["BundleEncryption", "BundleEntry", "OfflineBundle"]


class BundleEntry(MRRModel):
    """Mirrors one `entries[]` item. All three properties are required."""

    message_id: Urn
    payload_kind: str = Field(min_length=1)
    envelope_content_hash: Sha256


class BundleEncryption(MRRModel):
    """Mirrors the `encryption` object — STRUCTURAL metadata only; see the
    module docstring's "Encryption metadata is STRUCTURAL only" section.
    `recipient_key_id` is the only property absent from the schema's
    `required: ["scheme"]` list.
    """

    scheme: str = Field(min_length=1)
    recipient_key_id: str | None = None


class OfflineBundle(MRRModel):
    """Mirrors schemas/offline-bundle.schema.json. Every property is
    required except ``encryption.recipient_key_id`` (see
    ``BundleEncryption``).
    """

    bundle_id: Urn
    bundle_nonce: str = Field(min_length=16)
    sender_node_id: Urn
    sender_practice_id: Urn
    recipient_node_id: Urn
    created_at: AwareDatetime
    expires_at: AwareDatetime
    entries: list[BundleEntry] = Field(min_length=1)
    envelopes: list[NodeMessageEnvelope] = Field(min_length=1)
    encryption: BundleEncryption
    signature: Signature

    @model_validator(mode="after")
    def _created_at_before_expires_at(self) -> Self:
        if not self.created_at < self.expires_at:
            raise ValueError(
                f"created_at ({self.created_at.isoformat()}) must be strictly before "
                f"expires_at ({self.expires_at.isoformat()})"
            )
        return self

    @model_validator(mode="after")
    def _signer_practice_matches_sender(self) -> Self:
        if self.signature.signer_practice_id != self.sender_practice_id:
            raise ValueError(
                "signature.signer_practice_id must equal this bundle's own "
                f"sender_practice_id ({self.sender_practice_id!r}); got "
                f"{self.signature.signer_practice_id!r}"
            )
        return self

    @model_validator(mode="after")
    def _entries_correspond_to_envelopes(self) -> Self:
        if len(self.entries) != len(self.envelopes):
            raise ValueError(
                f"entries has {len(self.entries)} item(s) but envelopes has "
                f"{len(self.envelopes)} — the two lists must correspond 1:1, in order"
            )
        for index, (entry, envelope) in enumerate(zip(self.entries, self.envelopes, strict=True)):
            if entry.message_id != envelope.message_id:
                raise ValueError(
                    f"entries[{index}].message_id ({entry.message_id!r}) does not equal "
                    f"envelopes[{index}].message_id ({envelope.message_id!r}) — entries and "
                    "envelopes must correspond 1:1, in order"
                )
        return self
