"""Mirrors schemas/verification-result.schema.json (docs/spec/02_DOMAIN_MODEL.md
section 2.13, "Review and VerificationResult": "a review records judgment; a
verification records checks."). Fourth task of Epic E3 (claim, evidence,
correction kernel); the eleventh entity schema/model pair in this
repository. This is the schema/contract half of the task; the recording
service and its self-verification gate (AGENTS.md rule 8, MRR-FR-070) are
``mrr.services.verification.service.VerificationService``.

--- No separate `verification_id` field; minted with URN entity "verification" ---

Like every sibling entity (``SourceFamily``, ``Claim``, ...), the base
object's own ``id`` already names this record — there is no second,
redundant identifier field. It is minted with URN entity ``"verification"``
(``mrr.domain.identity.new_urn("verification")``), not ``"verification-result"``,
to match the precedent ``schemas/claim.schema.json``'s own example/fixtures
already set for the shape of a value in ``Claim.verification_ids``
(``urn:mrr:verification:...``) before this schema existed. The URN entity
segment has no registry enforcing a specific string
(``mrr.domain.identity._ENTITY_PATTERN`` accepts any ``[a-z0-9-]+``), so this
is a naming-consistency choice flagged for reviewer scrutiny in the PR body,
not a structural requirement either way could have satisfied.

--- confidence: the REVIEWER's own confidence, not epistemic truth -----------

Mirrors ``mrr.contracts.source_family.SourceFamily.confidence``'s own
guard exactly: a plain bounded ``[0, 1]`` float, documented as the
reviewer's own confidence in their verification judgment — NEVER treated
here (or by any downstream consumer without its own justification) as a
calibrated epistemic probability of anything, and never itself an
"independence weight". AGENTS.md's prohibited-shortcuts list names exactly
this trap ("using an LLM confidence number as epistemic confidence").

--- findings[].severity reuses CorrectionEvent's exact vocabulary ------------

Domain 2.13 names "findings by severity" without enumerating values of its
own. Rather than invent a fourth vocabulary, ``FindingSeverity`` reuses
``mrr.contracts.correction_event.CorrectionSeverity``'s exact three values
(``minor``/``material``/``critical``) for consistency across this
codebase's two "severity" fields — kept as its own ``Literal`` (not an
import of ``CorrectionSeverity`` itself) since a verification finding and a
correction are conceptually distinct entities that happen to share a
vocabulary, not one entity reusing another's type.

--- checks_performed / conflicts_of_interest: plain string arrays ------------

Domain 2.13 gives no further structure for either field beyond naming them.
Modeled as plain ``list[str]`` (one free-text entry per check performed, or
per conflict-of-interest declaration) — not a spec-derived vocabulary,
mirroring ``mrr.contracts.evidence_anchor.RecomputationStatus``'s own
"not a specification-given vocabulary" precedent; flagged as an open
question in this task's PR body.

--- numeric_recomputation / evidence_inspected: enforced twice, like EvidenceAnchor ---

MRR-FR-072 ("Source verification MUST retrieve or locally inspect the cited
source and validate the evidence anchor") and MRR-FR-073 ("Numeric
verification MUST recompute the value or explicitly record why
recomputation is impossible") are each enforced in TWO places — the JSON
Schema if/then conditionals (checked by ``scripts/check_contracts.py``) AND
the ``model_validator`` below (checked the moment Python code constructs a
``VerificationResult``) — mirroring exactly how
``mrr.contracts.evidence_anchor.EvidenceAnchor`` enforces its own
"exact resolution or explicit reason" invariant twice, and
``mrr.contracts.claim.Claim`` its own conditional. Neither is expressible as
a plain per-field constraint (each depends on ``verification_type``'s
value), so a hand-written validator is required either way.

--- Independence is DECLARED here, not validated ------------------------------

``independence_profile`` requires all six MRR-FR-071 dimensions
(``principal``, ``model_family``, ``prompt_family``, ``retrieval_path``,
``code_path``, ``data_access_path``) — a record missing any one dimension
fails validation, since none of ``IndependenceProfile``'s six fields carries
a default. This schema and this module do NOT validate that a declared
profile is genuinely independent from the claim's proposer or the run's
executor (same-model/prompt/path detection, MRR-FR-076) — that calculation
is E3-T05's ``forbidden_changes``-flagged scope. The basic self-verification
prohibition this task DOES enforce (reviewer identity != proposer/executor)
lives in ``mrr.services.verification.service.VerificationService.record``,
not here — a contract model has no access to the claim or run it is
validated against.

--- No signature; create-only, like SourceFamily and Claim -------------------

``VerificationResult`` carries no ``signature`` field (domain 2.13 lists
none). Unlike disagreement being resolved by mutating a prior record,
domain 2.13's MRR-FR-077 ("The system MUST preserve reviewer disagreement
and adjudication rationale") is satisfied by recording a SEPARATE new
``VerificationResult`` object per review, linked by ``adjudication_relation``
— never a new revision of an earlier one. This module (and its service)
therefore describe no update/mutate lifecycle at all, exactly like
``mrr.contracts.source_family.SourceFamily``.
"""

