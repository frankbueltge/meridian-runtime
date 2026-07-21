"""Mirrors schemas/obligation.schema.json (docs/spec/02_DOMAIN_MODEL.md
section 2.15, "Obligation" — task-packets/E6-T02.yaml, the twentieth
schema/contract/example entity, following E6-T01's ``TransferContract``).

--- Unsigned, revision-based — unlike TransferContract -----------------------

Section 2.15 lists no ``signature``/``signatures`` field (unlike section
2.14's ``TransferContract``), so ``Obligation`` follows ``Claim``/
``CorrectionEvent``'s precedent, not ``TransferContract``'s/``TaskBundle``'s:
a lifecycle transition (``resolve``/``defer``,
``mrr.services.obligation.service.ObligationService``) mints a NEW REVISION
(content hash recomputed), never an ADR-0007-style event-only transition —
task-packets/E6-T02.yaml derived_decisions (a).

--- obligation_kind reuses TransferContract's ObligationKind verbatim --------

``TransferContract.obligations[]`` (``mrr.contracts.transfer_contract.
ObligationStub``) already carries the identical eight-value ``kind`` vocabulary
this entity's own duty-kind field needs (docs/spec/02_DOMAIN_MODEL.md section
2.15's own "Kinds include" list — the same prose ``ObligationStub.kind``
already draws from). Importing ``ObligationKind`` directly rather than
re-declaring an identical ``Literal`` avoids two independently-maintained
copies of the same eight-value vocabulary silently drifting apart — this
module reads `mrr.contracts.transfer_contract` (an existing E6-T01 file) but
never modifies it, matching task-packets/E6-T02.yaml's forbidden_changes
("read ... but does not modify"). ``kind`` itself (the entity discriminator,
``Literal["Obligation"]``, inherited naming convention from every other
``BaseObject`` subclass) is therefore distinct from ``obligation_kind`` (the
duty kind) to avoid a name collision with ``BaseObject.kind``.

--- Field defaults per task-packets/E6-T02.yaml derived_decisions (f) --------

``responsible_practice_id`` mirrors the materializing TransferContract's own
``receiver_practice_id``; ``responsible_role`` stays unset (no responsible-
ROLE data exists on a structural obligation stub). ``trigger`` is a fixed
descriptive string naming the transfer decision/event that created this
Obligation. ``resolution_evidence`` is ``None`` until ``resolve()``.
``escalation_policy`` stays optional and unused (present-but-unused
structural field only — no escalation mechanism is implemented, per
forbidden_changes). ``caveat_text`` is populated only for the one additional
``retain_caveat``-kind Obligation materialized from a TransferContract's
non-empty ``caveats`` field (derived_decisions (c)); ``None``/absent for
every Obligation materialized from an explicit ``obligations[]`` stub, even
one whose own ``kind`` also happens to be ``retain_caveat`` — the two
mechanisms are independent and may each produce their own Obligation on the
same transfer.

``bound_objects``/``propagated_objects`` are kept as two separate fields
(derived_decisions (g), mirroring ``CorrectionEvent.affected_objects``/
``impact_objects``'s identical seeds-vs-computed-closure split): the former
is the transfer's own ``transferred_objects`` id set, seeded once at
materialization and never rewritten by ``propagate``; the latter is
``mrr.domain.obligation_propagation.compute_obligation_binding``'s own
computed downstream closure, recomputed (and, when it changes, rewritten as
a new revision — sorted, for a deterministic content hash) by every
``propagate`` call.
"""

from __future__ import annotations

from typing import Any, Literal

from mrr.contracts.common import BaseObject, Urn
from mrr.contracts.transfer_contract import ObligationKind
from pydantic import AwareDatetime, Field

#: Mirrors the top-level `status` enum — task-packets/E6-T02.yaml
#: derived_decisions (a): `open -> {resolved, deferred}`, lowercase (matching
#: TransferContract's own casing choice, the newest sibling entity, in the
#: absence of any section-6 diagram or schema precedent for Obligation).
ObligationStatus = Literal["open", "resolved", "deferred"]


class Obligation(BaseObject):
    """Mirrors schemas/obligation.schema.json.

    ``responsible_role``, ``deadline``, ``resolution_evidence``,
    ``escalation_policy``, and ``caveat_text`` are the schema's only
    top-level properties absent from its ``required`` list — each a plain
    optional scalar/array (not JSON-``null``-nullable), mirroring
    ``mrr.contracts.common.Budget``'s identical "unset means not stated,
    dropped on dump via ``exclude_none=True``" convention rather than
    ``CorrectionEvent.replacement_object_id``'s explicit
    ``anyOf: [urn, null]`` shape — no caller ever needs to set one of these
    five fields to an explicit JSON ``null`` in this task's scope.
    """

    kind: Literal["Obligation"]
    source_transfer_id: Urn
    obligation_kind: ObligationKind
    responsible_practice_id: Urn
    responsible_role: str | None = None
    trigger: str = Field(min_length=1)
    deadline: AwareDatetime | None = None
    status: ObligationStatus
    bound_objects: list[Urn] = Field(min_length=1)
    propagated_objects: list[Urn] = Field(default_factory=list)
    resolution_evidence: str | None = Field(default=None, min_length=1)
    escalation_policy: dict[str, Any] | None = None
    caveat_text: list[str] | None = None
