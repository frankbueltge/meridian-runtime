"""Mirrors schemas/node-message-envelope.schema.json
(docs/spec/04_SECURITY_AND_POLICY.md section 8.2, the "envelope": "Task and
result envelopes include nonces, expiry, recipient identity, content hashes,
and signatures"). Seventeenth schema/model pair in this repository; third
task of Epic E5 (task-packets/E5-T03.yaml).

``NodeMessageEnvelope`` is the online, point-to-point transport wrapper
around ONE already-signed application payload
(docs/spec/01_SYSTEM_SPEC.md section 2.2: "Task inbox and decision
interface", "Signed result outbox"). Deliberately NOT a ``BaseObject``
subclass: it carries no ``practice_id``/``revision``/``created_at``/
``created_by``/``content_hash`` of its own — it is a transport message
describing one delivery attempt, not a first-class, revisioned MRR domain
object (docs/spec/02_DOMAIN_MODEL.md section 1's eight required base
fields do not apply here). It is still registered exactly like every other
entity (schemas/, this model + its ``mrr.contracts`` export, examples/, and
``scripts/check_contracts.py``) per task-packets/E5-T03.yaml
derived_decisions.

--- Payload-agnostic by construction ----------------------------------------

``payload`` is an open JSON object — whatever a signed
``mrr.contracts.task_bundle.TaskBundle`` (E5-T04) or
``mrr.contracts.evidence_crate.EvidenceCrate`` (E5-T05) dumps to via
``model_dump_json(exclude_none=True)`` — referenced only by
``payload_kind`` (a free-form, non-empty tag, mirroring
``mrr.contracts.common.BaseObject.kind``'s own "a string identifying the
carried shape" convention) and ``payload_content_hash`` (the payload's own
``content_hash``). This model does not itself recompute or compare that
hash — binding the two is
``mrr.domain.envelope_validation.validate_inbound_envelope``'s job (the
inbound validator, task-packets/E5-T03.yaml). Nothing here special-cases a
task versus a result payload, or imports either contract module.

--- Transport signature reuses the EXISTING Signature/hashing_policy -------

``signature`` is the SENDING node's transport signature over this
envelope's own ADR-0004 ``exclude_none=True`` canonical form, built and
verified via the EXISTING ``mrr.domain.hashing_policy``
(``sign_object``/``verify_object_signature``) — never a second signing
recipe (task-packets/E5-T03.yaml forbidden_changes: "reuse the Signature
type ... and hashing_policy unchanged"). ``_signer_practice_matches_sender``
below enforces that the embedded ``Signature.signer_practice_id`` (the
cryptographic signer claim) agrees with this envelope's own explicit
``sender_practice_id`` field — the same "self shape must match its
signature's own claim" consistency check
``mrr.contracts.practice.Practice._signature_signer_is_this_practice_with_a_listed_key``
already establishes as this codebase's precedent, adapted here for an
externally-addressed envelope rather than a self-signed identity: the
envelope's SENDER (not the envelope itself) must be who the signature
claims signed it.

``_sent_at_before_expires_at`` is a plain structural sanity check —
mirroring ``mrr.contracts.practice.PublicKeyDescriptor``'s own
``valid_from < valid_until`` ordering check — so a self-inconsistent
envelope (whose declared validity window could never contain any evaluation
instant) is rejected at construction time, before it ever reaches
``mrr.domain.envelope_validation.validate_inbound_envelope``'s own
``sent_at <= now < expires_at`` check against a real clock reading.
"""

from __future__ import annotations

from typing import Any, Self

from mrr.contracts.common import MRRModel, Sha256, Signature, Urn
from pydantic import AwareDatetime, Field, model_validator

__all__ = ["NodeMessageEnvelope"]


class NodeMessageEnvelope(MRRModel):
    """Mirrors schemas/node-message-envelope.schema.json. Every property is
    required — there are no optional fields on this entity.
    """

    message_id: Urn
    sender_node_id: Urn
    sender_practice_id: Urn
    recipient_node_id: Urn
    sent_at: AwareDatetime
    expires_at: AwareDatetime
    payload_kind: str = Field(min_length=1)
    payload_content_hash: Sha256
    payload: dict[str, Any]
    signature: Signature

    @model_validator(mode="after")
    def _sent_at_before_expires_at(self) -> Self:
        if not self.sent_at < self.expires_at:
            raise ValueError(
                f"sent_at ({self.sent_at.isoformat()}) must be strictly before "
                f"expires_at ({self.expires_at.isoformat()})"
            )
        return self

    @model_validator(mode="after")
    def _signer_practice_matches_sender(self) -> Self:
        if self.signature.signer_practice_id != self.sender_practice_id:
            raise ValueError(
                "signature.signer_practice_id must equal this envelope's own "
                f"sender_practice_id ({self.sender_practice_id!r}); got "
                f"{self.signature.signer_practice_id!r}"
            )
        return self
