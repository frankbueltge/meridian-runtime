"""The deterministic promotion policy (task-packets/E4-T07.yaml): a PURE
function from a ``MetricsReport`` and this package's ``targets`` to a
promote/hold ``PromotionDecision`` — never a live action.

--- Enacts nothing -----------------------------------------------------------

``decide_promotion`` reads its arguments and returns a value; it opens no
database connection, calls no capability registry, flips no feature flag,
and mutates no module-level state. Running it twice with identical inputs
returns two equal (by value) ``PromotionDecision``s — the same call, run
again, is a no-op with respect to anything outside this function's own return
value (docs/spec/06_IMPLEMENTATION_PLAN.md section 5.6, "capability
promotion", is a SEPARATE, gated action this function does not perform;
AGENTS.md rule 15: "sealing ... is not permission to ... mutate ... the live
... system"). A caller that wants an actual promotion to happen must take
some other, explicitly gated action with this decision as its INPUT — that
action does not live in this package.

--- Reversible and version-attributed ---------------------------------------

Every ``PromotionDecision`` carries the ``EvaluationProfile`` it was computed
under (docs/spec/05 section 8's "frozen evaluation profiles": exact
model/profile id, prompt version, fixture-set id) and a per-target
``TargetCheck`` for each of the three targets this task's suites measure —
"reversible" here means the decision is fully determined by, and fully
traceable back to, those recorded inputs: nothing about it depends on prior
decisions, hidden state, or wall-clock time.

--- A metric a target cannot evaluate fails closed --------------------------

A ``MetricsReport`` field left ``None`` (a suite that was not run) makes its
corresponding ``TargetCheck.passed`` ``False`` — never silently skipped and
never counted as passing by default. ``decide_promotion`` promotes if and
only if every target check passes; a single missing measurement is exactly
as disqualifying as a measured failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from benchmarks.meridianbench.targets import (
    FALSE_SUPPORT_ON_MB_CIT_COMPARATOR,
    FALSE_SUPPORT_ON_MB_CIT_TARGET,
    FALSE_SUPPORT_ON_MB_CLS_COMPARATOR,
    FALSE_SUPPORT_ON_MB_CLS_TARGET,
    GOLD_CLASSIFICATION_KAPPA_COMPARATOR,
    GOLD_CLASSIFICATION_KAPPA_TARGET,
    GOLD_CLASSIFICATION_MACRO_F1_COMPARATOR,
    GOLD_CLASSIFICATION_MACRO_F1_TARGET,
    NO_THRESHOLD_SET_REASON,
    NUMERIC_VERIFICATION_ACCURACY_COMPARATOR,
    NUMERIC_VERIFICATION_ACCURACY_TARGET,
    VALID_CITATION_ANCHOR_RESOLUTION_COMPARATOR,
    VALID_CITATION_ANCHOR_RESOLUTION_TARGET,
    TargetComparator,
)

#: A promotion decision's own outcome vocabulary. Deliberately narrower than
#: docs/spec/05 section 9's five dual-run outcomes
#: (``promote_mrr_capability``/``retain_classic_capability``/
#: ``combine_capabilities``/``continue_dual_run``/``inconclusive``) — this
#: policy answers a narrower question (do MB-NUM/MB-CIT meet their initial
#: calibrated targets), not the full Classic/MRR dual-run adjudication
#: section 9 describes, which is out of this task's scope.
PromotionOutcome = Literal["promote", "hold"]


@dataclass(frozen=True, slots=True, kw_only=True)
class EvaluationProfile:
    """docs/spec/05 section 8's "frozen evaluation profile", attributed
    exactly: which system/model was evaluated, under which prompt version,
    against which fixture set. Plain identifying strings — never a live
    reference to a running model or capability.
    """

    system_id: str
    prompt_version: str
    fixture_set_id: str

    def __post_init__(self) -> None:
        for field_name in ("system_id", "prompt_version", "fixture_set_id"):
            if not getattr(self, field_name):
                raise ValueError(f"{field_name} must not be empty")


@dataclass(frozen=True, slots=True, kw_only=True)
class MetricsReport:
    """The metrics a promotion decision is evaluated against. ``None`` means
    that suite was not run for this evaluation profile — never a silent 0.0
    or 1.0 stand-in (see the module docstring's "fails closed" section).
    """

    numeric_accuracy: float | None = None
    valid_anchor_resolution_rate: float | None = None
    false_support_rate: float | None = None

    # --- MB-CLS (task-packets/N1-T02.yaml R5). Same fail-closed rule as the
    #     three above: ``None`` means the suite was not run, and its target
    #     check fails rather than being skipped.
    gold_classification_kappa: float | None = None
    gold_classification_macro_f1: float | None = None
    false_support_rate_mb_cls: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TargetCheck:
    """One target's pass/fail, alongside the exact target value and
    comparator it was checked against — enough to reconstruct the whole
    decision from this record alone, without re-reading ``targets.py``.

    ``target_value`` is ``None`` when no threshold has been set for this
    target yet, and ``reason`` then says so. That is a THIRD state, distinct
    from both "measured and passed" and "measured and fell short": nobody has
    yet said what good means. It fails, like every other non-pass — but it
    fails legibly, because a decision that cannot distinguish "we fell short"
    from "no one set a bar" is not a decision anyone can act on.
    """

    name: str
    metric_value: float | None
    target_value: float | None
    comparator: TargetComparator
    passed: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class PromotionDecision:
    """The full, deterministic, reversible promotion decision: an outcome,
    every target's own pass/fail, and the evaluation profile it was computed
    under. Producing this object enacts nothing — see the module docstring.
    """

    outcome: PromotionOutcome
    target_checks: tuple[TargetCheck, ...]
    evaluation_profile: EvaluationProfile


def _check_target(
    *,
    name: str,
    metric_value: float | None,
    target_value: float | None,
    comparator: TargetComparator,
) -> TargetCheck:
    reason: str | None = None
    if target_value is None:
        # No threshold has been set for this target yet. It fails — never
        # passes by default, never silently disappears from the decision.
        passed = False
        reason = NO_THRESHOLD_SET_REASON
    elif metric_value is None:
        passed = False
        reason = "not measured: the suite producing this metric was not run"
    elif comparator == ">=":
        passed = metric_value >= target_value
    else:
        passed = metric_value <= target_value
    return TargetCheck(
        name=name,
        metric_value=metric_value,
        target_value=target_value,
        comparator=comparator,
        passed=passed,
        reason=reason,
    )


def decide_promotion(
    metrics: MetricsReport, evaluation_profile: EvaluationProfile
) -> PromotionDecision:
    """The promotion policy itself: promote iff every one of the three
    docs/spec/05 section 4 targets this task's suites measure is met at or
    past its comparator boundary (a metric exactly at its target passes —
    section 4's own ">="/"<=" notation, never a strict inequality); hold
    otherwise. Pure and side-effect-free — see the module docstring.
    """
    target_checks = (
        _check_target(
            name="numeric_verification_accuracy",
            metric_value=metrics.numeric_accuracy,
            target_value=NUMERIC_VERIFICATION_ACCURACY_TARGET,
            comparator=NUMERIC_VERIFICATION_ACCURACY_COMPARATOR,
        ),
        _check_target(
            name="valid_citation_anchor_resolution",
            metric_value=metrics.valid_anchor_resolution_rate,
            target_value=VALID_CITATION_ANCHOR_RESOLUTION_TARGET,
            comparator=VALID_CITATION_ANCHOR_RESOLUTION_COMPARATOR,
        ),
        _check_target(
            name="false_support_on_mb_cit",
            metric_value=metrics.false_support_rate,
            target_value=FALSE_SUPPORT_ON_MB_CIT_TARGET,
            comparator=FALSE_SUPPORT_ON_MB_CIT_COMPARATOR,
        ),
    )
    outcome: PromotionOutcome = "promote" if all(c.passed for c in target_checks) else "hold"
    return PromotionDecision(
        outcome=outcome, target_checks=target_checks, evaluation_profile=evaluation_profile
    )


def decide_gold_classification_promotion(
    metrics: MetricsReport, evaluation_profile: EvaluationProfile
) -> PromotionDecision:
    """The MB-CLS promotion policy (task-packets/N1-T02.yaml R5): promote iff
    every one of the three gold-classification targets is met. Pure and
    side-effect-free, exactly like :func:`decide_promotion`.

    --- Why this is a SEPARATE function, and not three more checks in
        ``decide_promotion`` ---------------------------------------------

    It answers a different question. ``decide_promotion`` asks whether the
    numeric verifier and the citation anchoring meet the targets
    docs/spec/05 section 4 set for them. This asks whether a CLASSIFIER is good
    enough to be trusted with an evidence relation — a question that did not
    exist when those targets were written, whose thresholds come from somewhere
    else entirely (an encounter, per the owner's decision of 2026-08-01), and
    whose suite may not have been run at all.

    Folding them together would have a concrete and bad consequence: the
    MB-CLS targets are ``None`` until the encounter sets them, so they fail by
    construction, and a combined policy could therefore never return
    ``promote`` for MB-NUM/MB-CIT again — an unrelated, unset threshold would
    silently veto a fully measured result. Two questions, two decisions.

    With no thresholds set, this function returns ``hold`` with all three
    checks carrying
    :data:`benchmarks.meridianbench.targets.NO_THRESHOLD_SET_REASON`. That is
    the correct answer today, and it is meant to be read as "nobody has said
    what better means yet", not as "the classifier failed".
    """
    target_checks = (
        _check_target(
            name="gold_classification_kappa",
            metric_value=metrics.gold_classification_kappa,
            target_value=GOLD_CLASSIFICATION_KAPPA_TARGET,
            comparator=GOLD_CLASSIFICATION_KAPPA_COMPARATOR,
        ),
        _check_target(
            name="gold_classification_macro_f1",
            metric_value=metrics.gold_classification_macro_f1,
            target_value=GOLD_CLASSIFICATION_MACRO_F1_TARGET,
            comparator=GOLD_CLASSIFICATION_MACRO_F1_COMPARATOR,
        ),
        _check_target(
            name="false_support_on_mb_cls",
            metric_value=metrics.false_support_rate_mb_cls,
            target_value=FALSE_SUPPORT_ON_MB_CLS_TARGET,
            comparator=FALSE_SUPPORT_ON_MB_CLS_COMPARATOR,
        ),
    )
    outcome: PromotionOutcome = "promote" if all(c.passed for c in target_checks) else "hold"
    return PromotionDecision(
        outcome=outcome, target_checks=target_checks, evaluation_profile=evaluation_profile
    )


__all__ = [
    "EvaluationProfile",
    "MetricsReport",
    "PromotionDecision",
    "PromotionOutcome",
    "TargetCheck",
    "decide_gold_classification_promotion",
    "decide_promotion",
]
