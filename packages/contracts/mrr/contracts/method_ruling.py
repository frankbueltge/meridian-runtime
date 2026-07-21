"""Mirrors schemas/method-ruling.schema.json (docs/spec/08_RESEARCH_METHOD_KERNEL.md,
status ACCEPTED 2026-07-21, section 3 "Method-governance objects (Layer 1)").
Fifth of the six task-packets/K1-T01.yaml entities.

``ruled_ceiling: ClaimCeiling`` imports and reuses
``mrr.contracts.method_profile.ClaimCeiling``/``CLAIM_CEILING_ORDER``
verbatim — never redeclared. MRR-MTH-017's "scope of validity and at least
one named non-applicability condition" is placed HERE, on ``MethodRuling``
(``scope_of_validity: Scope``, reusing ``mrr.contracts.common.Scope``
verbatim like ``QuestionModel`` does, and ``non_applicability_conditions:
list[str]``), rather than as a new field on ``Claim`` — deliberately, since
``schemas/claim.schema.json``/``packages/contracts/mrr/contracts/claim.py``
are outside this task's `allowed_paths` (K1-T02 territory); whether ``Claim``
should ALSO carry its own copy is left open (task-packets/K1-T01.yaml
specification_gaps). ``non_applicability_conditions`` is required non-empty
exactly when ``CLAIM_CEILING_ORDER.index(ruled_ceiling) >
CLAIM_CEILING_ORDER.index("descriptive")`` — MTH-017's own "claims above
descriptive" threshold, enforced below by importing the same ordered tuple
rather than re-deriving it.

``ruling_basis`` is a two-value discriminator (``deterministic_rule`` /
``human_review``) with exactly one of ``deterministic_rule_reference`` /
``human_review`` populated to match — mirroring
``mrr.contracts.evidence_anchor.EvidenceAnchor``'s own
``anchor_kind``-picks-the-active-half pattern exactly.

``issued_by: Urn`` is required unconditionally (following
``MethodProfile``'s own "every field required from draft" discipline,
task-packets/K0-T01.yaml derived_decisions (h)) — kept distinct from
``BaseObject.created_by`` because a system process could persist a ruling on
a human reviewer's behalf (mirrors ``VerificationResult.reviewer_id``'s own
coexistence with ``created_by``).
"""

from __future__ import annotations

from typing import Literal, Self

from mrr.contracts.common import BaseObject, MRRModel, Scope, Urn
from mrr.contracts.method_profile import CLAIM_CEILING_ORDER, ClaimCeiling
from pydantic import Field, model_validator

__all__ = ["HumanReviewReference", "MethodRuling", "MethodRulingStatus", "RulingBasis"]

#: Mirrors schemas/method-ruling.schema.json's `ruling_basis` enum.
RulingBasis = Literal["deterministic_rule", "human_review"]

#: Mirrors schemas/method-ruling.schema.json's `status` enum — spec 08
#: section 3's table: "MethodRuling | ... | pending -> issued -> superseded".
MethodRulingStatus = Literal["pending", "issued", "superseded"]

#: MTH-017's own "claims above descriptive" threshold: every ClaimCeiling
#: strictly stronger than "descriptive" in CLAIM_CEILING_ORDER (imported,
#: never redeclared).
_DESCRIPTIVE_INDEX = CLAIM_CEILING_ORDER.index("descriptive")


class HumanReviewReference(MRRModel):
    """Mirrors the `human_review` object: the active half of `ruling_basis`
    when it is `"human_review"`.
    """

    reviewer_id: Urn
    review_note: str = Field(min_length=1)


class MethodRuling(BaseObject):
    """Mirrors schemas/method-ruling.schema.json.

    Every property in the schema's top-level `required` list is required
    here too. `deterministic_rule_reference` and `human_review` are both
    explicitly nullable and individually optional (default `None`) — see
    the module docstring's `ruling_basis`-picks-the-active-half pattern.
    """

    kind: Literal["MethodRuling"]
    ruled_ceiling: ClaimCeiling
    scope_of_validity: Scope
    non_applicability_conditions: list[str]
    ruling_basis: RulingBasis
    deterministic_rule_reference: str | None = None
    human_review: HumanReviewReference | None = None
    issued_by: Urn
    protocol_id: Urn
    applies_to_analysis: str = Field(min_length=1)
    status: MethodRulingStatus

    @model_validator(mode="after")
    def _non_applicability_required_above_descriptive(self) -> Self:
        """MRR-MTH-017: non-empty exactly when the ruled ceiling is above
        'descriptive' in CLAIM_CEILING_ORDER.
        """
        if (
            CLAIM_CEILING_ORDER.index(self.ruled_ceiling) > _DESCRIPTIVE_INDEX
            and not self.non_applicability_conditions
        ):
            raise ValueError(
                f"MethodRuling with ruled_ceiling {self.ruled_ceiling!r} (above "
                "'descriptive') must carry at least one non_applicability_conditions "
                "entry (MRR-MTH-017)"
            )
        return self

    @model_validator(mode="after")
    def _ruling_basis_picks_exactly_one_active_half(self) -> Self:
        """Mirrors EvidenceAnchor's own anchor_kind-picks-the-active-half
        pattern: exactly one of deterministic_rule_reference / human_review
        is non-null, matching ruling_basis.
        """
        if self.ruling_basis == "deterministic_rule":
            if self.deterministic_rule_reference is None:
                raise ValueError(
                    "ruling_basis 'deterministic_rule' requires a non-null "
                    "deterministic_rule_reference"
                )
            if self.human_review is not None:
                raise ValueError(
                    "ruling_basis 'deterministic_rule' must not carry a non-null human_review"
                )
        else:
            if self.human_review is None:
                raise ValueError("ruling_basis 'human_review' requires a non-null human_review")
            if self.deterministic_rule_reference is not None:
                raise ValueError(
                    "ruling_basis 'human_review' must not carry a non-null "
                    "deterministic_rule_reference"
                )
        return self