from __future__ import annotations

from typing import Literal, Self

from mrr.contracts.common import BaseObject, MRRModel, Urn
from pydantic import Field, model_validator

#: Mirrors `target_kind`.
TargetKind = Literal["claim", "run", "artifact"]

#: Mirrors `verification_type`.
VerificationType = Literal["source", "numeric", "skeptic", "reproduction", "other"]

#: Mirrors `recommendation`.
Recommendation = Literal["pass", "fail", "inconclusive"]

#: Mirrors `findings[].severity`. See the module docstring's "findings[].severity
#: reuses CorrectionEvent's exact vocabulary" section — deliberately the same
#: three values as `mrr.contracts.correction_event.CorrectionSeverity`, kept
#: as its own Literal rather than importing that type.
FindingSeverity = Literal["minor", "material", "critical"]


class IndependenceProfile(MRRModel):
    """Mirrors `independence_profile` — MRR-FR-071's exact six dimensions.
    Every field is required (no default): a record missing any one
    dimension fails validation, both here and via the schema's own
    `required` list on this nested object.
    """

    principal: str = Field(min_length=1)
    model_family: str = Field(min_length=1)
    prompt_family: str = Field(min_length=1)
    retrieval_path: str = Field(min_length=1)
    code_path: str = Field(min_length=1)
    data_access_path: str = Field(min_length=1)


class NumericRecomputation(MRRModel):
    """Mirrors `numeric_recomputation`'s object variant (the schema's
    `$defs.numericRecomputationObject` combined with
    `$defs.numericResolutionPresent`). MRR-FR-073's invariant is enforced by
    `_recomputed_value_or_impossible_reason` below: `recomputed_value` or
    `impossible_reason` must be non-`None` — never neither.
    """

    recomputed_value: float | str | None = None
    matches_claimed_value: bool | None = None
    method: str | None = None
    impossible_reason: str | None = None

    @model_validator(mode="after")
    def _recomputed_value_or_impossible_reason(self) -> Self:
        if self.recomputed_value is None and self.impossible_reason is None:
            raise ValueError(
                "numeric_recomputation must record either a recomputed_value or an explicit "
                "impossible_reason (MRR-FR-073) — neither may be blank"
            )
        return self


class Finding(MRRModel):
    """Mirrors a `findings[]` entry; both properties are required."""

    severity: FindingSeverity
    statement: str = Field(min_length=1)


class VerificationResult(BaseObject):
    """Mirrors schemas/verification-result.schema.json. See the module
    docstring for the full design rationale.

    Every property is in the schema's top-level `required` list except
    `numeric_recomputation` and `adjudication_relation` — both explicitly
    nullable and, per this codebase's universal
    `model_dump_json(exclude_none=True)` round-trip convention, deliberately
    absent from `required` so a `None` value round-trips correctly.
    """

    kind: Literal["VerificationResult"]
    target_id: Urn
    target_kind: TargetKind
    reviewer_id: Urn
    reviewer_role: str = Field(min_length=1)
    independence_profile: IndependenceProfile
    verification_type: VerificationType
    checks_performed: list[str]
    evidence_inspected: list[Urn]
    numeric_recomputation: NumericRecomputation | None = None
    findings: list[Finding]
    recommendation: Recommendation
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1)
    conflicts_of_interest: list[str]
    adjudication_relation: Urn | None = None

    @model_validator(mode="after")
    def _source_and_numeric_type_requirements(self) -> Self:
        """Mirrors the schema's two if/then conditionals (MRR-FR-072/073):
        a `verification_type == "source"` record must have inspected at
        least one piece of evidence; a `verification_type == "numeric"`
        record must carry a non-null `numeric_recomputation` (which, in
        turn, `NumericRecomputation` itself already guarantees is not
        blank).
        """
        if self.verification_type == "source" and not self.evidence_inspected:
            raise ValueError(
                "a verification_type 'source' record must have at least one "
                "evidence_inspected entry (MRR-FR-072)"
            )
        if self.verification_type == "numeric" and self.numeric_recomputation is None:
            raise ValueError(
                "a verification_type 'numeric' record must carry a non-null "
                "numeric_recomputation (MRR-FR-073)"
            )
        return self
