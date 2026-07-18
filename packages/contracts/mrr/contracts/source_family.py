"""Mirrors schemas/source-family.schema.json (docs/spec/02_DOMAIN_MODEL.md
section 2.10, "SourceFamily": represents evidence dependence between
sources). Third task of Epic E3 (claim, evidence, correction kernel).

--- Additive representation is the headline invariant (MRR-FR-065) ----------

"Source count MUST NOT be presented as evidence independence. Source
families MUST be represented separately" (docs/spec/01_SYSTEM_SPEC.md
MRR-FR-065), and domain 2.10's own invariant: "family confidence is not
used to silently delete sources. It changes independence calculations and
presentation." This module carries nothing that could mutate or delete a
``SourceRecord`` — ``member_source_ids`` is a plain list of urns (never an
embedded copy of the referenced records' content), matching
``mrr.contracts.evidence_anchor.EvidenceAnchor.source_record_id``'s own
by-reference-only precedent. The independence CALCULATION that actually
consumes a ``SourceFamily`` to adjust effective evidence weight is E3-T05 —
out of scope here (task-packets/E3-T03.yaml derived_decisions).

--- No separate `family_id` field ---------------------------------------------

task-packets/E3-T03.yaml offers a choice: "family_id (or reuse the base id
as the family identifier — decide and document)". This module reuses the
base object's own ``id`` (minted with URN entity ``source-family``) as the
family's identifier, exactly like every sibling entity in this codebase
(``Claim.id``, ``SourceRecord.id``, ``EvidenceAnchor.id``, ...) never
duplicates its own identity under a second, redundant field name. A future
consumer names a family the same way it names any other object: by its
``id``.

--- confidence is a plain [0, 1] number, not an enum -------------------------

task-packets/E3-T03.yaml also offers a choice here: "confidence (number
0..1 or an enum — choose per 2.10 and document)". This module picks a
bounded float. Domain 2.10 gives no calibration requirement for this field,
and the independence calculator that eventually consumes it (E3-T05) needs
a value it can use directly as a weight input rather than first having to
invent its own enum-to-number mapping. Critically, this is the DETECTING
METHOD's own confidence that the ``relationship_type`` classification is
correct — never treated here (or by any downstream consumer without its own
justification) as a calibrated epistemic probability of anything, and never
itself an "independence weight": AGENTS.md's own prohibited-shortcuts list
names exactly this trap ("using an LLM confidence number as epistemic
confidence"). Pairing it with a mandatory ``rationale`` and
``detecting_method`` keeps the number auditable rather than an opaque
scalar, mirroring this lab's own "AI output verified or marked as an
estimate" transparency stance.

--- origin_ref: urn, free text, or null ---------------------------------------

Domain 2.10 lists "origin source or dataset" as one field without saying it
must itself be a ``SourceRecord`` in this corpus — an origin is often a wire
service, press-release distributor, or external dataset never itself
retrieved and recorded as its own ``SourceRecord``. ``origin_ref`` therefore
accepts a urn (when the origin *is* a known ``SourceRecord``), a free-text
descriptor (when it is not), or ``None`` (origin not yet known — the
natural case for ``relationship_type == "uncertain"``). This mirrors
``mrr.contracts.evidence_anchor.EvidenceAnchor.output_artifact``'s own
urn-or-structured-or-null shape for the same "might or might not resolve to
a first-class object in this system" reason.

--- member_source_ids requires at least one entry -----------------------------

Not itself stated as a domain-2.10 sentence, but the same non-degeneracy
reasoning ``mrr.contracts.correction_event.CorrectionEvent.affected_objects``
already applies (``Field(min_length=1)`` there): a family referencing zero
sources represents nothing there is to represent. This is a structural
non-emptiness check, not an invented business rule about how MANY members
constitute "a family" (E3-T05's independence calculation is free to treat a
single-member family however it needs to) — flagged in the PR body for
reviewer scrutiny all the same, since domain 2.10 does not spell out a
minimum count.

--- relationship_type is a closed Literal, fails closed -----------------------

Mirrors the enum exactly (domain 2.10: "relationship type: copy,
syndication, shared dataset, shared press release, direct derivation,
uncertain"). An out-of-vocabulary value is rejected by both the JSON Schema
``enum`` and this ``Literal`` — neither validator waves it through.

--- No signature: plain append-only revisions, like Claim ---------------------

``SourceFamily`` carries no ``signature`` field (domain 2.10 lists none),
so — like ``Claim`` (``mrr.contracts.claim``) — its service persists plain
new revisions via ``mrr.persistence.unit_of_work.
record_object_revision_with_event``. This task only implements ``create``
(revision 1); there is no lifecycle here to drive with further revisions.
"""

from __future__ import annotations

from typing import Literal

from mrr.contracts.common import BaseObject, Urn
from pydantic import Field

#: Mirrors `relationship_type` — domain 2.10's exact vocabulary.
RelationshipType = Literal[
    "copy",
    "syndication",
    "shared_dataset",
    "shared_press_release",
    "direct_derivation",
    "uncertain",
]


class SourceFamily(BaseObject):
    """Mirrors schemas/source-family.schema.json.

    Every property is in the schema's top-level `required` list except
    `origin_ref` and `reviewer_id` — both explicitly nullable
    (`anyOf [<type>, {"type": "null"}]` in the schema) and, per this
    codebase's universal `model_dump_json(exclude_none=True)` round-trip
    convention, deliberately absent from `required` so a `None` value
    round-trips correctly instead of disappearing into a required-but-missing
    key (see `mrr.contracts.common.Budget`'s docstring for the full
    reasoning).
    """

    kind: Literal["SourceFamily"]
    origin_ref: Urn | str | None = None
    member_source_ids: list[Urn] = Field(min_length=1)
    relationship_type: RelationshipType
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1)
    detecting_method: str = Field(min_length=1)
    reviewer_id: Urn | None = None
