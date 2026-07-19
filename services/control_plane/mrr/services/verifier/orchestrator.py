"""The deterministic verifier orchestrator (task-packets/E4-T05.yaml) — maps
either deterministic tool's outcome
(``mrr.services.verifier.numeric.recompute_numeric_claim``,
``mrr.services.verifier.source.validate_evidence_anchor``) to a
``mrr.contracts.verification_result.Recommendation`` by a documented, TOTAL
policy, and assembles the full, schema-valid ``VerificationResult`` that
policy decision belongs to.

--- The tool-outcome -> Recommendation mapping: documented and TOTAL --------

:func:`recommendation_for_numeric_outcome` and
:func:`recommendation_for_anchor_status` are the whole of this task's
"deterministic policy" (MRR-FR-075's own phrase, reused here for the
verifier's OWN internal tool-to-recommendation mapping — a distinct, prior
step from ``mrr.services.verification.service.VerificationService``'s own
MRR-FR-075 failed-verification-to-CLAIM-STATUS policy, which this module
does not touch). Every branch is exhaustive over its input's closed
``Literal``/structural range — no branch is a silent no-op, and no outcome
reaches this module without mapping to exactly one of ``"pass"``/
``"fail"``/``"inconclusive"``:

- numeric: recomputation matches -> ``"pass"``; mismatches -> ``"fail"``;
  impossible (unknown operation, missing/malformed input, or an undefined
  operation like division by zero) -> ``"inconclusive"``.
- source: anchor ``"validated"`` -> ``"pass"``; ``"unvalidated"`` (the
  section 4.8 acceptance case: the source could not be locally opened) ->
  ``"inconclusive"`` — NEVER ``"pass"``; ``"invalid"`` (the source was
  opened but the specific citation does not hold) -> ``"fail"``.

--- No model, no persistence — this module PRODUCES; it does not RECORD ----

Building a ``VerificationResult`` here never invokes a model of any kind
(the ``rationale``/``checks_performed`` text below is built entirely from
f-strings over this module's own deterministic findings, never model
output) and never opens a network connection or touches a database — see
the ``mrr.services.verifier`` package docstring and the AST architecture
test that checks both. Recording the built object (a revision + event, the
MRR-FR-070 self-verification gate, and the MRR-FR-075 failed-verification
claim-status policy) is the EXISTING, UNMODIFIED
``mrr.services.verification.service.VerificationService`` (E3-T04) — a
SEPARATE, later call this module's own caller makes with the object either
``build_numeric_verification_result`` or ``build_source_verification_result``
returns.

--- Minting the envelope: mirrors _to_hypothesis's own precedent -----------

Both builders assemble a draft ``VerificationResult`` with a placeholder
``content_hash``, dump it to JSON, compute the real
``mrr.domain.hashing_policy.compute_content_hash``, and re-validate —
exactly ``mrr.services.planner.service._to_hypothesis``'s own "placeholder
content_hash, dump, recompute, re-validate" sequence. ``id_factory``/
``clock`` are injectable (defaulting to ``mrr.domain.identity.new_urn``/the
real wall clock) for the same determinism reason
``propose_hypothesis_forest``/``propose_skeptical_challenges`` already
document: two calls with IDENTICAL scripted inputs must yield an IDENTICAL
``VerificationResult`` (same ``content_hash``) — task-packets/E4-T05.yaml's
own determinism acceptance test, taken literally.

--- ``confidence``: always 1.0, never caller-configurable ------------------

``VerificationResult.confidence`` is documented, on that contract itself, as
"the REVIEWER's own confidence in their verification judgment" — never
epistemic truth. A deterministic tool's confidence in its OWN deterministic
finding is always maximal by construction: either the recomputed value
matches the claim or it does not; either the anchor resolves against locally
available content or it does not — there is no hedge a checked tool could
meaningfully express short of the ``"inconclusive"`` recommendation itself
(which already carries the honest "could not determine" signal via
``impossible_reason``/``unverified_source_access``). Hardcoding
:data:`_DETERMINISTIC_CONFIDENCE` (rather than accepting it as a parameter)
is a deliberate choice preventing a caller from quietly turning this
deterministic tool's output into a hedged, model-like confidence number —
exactly the "using an LLM confidence number as epistemic confidence" trap
AGENTS.md's prohibited-shortcuts list names, generalized to any oracle.

--- ``reviewer_role``/``checks_performed``: fixed, factual, never caller text
    beyond what each build function's own parameters already state --------

Both builders set a fixed ``reviewer_role`` string naming the deterministic
tool itself (never a caller-supplied free-text role, since the "reviewer"
recorded here IS this checked tool, not a human or model persona) and a
single, deterministically-generated ``checks_performed`` entry describing
exactly what ran. ``findings``/``conflicts_of_interest``/
``adjudication_relation`` remain caller-suppliable (default empty/``None``)
for the rare case a caller has additional context to attach; this module
never invents a ``Finding`` of its own (deciding a finding's ``severity`` is
a judgment call beyond mere mechanical recompute/anchor-access, and is
explicitly out of this task's scope).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from mrr.contracts.common import ApiVersion, Urn
from mrr.contracts.evidence_anchor import AnchorValidationStatus, EvidenceAnchor
from mrr.contracts.verification_result import (
    Finding,
    IndependenceProfile,
    NumericRecomputation,
    Recommendation,
    TargetKind,
    VerificationResult,
    VerificationType,
)
from mrr.domain.hashing_policy import compute_content_hash
from mrr.domain.identity import new_urn
from mrr.services.verifier.numeric import NumberLike, recompute_numeric_claim
from mrr.services.verifier.source import (
    LocalComputationalArtifact,
    LocalTextArtifact,
    SourceVerificationOutcome,
    validate_evidence_anchor,
)

#: Every VerificationResult this module mints uses this fixed, literal
#: api_version — matches every other from-scratch object-assembly precedent
#: in this codebase (e.g. mrr.services.planner.service's own draft).
_API_VERSION: ApiVersion = "mrr/v1alpha1"

#: A placeholder content_hash, structurally valid (matches
#: mrr.contracts.common.Sha256's pattern) but never the real one —
#: overwritten by ``_assemble`` before the final object is returned. Mirrors
#: mrr.services.planner.service._PLACEHOLDER_CONTENT_HASH's own precedent.
_PLACEHOLDER_CONTENT_HASH = "sha256:" + "0" * 64

#: See the module docstring's "confidence: always 1.0" section.
_DETERMINISTIC_CONFIDENCE = 1.0

_NUMERIC_REVIEWER_ROLE = "deterministic numeric verification tool (MRR-FR-073)"
_SOURCE_REVIEWER_ROLE = "deterministic source verification tool (MRR-FR-072)"


def recommendation_for_numeric_outcome(recomputation: NumericRecomputation) -> Recommendation:
    """The numeric half of this module's documented, TOTAL tool-outcome ->
    Recommendation policy — see the module docstring. Exhaustive over
    ``NumericRecomputation``'s own invariant (either ``impossible_reason``
    is set, or ``matches_claimed_value`` is not ``None`` — never neither,
    enforced by that contract's own validator).
    """
    if recomputation.impossible_reason is not None:
        return "inconclusive"
    return "pass" if recomputation.matches_claimed_value else "fail"


def recommendation_for_anchor_status(status: AnchorValidationStatus) -> Recommendation:
    """The source half of this module's documented, TOTAL tool-outcome ->
    Recommendation policy — see the module docstring. Exhaustive over
    ``AnchorValidationStatus``'s exact three values.
    """
    if status == "validated":
        return "pass"
    if status == "unvalidated":
        return "inconclusive"
    if status == "invalid":
        return "fail"
    raise ValueError(f"unknown AnchorValidationStatus {status!r}")  # pragma: no cover — exhaustive


def _default_id_factory() -> str:
    return new_urn("verification")


def _default_clock() -> datetime:
    return datetime.now(UTC)


def _numeric_rationale(
    *, operation: str, recomputation: NumericRecomputation, recommendation: Recommendation
) -> str:
    if recomputation.impossible_reason is not None:
        return (
            f"Numeric recomputation for operation {operation!r} is impossible: "
            f"{recomputation.impossible_reason}"
        )
    verdict = "matches" if recomputation.matches_claimed_value else "does not match"
    return (
        f"Numeric recomputation for operation {operation!r} yielded "
        f"{recomputation.recomputed_value!r}, which {verdict} the claimed value — "
        f"recommendation {recommendation!r}."
    )


def _source_rationale(*, outcome: SourceVerificationOutcome, recommendation: Recommendation) -> str:
    return (
        f"Evidence anchor validation status {outcome.anchor_validation_status!r} "
        f"(source-access outcome {outcome.source_access_outcome!r}): {outcome.reason} — "
        f"recommendation {recommendation!r}."
    )


def _assemble(
    *,
    id_: str,
    practice_id: Urn,
    created_at: datetime,
    created_by: Urn,
    target_id: Urn,
    target_kind: TargetKind,
    reviewer_id: Urn,
    reviewer_role: str,
    independence_profile: IndependenceProfile,
    verification_type: VerificationType,
    checks_performed: list[str],
    evidence_inspected: list[Urn],
    numeric_recomputation: NumericRecomputation | None,
    findings: list[Finding],
    recommendation: Recommendation,
    rationale: str,
    conflicts_of_interest: list[str],
    adjudication_relation: Urn | None,
) -> VerificationResult:
    """Mint the BaseObject envelope and assemble a full, schema-valid
    ``VerificationResult`` — the shared tail end of both
    :func:`build_numeric_verification_result` and
    :func:`build_source_verification_result`. See the module docstring's
    "Minting the envelope" section.
    """
    draft = VerificationResult(
        id=id_,
        api_version=_API_VERSION,
        kind="VerificationResult",
        practice_id=practice_id,
        revision=1,
        created_at=created_at,
        created_by=created_by,
        content_hash=_PLACEHOLDER_CONTENT_HASH,
        target_id=target_id,
        target_kind=target_kind,
        reviewer_id=reviewer_id,
        reviewer_role=reviewer_role,
        independence_profile=independence_profile,
        verification_type=verification_type,
        checks_performed=checks_performed,
        evidence_inspected=evidence_inspected,
        numeric_recomputation=numeric_recomputation,
        findings=findings,
        recommendation=recommendation,
        confidence=_DETERMINISTIC_CONFIDENCE,
        rationale=rationale,
        conflicts_of_interest=conflicts_of_interest,
        adjudication_relation=adjudication_relation,
    )
    body = draft.model_dump(mode="json", exclude_none=True)
    body["content_hash"] = compute_content_hash(body)
    return VerificationResult.model_validate(body)


def build_numeric_verification_result(
    *,
    target_id: Urn,
    target_kind: TargetKind,
    reviewer_id: Urn,
    independence_profile: IndependenceProfile,
    practice_id: Urn,
    operation: str,
    claimed_value: NumberLike,
    inputs: Mapping[str, NumberLike],
    tolerance: NumberLike | None = None,
    method: str | None = None,
    created_by: Urn | None = None,
    findings: list[Finding] | None = None,
    conflicts_of_interest: list[str] | None = None,
    adjudication_relation: Urn | None = None,
    id_factory: Callable[[], str] = _default_id_factory,
    clock: Callable[[], datetime] = _default_clock,
) -> VerificationResult:
    """Recompute a claimed numeric value
    (:func:`mrr.services.verifier.numeric.recompute_numeric_claim`), map the
    outcome to a ``Recommendation``
    (:func:`recommendation_for_numeric_outcome`), and assemble a full,
    schema-valid ``verification_type == "numeric"`` ``VerificationResult``.

    Args:
        target_id: the claim/run/artifact this verification is about —
            caller-supplied; this function does not fetch or persist it.
        target_kind: which kind ``target_id`` names.
        reviewer_id: the identity this verification is recorded under (this
            checked tool's own agent/service identity, or the identity of
            whichever role invoked it) — MRR-FR-070's self-verification gate
            is enforced later, by ``VerificationService.record``, not here.
        independence_profile: the caller-DECLARED MRR-FR-071 profile,
            carried straight onto the built object — this function computes
            no independence verdict of its own (``mrr.domain.independence``,
            E3-T05, is a separate, reused concern).
        practice_id: the practice minting this ``VerificationResult``.
        operation, claimed_value, inputs, tolerance, method: forwarded
            verbatim to ``recompute_numeric_claim``.
        created_by: defaults to ``reviewer_id`` (the reviewer records its own
            verification) if not given.
        findings, conflicts_of_interest, adjudication_relation:
            caller-suppliable context; default to empty/``None`` (see the
            module docstring — this function never invents a ``Finding`` of
            its own).
        id_factory, clock: injectable for deterministic testing (see the
            module docstring's "Minting the envelope" section); default to
            fresh identity/the real wall clock in production.

    Returns:
        A full, schema-valid ``VerificationResult`` with
        ``verification_type == "numeric"``, its ``numeric_recomputation``
        set, and ``evidence_inspected`` empty (no source anchor is inspected
        by a numeric verification). PERSISTS NOTHING — the caller records
        the returned object via
        ``mrr.services.verification.service.VerificationService.record``.
    """
    recomputation = recompute_numeric_claim(
        operation=operation,
        claimed_value=claimed_value,
        inputs=inputs,
        tolerance=tolerance,
        method=method,
    )
    recommendation = recommendation_for_numeric_outcome(recomputation)
    rationale = _numeric_rationale(
        operation=operation, recomputation=recomputation, recommendation=recommendation
    )
    reviewer = reviewer_id
    return _assemble(
        id_=id_factory(),
        practice_id=practice_id,
        created_at=clock(),
        created_by=created_by or reviewer,
        target_id=target_id,
        target_kind=target_kind,
        reviewer_id=reviewer,
        reviewer_role=_NUMERIC_REVIEWER_ROLE,
        independence_profile=independence_profile,
        verification_type="numeric",
        checks_performed=[
            f"Recomputed the claimed value using operation {operation!r} over named inputs."
        ],
        evidence_inspected=[],
        numeric_recomputation=recomputation,
        findings=findings or [],
        recommendation=recommendation,
        rationale=rationale,
        conflicts_of_interest=conflicts_of_interest or [],
        adjudication_relation=adjudication_relation,
    )


def build_source_verification_result(
    *,
    target_id: Urn,
    target_kind: TargetKind,
    reviewer_id: Urn,
    independence_profile: IndependenceProfile,
    practice_id: Urn,
    anchor: EvidenceAnchor,
    local_text_artifact: LocalTextArtifact | None = None,
    local_computational_artifact: LocalComputationalArtifact | None = None,
    created_by: Urn | None = None,
    findings: list[Finding] | None = None,
    conflicts_of_interest: list[str] | None = None,
    adjudication_relation: Urn | None = None,
    id_factory: Callable[[], str] = _default_id_factory,
    clock: Callable[[], datetime] = _default_clock,
) -> VerificationResult:
    """Validate a cited source's evidence anchor
    (:func:`mrr.services.verifier.source.validate_evidence_anchor`), map the
    outcome to a ``Recommendation``
    (:func:`recommendation_for_anchor_status`), and assemble a full,
    schema-valid ``verification_type == "source"`` ``VerificationResult``.

    Args:
        target_id, target_kind, reviewer_id, independence_profile,
            practice_id: see :func:`build_numeric_verification_result`.
        anchor: the ``EvidenceAnchor`` being validated by LOCAL inspection
            only (MRR-FR-072) — its own ``id`` becomes this result's sole
            ``evidence_inspected`` entry (the contract's own validator
            requires at least one for ``verification_type == "source"``).
        local_text_artifact, local_computational_artifact: the caller's own
            locally available artifact content — see
            ``mrr.services.verifier.source``'s module docstring for the full
            local-inspection contract; only the one matching
            ``anchor.anchor_kind`` need be supplied.
        created_by, findings, conflicts_of_interest, adjudication_relation,
            id_factory, clock: see :func:`build_numeric_verification_result`.

    Returns:
        A full, schema-valid ``VerificationResult`` with
        ``verification_type == "source"``, ``numeric_recomputation`` unset,
        and ``evidence_inspected == [anchor.id]``. PERSISTS NOTHING.
    """
    outcome = validate_evidence_anchor(
        anchor,
        local_text_artifact=local_text_artifact,
        local_computational_artifact=local_computational_artifact,
    )
    recommendation = recommendation_for_anchor_status(outcome.anchor_validation_status)
    rationale = _source_rationale(outcome=outcome, recommendation=recommendation)
    reviewer = reviewer_id
    return _assemble(
        id_=id_factory(),
        practice_id=practice_id,
        created_at=clock(),
        created_by=created_by or reviewer,
        target_id=target_id,
        target_kind=target_kind,
        reviewer_id=reviewer,
        reviewer_role=_SOURCE_REVIEWER_ROLE,
        independence_profile=independence_profile,
        verification_type="source",
        checks_performed=["Locally inspected the cited source and validated the evidence anchor."],
        evidence_inspected=[anchor.id],
        numeric_recomputation=None,
        findings=findings or [],
        recommendation=recommendation,
        rationale=rationale,
        conflicts_of_interest=conflicts_of_interest or [],
        adjudication_relation=adjudication_relation,
    )


__all__ = [
    "build_numeric_verification_result",
    "build_source_verification_result",
    "recommendation_for_anchor_status",
    "recommendation_for_numeric_outcome",
]
