"""Mirrors schemas/evidence-matrix.schema.json (docs/spec/08_RESEARCH_METHOD_KERNEL.md,
status ACCEPTED 2026-07-21, section 3 "Method-governance objects (Layer 1)").
Fourth of the six task-packets/K1-T01.yaml entities.

MRR-MTH-015: every ``EvidenceMatrix`` row MUST anchor a resolvable source
with a verification status and a source family; unverifiable rows are
marked, never dropped; copied or derivative sources MUST NOT count as
independent evidence (the independence CALCULATION itself is E3-T05's
``mrr.domain.independence``, out of this task's scope — a row only records
its ``source_family_id``, never computes anything from it).

``rows`` carries NO minimum length — deliberately, unlike
``mrr.contracts.concept_charter.ConceptCharter.entries``. MRR-MTH-011
("``insufficient_evidence`` outcomes ... are successful terminal results ...
MUST NOT be silently omitted from any claim landscape") means a matrix that
legitimately found ZERO usable sources must remain constructible and
addressable, not rejected as degenerate — a zero-row ``EvidenceMatrix`` is
exactly the honest record a later ``stop_insufficient_evidence``
``ResearchDecision`` would point back to.

``verification_status`` (``"verified"`` / ``"unverifiable"`` / ``"pending"``)
is this task's own minimal, three-value LOCAL vocabulary — not a
specification-given one (spec 08 section 3/MTH-015 name "verification
status" without enumerating values), accepted per task-packets/K1-T01.yaml
``reviewer_resolution`` item (4). ``unverifiable_reason`` is required
non-null, non-empty exactly when ``verification_status == "unverifiable"``
(MRR-MTH-015's headline gate), enforced below.

``row_id`` values are required unique within one ``EvidenceMatrix``
revision, mirroring ``ConceptCharterEntry.entry_id``'s identical uniqueness
treatment, so a future edge or projection can address one row unambiguously.

``sensitivity_analysis_results`` (task-packets/K1-T03b.yaml, MRR-MTH-018:
"Where a protocol declares sensitivity analyses over classifications ...
the varied classifications MUST be versioned charter entries, and results
under each variation MUST be reported") is an OPTIONAL, NULLABLE, additive
field — a SECOND, parallel array of per-variation results, never a new
Layer-1 object kind (spec 08 section 3's table is a closed set of seven
kinds). Absent (``None``) on every ``EvidenceMatrix`` that declares no
variations — which is every existing example/fixture — round-trips
byte-identically to before this field existed
(``model_dump_json(exclude_none=True)``, the codebase's universal
convention).
"""

from __future__ import annotations

from typing import Literal, Self

from mrr.contracts.common import BaseObject, MRRModel, Urn
from pydantic import Field, model_validator

__all__ = [
    "EvidenceMatrix",
    "EvidenceMatrixRow",
    "EvidenceMatrixStatus",
    "EvidenceMatrixVerificationStatus",
    "SensitivityAnalysisResult",
]

#: This task's own minimal, local vocabulary (not specification-given) —
#: see the module docstring's "verification_status" section.
EvidenceMatrixVerificationStatus = Literal["verified", "unverifiable", "pending"]

#: Mirrors schemas/evidence-matrix.schema.json's `status` enum — spec 08
#: section 3's table: "EvidenceMatrix | ... | draft -> active -> frozen -> superseded".
EvidenceMatrixStatus = Literal["draft", "active", "frozen", "superseded"]


class EvidenceMatrixRow(MRRModel):
    """Mirrors one entry of `rows`. `evidence_anchor_id`, `source_family_id`,
    and `unverifiable_reason` are the only fields absent from the schema
    item's own `required` list — all explicitly nullable, per this
    codebase's universal `model_dump_json(exclude_none=True)` round-trip
    convention.
    """

    row_id: str = Field(min_length=1)
    source_record_id: Urn
    evidence_anchor_id: Urn | None = None
    source_family_id: Urn | None = None
    verification_status: EvidenceMatrixVerificationStatus
    unverifiable_reason: str | None = None
    claim_relevant_finding: str = Field(min_length=1)
    extraction: dict[str, str]

    @model_validator(mode="after")
    def _unverifiable_requires_reason(self) -> Self:
        """MRR-MTH-015: 'unverifiable rows are marked, never dropped'."""
        if self.verification_status == "unverifiable" and not self.unverifiable_reason:
            raise ValueError(
                "an EvidenceMatrixRow with verification_status 'unverifiable' must "
                "carry a non-null, non-empty unverifiable_reason (MRR-MTH-015)"
            )
        return self


