"""Mirrors schemas/correction-notification.schema.json (docs/spec/01_SYSTEM_
SPEC.md section 4.10, MRR-FR-094: "Every affected practice MUST receive a
signed notification or a durable pending-delivery record"; docs/spec/02_
DOMAIN_MODEL.md section 2.16, CorrectionEvent). Twenty-second schema/model
pair in this repository; third task of Epic E6 (task-packets/E6-T03.yaml).

``CorrectionNotification`` carries ONE cross-practice correction notice from
the notifying practice to ONE recipient practice — a reference to an
already-recorded ``mrr.contracts.correction_event.CorrectionEvent`` revision
plus the ``notified_object_ids`` relevant to that specific recipient
(caller-resolved; this task builds no ``TransferContract.
correction_subscription`` resolver — see task-packets/E6-T03.yaml
forbidden_changes), self-contained with ``correction_type``/``severity``/
``reason``/``requested_action``/``replacement_object_id`` copied in from the
originating ``CorrectionEvent`` so the recipient never has to cross-practice
-fetch it to act on the notification.

Deliberately NOT a ``BaseObject`` subclass, for the identical reason
``mrr.contracts.node_message_envelope.NodeMessageEnvelope`` and
``mrr.contracts.offline_bundle.OfflineBundle`` are not one (see those
modules' own docstrings): a notification is "one delivery attempt's worth of
information," not a first-class, revisioned MRR domain object — no
``revision``/``supersedes``/``labels``, no ``api_version``/``kind`` BaseObject
scaffolding.

--- Why this model still needs its OWN ``content_hash`` -------------------

Unlike ``NodeMessageEnvelope``, which never carries a ``content_hash`` of its
own, this model MUST carry one: the EXISTING, unmodified
``mrr.domain.envelope_validation.validate_inbound_envelope``'s condition 3
does a literal ``envelope.payload.get("content_hash") ==
envelope.payload_content_hash`` dict-key lookup against whatever payload is
nested inside the wrapping envelope, with no special-casing per
``payload_kind``. For a ``CorrectionNotification`` to pass that unmodified
check when carried as an envelope's ``payload``, it MUST literally carry a
``content_hash`` key (task-packets/E6-T03.yaml derived_decisions (a)) — a
hard interoperability requirement of reusing E5-T03's validator verbatim, not
a stylistic echo of ``BaseObject``.

--- Own identifier/nonce/validity namespace, independent of the envelope ---

``notification_id``/``nonce``/``sent_at``/``expires_at`` are a SECOND,
independent identifier/replay/validity space from the wrapping envelope's own
``message_id``/``sent_at``/``expires_at`` — mirroring the already-established
"a bundle id and a message id are different identifier spaces" precedent from
``mrr.domain.offline_bundle``'s own module docstring (there, for
``OfflineBundle`` vs. the envelopes it carries), applied one layer further in
(task-packets/E6-T03.yaml derived_decisions (b)): a ``CorrectionNotification``
might, in a later task (E6-T06), be re-carried inside a freshly-built
``OfflineBundle``/``NodeMessageEnvelope`` pair after an earlier online
attempt failed, and its OWN replay/validity state must not depend on which
specific envelope most recently wrapped it.

--- Signature: the NOTIFYING practice, not the transport sender -----------

``signature`` is the NOTIFYING practice's own signature over this
notification's ADR-0004 ``exclude_none=True`` canonical form (built/verified
via the UNCHANGED ``mrr.domain.hashing_policy``, never a second signing
recipe) — verified by the new, fifth trust resolver,
``mrr.domain.correction_notification.resolve_trusted_correction_notification_key``
(a fourth-condition, fail-closed clone of
``mrr.domain.task_trust.resolve_trusted_task_key``), independent of whatever
node/practice transport-signs the wrapping ``NodeMessageEnvelope``.
``_signer_practice_matches_notifying_practice`` mirrors
``NodeMessageEnvelope``'s/``OfflineBundle``'s own identically-shaped
``_signer_practice_matches_sender`` check: the signature's own
``signer_practice_id`` claim must agree with this notification's own explicit
``notifying_practice_id`` field.

--- No raw or participant-identifiable content (MRR-NFR-006) ---------------

Only ids, hashes, ``correction_type``, ``severity``, ``reason``,
``requested_action``, and an optional ``replacement_object_id`` cross the
practice boundary here — mirroring ``CorrectionEvent``'s own no-raw-data
shape. No raw restricted or ``PARTICIPANT_IDENTIFIABLE`` object content is
carried.
"""

from __future__ import annotations

from typing import Self

from mrr.contracts.common import MRRModel, Sha256, Signature, Urn
from mrr.contracts.correction_event import CorrectionSeverity, CorrectionType
from pydantic import AwareDatetime, Field, model_validator

__all__ = ["CorrectionNotification"]


class CorrectionNotification(MRRModel):
    """Mirrors schemas/correction-notification.schema.json. Every property is
    required except ``replacement_object_id`` (explicitly nullable and
    absent from the schema's own ``required`` list, mirroring
    ``mrr.contracts.correction_event.CorrectionEvent.replacement_object_id``).
    """

    notification_id: Urn
    correction_id: Urn
    correction_revision: int = Field(ge=1)
    notifying_practice_id: Urn
    recipient_practice_id: Urn
    notified_object_ids: list[Urn] = Field(min_length=1)
    correction_type: CorrectionType
    severity: CorrectionSeverity
    reason: str = Field(min_length=1)
    requested_action: str
    replacement_object_id: Urn | None = None
    content_hash: Sha256
    nonce: str = Field(min_length=16)
    sent_at: AwareDatetime
    expires_at: AwareDatetime
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
    def _signer_practice_matches_notifying_practice(self) -> Self:
        if self.signature.signer_practice_id != self.notifying_practice_id:
            raise ValueError(
                "signature.signer_practice_id must equal this notification's own "
                f"notifying_practice_id ({self.notifying_practice_id!r}); got "
                f"{self.signature.signer_practice_id!r}"
            )
        return self
