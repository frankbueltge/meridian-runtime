"""Mirrors schemas/claim.schema.json (docs/spec/02_DOMAIN_MODEL.md
section 2.11, "Claim").
"""

from __future__ import annotations

from typing import Literal, Self

from mrr.contracts.common import BaseObject, MRRModel, Scope, Urn
from pydantic import Field, model_validator

#: Mirrors `claim_type`.
ClaimType = Literal[
    "observational",
    "causal",
    "statistical",
    "methodological",
    "interpretive",
    "normative",
    "speculative",
]

#: Mirrors the top-level `status` enum.
ClaimStatus = Literal[
    "draft",
    "under_review",
    "supported",
    "contested",
    "contradicted",
    "unsupported",
    "unresolved",
    "review_required",
    "withdrawn",
    "superseded",
    "legacy_unverified",
]

#: Mirrors `uncertainty[].kind`.
UncertaintyKind = Literal[
    "measurement",
    "sampling",
    "model",
    "inferential",
    "source",
    "contextual",
    "ethical",
    "operational",
    "unknown",
]


class UncertaintyEntry(MRRModel):
    """Mirrors a `uncertainty[]` entry. `method` is the only property
    absent from its `required: ["kind", "statement"]` list, and is
    explicitly nullable (`type: ["string", "null"]`) as well as optional.
    """

    kind: UncertaintyKind
    statement: str = Field(min_length=1)
    method: str | None = None


class Claim(BaseObject):
    """Mirrors schemas/claim.schema.json.

    `source_family_ids` and `correction_ids` are the only two properties
    absent from the schema's top-level `required` list.

    The schema's `if`/`then` (the only conditional in any of the seven
    schemas — see packages/contracts/mrr/contracts/__init__.py and the
    other five entity modules, none of which have one): when
    `status == "supported"`, `evidence_relations` and `verification_ids`
    must each have at least one entry. That is not expressible as a plain
    per-field constraint (it depends on another field's value), so it is
    enforced by an `model_validator(mode="after")` below instead — the
    approved rationale (see PR body) for hand-writing this model rather than
    generating it.
    """

    kind: Literal["Claim"]
    assertion: str = Field(min_length=1)
    claim_type: ClaimType
    scope: Scope
    status: ClaimStatus
    evidence_relations: list[Urn]
    counterevidence_relations: list[Urn]
    dependencies: list[Urn]
    source_family_ids: list[Urn] = Field(default_factory=list)
    uncertainty: list[UncertaintyEntry]
    known_unknowns: list[str]
    proposer_id: Urn
    verification_ids: list[Urn]
    correction_ids: list[Urn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _supported_requires_evidence_and_verification(self) -> Self:
        """Mirrors the schema's `if status == "supported" then
        evidence_relations.minItems >= 1 AND verification_ids.minItems >= 1`
        (packet invariant: "supported claims require evidence and
        verification at contract level"; docs/spec/02_DOMAIN_MODEL.md
        section 7, invariant 2: "No supported claim without evidence and
        completed required verification.").
        """
        if self.status == "supported":
            if not self.evidence_relations:
                raise ValueError(
                    "a claim with status 'supported' must have at least one "
                    "evidence_relations entry"
                )
            if not self.verification_ids:
                raise ValueError(
                    "a claim with status 'supported' must have at least one verification_ids entry"
                )
        return self