class SensitivityAnalysisResult(MRRModel):
    """One MRR-MTH-018 sensitivity-variation result (task-packets/
    K1-T03b.yaml): "results under each variation MUST be reported". Mirrors
    ``EvidenceMatrixRow``'s own field style field-for-field.

    ``outcome`` is the SAME four-value vocabulary
    ``mrr.services.node_runtime.synthesis_executor``'s own base
    classification uses, so ``matches_base_outcome`` is a direct,
    same-vocabulary comparison against the base run's own outcome for the
    identical ``applies_to_analysis`` key. ``decision_rationale`` is
    required non-null exactly when ``outcome == "insufficient_evidence"``,
    mirroring ``EvidenceMatrixRow.unverifiable_reason``'s own identical
    co-occurrence-with-another-field validator pattern below.
    """

    variation_entry_id: str = Field(min_length=1)
    applies_to_analysis: str = Field(min_length=1)
    outcome: Literal["supported", "contested", "unsupported", "insufficient_evidence"]
    included_source_count: int = Field(ge=0)
    verified_source_count: int = Field(ge=0)
    distinct_independent_supporting_family_count: int = Field(ge=0)
    distinct_independent_contradicting_family_count: int = Field(ge=0)
    decision_rationale: str | None = None
    matches_base_outcome: bool

    @model_validator(mode="after")
    def _insufficient_evidence_requires_rationale(self) -> Self:
        """MRR-MTH-018: mirrors ``EvidenceMatrixRow._unverifiable_requires_reason``'s
        identical co-occurrence-with-another-field validator pattern.
        """
        if self.outcome == "insufficient_evidence" and not self.decision_rationale:
            raise ValueError(
                "a SensitivityAnalysisResult with outcome 'insufficient_evidence' must carry "
                "a non-null, non-empty decision_rationale (MRR-MTH-018)"
            )
        return self


class EvidenceMatrix(BaseObject):
    """Mirrors schemas/evidence-matrix.schema.json.

    Every property in the schema's top-level `required` list is required
    here too, including `rows` — the array itself is a required key, but
    carries no minimum length (see the module docstring).
    """

    kind: Literal["EvidenceMatrix"]
    protocol_id: Urn
    question_id: Urn
    rows: list[EvidenceMatrixRow]
    status: EvidenceMatrixStatus
    sensitivity_analysis_results: list[SensitivityAnalysisResult] | None = None

    @model_validator(mode="after")
    def _row_ids_are_unique(self) -> Self:
        """Mirrors ConceptCharterEntry.entry_id's identical uniqueness
        treatment: no two rows within one EvidenceMatrix revision may share
        a row_id.
        """
        seen: set[str] = set()
        for row in self.rows:
            if row.row_id in seen:
                raise ValueError(
                    f"duplicate EvidenceMatrix row_id {row.row_id!r} — row_id must be "
                    "unique within one EvidenceMatrix revision"
                )
            seen.add(row.row_id)
        return self

    @model_validator(mode="after")
    def _sensitivity_result_pairs_are_unique(self) -> Self:
        """task-packets/K1-T03b.yaml derived_decisions (c): no two
        ``sensitivity_analysis_results`` entries within one EvidenceMatrix
        revision may share an identical ``(variation_entry_id,
        applies_to_analysis)`` pair — mirrors ``_row_ids_are_unique``'s own
        identical uniqueness treatment.
        """
        if self.sensitivity_analysis_results is None:
            return self
        seen: set[tuple[str, str]] = set()
        for result in self.sensitivity_analysis_results:
            pair = (result.variation_entry_id, result.applies_to_analysis)
            if pair in seen:
                raise ValueError(
                    f"duplicate SensitivityAnalysisResult pair {pair!r} — "
                    "(variation_entry_id, applies_to_analysis) must be unique within one "
                    "EvidenceMatrix revision"
                )
            seen.add(pair)
        return self
