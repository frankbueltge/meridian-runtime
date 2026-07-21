"""Mirrors schemas/correction-response.schema.json (docs/spec/01_SYSTEM_SPEC.md
section 4.9, MRR-FR-084: "A receiving practice MAY reject a correction, but
MUST record that it was notified and why it rejected or deferred it";
docs/spec/06_IMPLEMENTATION_PLAN.md's own task title, "E6-T04 local
accept/adapt/reject/defer response"). Twenty-third schema/model pair in this
repository (assuming TransferContract/Obligation/CorrectionNotification land
first as the 19th/20th/21st/22nd per their own packets' ordinal claims;
fourth task of Epic E6, task-packets/E6-T04.yaml).

``CorrectionResponse`` is the RECEIVING practice's own, LOCAL-ONLY recording
of exactly one disposition (accept/adapt/reject/defer) toward one already
-received ``mrr.contracts.correction_notification.CorrectionNotification``
(E6-T03). It is deliberately NOT a nested field on
``mrr.contracts.correction_event.CorrectionEvent`` — E6-T03's own invariant
is that the receiving practice never stores any copy of the remote
``CorrectionEvent`` at all, so there is nowhere on that object to attach a
response. It is also deliberately NOT itself a signed, cross-practice value
object like ``CorrectionNotification``/``TransferContract`` (task-packets/
E6-T04.yaml derived_decisions (a)): a ``CorrectionResponse`` never crosses a
practice boundary in this task's scope (see specification_gaps for the
sender-side response-transport-back mechanism this leaves undesigned), so it
carries no ``signature`` field and needs none.

--- A standalone ``BaseObject``, single revision, no lifecycle machine ------

``CorrectionResponse`` still IS a first-class, revisioned MRR object — it
subclasses ``BaseObject`` for the same reason ``Claim``/``CorrectionEvent``/
``Obligation`` do (an id, a ``practice_id`` naming the RESPONDING practice
itself, a ``content_hash``, ...) — but it is recorded ONCE and never revised
again: no ``mrr.domain.lifecycles.StateMachine`` is declared for it (mirrors
the "no section-6 diagram exists for this entity" gap already flagged for
``TransferContract``/``Obligation``, here without even a domain-model
narrative field list to anchor a shape against — task-packets/E6-T04.yaml
derived_decisions (a)).

--- Four-verb vocabulary; the CorrectionNotification fields it echoes -------

``correction_notification_id``, ``notifying_practice_id``,
``origin_correction_event_id``, ``origin_correction_event_revision``, and
``notified_object_ids`` are plain COPIED SCALAR fields from the
``CorrectionNotification`` the caller already validated via E6-T03's
``receive_correction_notification`` — never a nested ``$ref`` to that
contract (task-packets/E6-T04.yaml derived_decisions (g)), so this entity's
own schema/contract/example can be implemented and tested independently of
``CorrectionNotification`` landing in code first (it already has, as of this
task, but the design does not depend on that landing order).

``decision`` is exactly one of ``accept``/``adapt``/``reject``/``defer`` —
read from docs/spec/06_IMPLEMENTATION_PLAN.md's own task title, by direct
analogy to MRR-FR-081's five-way TRANSFER vocabulary (accepted/adapted/
rejected/deferred/unresolved) minus "unresolved": a correction response,
unlike a transfer negotiation, is a one-time local recording act with no
further "still pending" outcome to name (task-packets/E6-T04.yaml
derived_decisions (b); flagged in specification_gaps that accept/adapt as
equally first-class recorded decisions is this task's own reading of the
implementation plan's task title, not a second literal FR mandate beyond
MRR-FR-084's own reject/defer text).

--- reason: required (non-null, non-empty) iff reject/defer ----------------

Mirrors ``mrr.contracts.claim.Claim``'s own "if status == supported" JSON
Schema conditional / ``model_validator`` precedent (schemas/claim.schema.json,
``Claim._supported_requires_evidence_and_verification``): the schema's own
``if``/``then`` requires ``reason`` to be PRESENT (and, via its own
unconditional ``minLength: 1``, non-empty) exactly when ``decision`` is
``reject``/``defer`` — MRR-FR-084's own literal text. The OTHER direction —
``reason`` must be null/absent for ``accept`` — is enforced ONLY by
``_reason_required_iff_reject_or_defer`` below, not by the JSON Schema
(task-packets/E6-T04.yaml derived_decisions (c); flagged in
specification_gaps as the schema-design axis with the most implementer
latitude).

--- adaptations: required (non-empty) iff adapt; a `corrects` edge each ----

``adaptations`` pairs a caller-supplied, ALREADY-EXISTING local
``adapted_object_id`` with the ``notified_object_id`` it addresses — required
(non-empty) exactly when ``decision`` is ``adapt`` (schema ``if``/``then``
plus ``_adaptations_required_iff_adapt`` below), and otherwise present but
EMPTY (unlike ``reason``, which is otherwise absent — ``adaptations`` is
always a required top-level property, mirroring ``Claim.evidence_relations``'s
own "present, conditionally non-empty" shape rather than
``replacement_object_id``'s "conditionally absent" shape). Every
``adaptations[].notified_object_id`` MUST also be a member of this response's
own ``notified_object_ids`` — a referential-consistency check the JSON Schema
alone cannot express (no cross-array membership constraint language),
enforced by ``_adaptations_reference_notified_object_ids`` below
(task-packets/E6-T04.yaml derived_decisions (d)). Each pair is intended by
``mrr.services.correction.service.CorrectionImpactService.record_response``
to record one ``corrects`` edge (source=``adapted_object_id``,
target=``notified_object_id``) — the section-3 edge vocabulary's own
``corrects`` type, declared but left entirely unused since E3-T06, and
deliberately NOT ``adapted_from`` (E6-T01's own TRANSFER-adaptation edge
type), to keep "adapting a TRANSFERRED object" and "adapting in RESPONSE TO
A CORRECTION" distinguishable in the graph.
"""

