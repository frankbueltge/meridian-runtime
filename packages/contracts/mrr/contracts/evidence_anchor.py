"""Mirrors schemas/evidence-anchor.schema.json (docs/spec/02_DOMAIN_MODEL.md
section 2.9, "EvidenceAnchor": connects a claim-relevant proposition to an
exact part of a source or run).

--- Both variants, one object -------------------------------------------------

Domain 2.9 describes a text-anchor field set and a computational-anchor
field set side by side, sharing `relation`/`extraction_method`/
`extractor_id`/`anchor_validation_status`. Rather than model two disjoint
subtypes (which the schema's flat `additionalProperties`-closed shape does
not attempt either — see schemas/evidence-anchor.schema.json's own
docstring-equivalent `description`), this module carries every field from
both variants on one model, all individually nullable; `anchor_kind`
(`"text"` / `"computational"`) says which half is semantically active for a
given instance, and the validator below only checks the half that matches.

--- The CRITICAL invariant, enforced twice ------------------------------------

Domain 2.9: "the anchor must resolve against the exact referenced version or
explicitly declare why exact anchoring is impossible." task-packets/
E3-T01.yaml ties this directly to the stage-6 acceptance
(docs/spec/01_SYSTEM_SPEC.md section 4.6): "a supported source claim cannot
cite only a bare URL without an evidence anchor or explicit
`anchor_unavailable` reason." This is enforced identically in TWO places —
schemas/evidence-anchor.schema.json's `if`/`then` conditionals (checked by
`scripts/check_contracts.py` and any other JSON Schema consumer) AND
`_exact_resolution_or_explicit_reason` below (checked the moment Python code
constructs an `EvidenceAnchor`, before it ever reaches a schema validator) —
mirroring exactly how `mrr.contracts.claim.Claim` enforces its own single
schema conditional twice (see that module's docstring for the same
rationale: this is not expressible as a plain per-field constraint, since it
depends on another field's value, so a hand-written `model_validator` is
required either way).

An anchor with NEITHER an exact resolution NOR a non-null
`anchor_unavailable_reason` fails BOTH validators — never silently accepted
by one and only caught by the other (task-packets/E3-T01.yaml acceptance
test: "an anchor with neither an exact resolution NOR an anchor_unavailable
reason fails validation (fails closed)").

--- recomputation_status: not a specification-given vocabulary --------------

Domain 2.9 names "recomputation status" as a computational-anchor field
without enumerating values anywhere in docs/spec/ (confirmed by a full
search before writing this module). The three values below
(`"reproduced"`/`"not_reproduced"`/`"not_attempted"`) are this task's own
minimal proposal — not spec-derived — exactly the same situation
`mrr.services.task_bundle.service.RefusalReason` documents for its own
five-value vocabulary ("this task's own minimal, coarse proposal ... not a
specification-derived vocabulary"), and is flagged as an open specification
question in this task's PR for the same reason.
"""

from __future__ import annotations

from typing import Literal, Self

from mrr.contracts.common import ArtifactRef, BaseObject, MRRModel, Sha256, Urn
from pydantic import Field, model_validator

#: Mirrors `relation` — reuses exactly the four values domain 2.9 names,
#: which are also four of the nineteen `mrr.domain.repositories.EDGE_VOCABULARY`
#: members (supports, contradicts, qualifies, contextualizes).
EvidenceRelation = Literal["supports", "contradicts", "qualifies", "contextualizes"]

#: Mirrors `anchor_kind`.
AnchorKind = Literal["text", "computational"]

#: Mirrors `anchor_validation_status`.
AnchorValidationStatus = Literal["validated", "unvalidated", "invalid"]

#: Mirrors `recomputation_status`. See the module docstring's
#: "recomputation_status: not a specification-given vocabulary" section.
RecomputationStatus = Literal["reproduced", "not_reproduced", "not_attempted"]


class TextLocator(MRRModel):
    """Mirrors the `locator` object: "page, section, paragraph, line,
    character offsets, or structured selector" (domain 2.9). Every field is
    individually optional — a caller supplies whichever subset locates the
    fragment; none is privileged over the others.
    """

    page: int | str | None = None
    section: str | None = None
    paragraph: int | str | None = None
    line_start: int | None = None
    line_end: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    selector: str | None = None


class ComputationalSelector(MRRModel):
    """Mirrors the computational-anchor `selector` object: "table/column/row,
    JSON Pointer, query, or notebook cell" (domain 2.9).
    """

    table: str | None = None
    column: str | None = None
    row: int | str | None = None
    json_pointer: str | None = None
    query: str | None = None
    notebook_cell: str | None = None


class EvidenceAnchor(BaseObject):
    """Mirrors schemas/evidence-anchor.schema.json. See the module docstring
    for the shared-model-both-variants shape and the critical
    exact-resolution-or-explicit-reason invariant.

    Every property is in the schema's top-level `required` list except the
    text-anchor fields (`source_record_id`, `snapshot_hash`, `locator`,
    `quoted_fragment_hash`), the computational-anchor fields (`run_id`,
    `output_artifact`, `selector`, `recomputation_status`), and
    `anchor_unavailable_reason` — all explicitly nullable in the schema and
    deliberately absent from `required`, per this codebase's universal
    `model_dump_json(exclude_none=True)` round-trip convention (see
    `mrr.contracts.run_manifest`'s own docstring). `transformation_chain` IS
    schema-required (an always-present, possibly-empty array — mirroring
    `mrr.contracts.run_manifest.RunManifest.seeds`), so it carries no
    default here either.
    """

    kind: Literal["EvidenceAnchor"]
    relation: EvidenceRelation
    anchor_kind: AnchorKind
    extraction_method: str = Field(min_length=1)
    extractor_id: Urn
    anchor_validation_status: AnchorValidationStatus
    anchor_unavailable_reason: str | None = None

    # Text-anchor fields (nullable; semantically active when anchor_kind == "text").
    source_record_id: Urn | None = None
    snapshot_hash: Sha256 | None = None
    locator: TextLocator | None = None
    quoted_fragment_hash: Sha256 | None = None

    # Computational-anchor fields (nullable; semantically active when
    # anchor_kind == "computational").
    run_id: Urn | None = None
    output_artifact: ArtifactRef | Urn | None = None
    selector: ComputationalSelector | None = None
    transformation_chain: list[str]
    recomputation_status: RecomputationStatus | None = None

    @model_validator(mode="after")
    def _exact_resolution_or_explicit_reason(self) -> Self:
        """domain 2.9's invariant, fail closed. For `anchor_kind == "text"`,
        an exact resolution is `snapshot_hash` or `quoted_fragment_hash`
        being non-null. For `"computational"`, it is `run_id` AND
        `recomputation_status` both being non-null together (task-packets/
        E3-T01.yaml: "computational -> run_id + recomputation reference" —
        a bare `run_id` with no recorded recomputation outcome is not yet an
        exact resolution). Either way, a non-null `anchor_unavailable_reason`
        always satisfies the invariant regardless of what resolution fields
        are set.
        """
        if self.anchor_kind == "text":
            resolved = self.snapshot_hash is not None or self.quoted_fragment_hash is not None
        else:
            resolved = self.run_id is not None and self.recomputation_status is not None
        if not resolved and self.anchor_unavailable_reason is None:
            raise ValueError(
                "an EvidenceAnchor must provide an exact resolution appropriate to its "
                "anchor_kind (text: snapshot_hash or quoted_fragment_hash; computational: "
                "run_id and recomputation_status) or a non-null anchor_unavailable_reason"
            )
        return self