from __future__ import annotations

from typing import Literal, Self

from mrr.contracts.common import BaseObject, MRRModel, Urn
from pydantic import Field, model_validator

#: Mirrors `decision`. Read from docs/spec/06_IMPLEMENTATION_PLAN.md's own
#: task title ("local accept/adapt/reject/defer response") — see the module
#: docstring's "Four-verb vocabulary" section for why this has no
#: "unresolved" analogue.
CorrectionResponseDecision = Literal["accept", "adapt", "reject", "defer"]

#: The two `decision` values MRR-FR-084's own literal text requires a
#: non-empty `reason` for.
_REASON_REQUIRED_DECISIONS = frozenset({"reject", "defer"})


class CorrectionResponseAdaptation(MRRModel):
    """Mirrors an `adaptations[]` entry; both properties are required."""

    adapted_object_id: Urn
    notified_object_id: Urn


class CorrectionResponse(BaseObject):
    """Mirrors schemas/correction-response.schema.json.

    `reason` is the only property absent from the schema's top-level
    `required` list (conditionally required — see the module docstring).
    `adaptations` IS in the schema's top-level `required` list (always
    present, conditionally non-empty — mirrors `Claim.evidence_relations`).
    """

    kind: Literal["CorrectionResponse"]
    correction_notification_id: Urn
    notifying_practice_id: Urn
    origin_correction_event_id: Urn
    origin_correction_event_revision: int = Field(ge=1)
    notified_object_ids: list[Urn] = Field(min_length=1)
    decision: CorrectionResponseDecision
    reason: str | None = None
    adaptations: list[CorrectionResponseAdaptation]

    @model_validator(mode="after")
    def _reason_required_iff_reject_or_defer(self) -> Self:
        """Mirrors the schema's `if decision in [reject, defer] then
        required: [reason]` (MRR-FR-084's own literal text) — PLUS the one
        direction the schema does not enforce (task-packets/E6-T04.yaml
        derived_decisions (c)): `reason` must be null/absent for every OTHER
        decision.
        """
        if self.decision in _REASON_REQUIRED_DECISIONS:
            if not self.reason:
                raise ValueError(
                    f"a CorrectionResponse with decision {self.decision!r} must have a "
                    "non-empty reason (MRR-FR-084)"
                )
        elif self.reason is not None:
            raise ValueError(
                f"a CorrectionResponse with decision {self.decision!r} must not carry a "
                "reason — reason is reserved for decision in ('reject', 'defer')"
            )
        return self

    @model_validator(mode="after")
    def _adaptations_required_iff_adapt(self) -> Self:
        """Mirrors the schema's `if decision == adapt then required:
        [adaptations], adaptations.minItems >= 1` — PLUS the one direction
        the schema does not enforce (task-packets/E6-T04.yaml
        derived_decisions (d)): `adaptations` must be empty for every OTHER
        decision.
        """
        if self.decision == "adapt":
            if not self.adaptations:
                raise ValueError(
                    "a CorrectionResponse with decision 'adapt' must have at least one "
                    "adaptations entry"
                )
        elif self.adaptations:
            raise ValueError(
                f"a CorrectionResponse with decision {self.decision!r} must not carry any "
                "adaptations entries — adaptations is reserved for decision == 'adapt'"
            )
        return self

    @model_validator(mode="after")
    def _adaptations_reference_notified_object_ids(self) -> Self:
        """Every `adaptations[].notified_object_id` MUST be a member of this
        response's own `notified_object_ids` — a referential-consistency
        check no JSON Schema in this repository expresses (no cross-array
        membership constraint language), so this is Pydantic-only.
        """
        notified = set(self.notified_object_ids)
        for entry in self.adaptations:
            if entry.notified_object_id not in notified:
                raise ValueError(
                    f"adaptations[].notified_object_id {entry.notified_object_id!r} is not a "
                    "member of this response's own notified_object_ids"
                )
        return self
