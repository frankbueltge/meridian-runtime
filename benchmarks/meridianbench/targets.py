"""The docs/spec/05_EVALUATION_AND_ACCEPTANCE.md section 4 "Initial
calibrated targets", declared once as named constants for
``promotion.decide_promotion``.

These are, per section 4's own words, "provisional performance targets, not
immutable constitutional truths. They must be updated from baseline
measurements through an ADR." Each constant below is individually
ADR-updatable — a future ADR changes the number here, not the comparator or
the promotion policy shape in ``promotion.py``. Only the three targets this
task's two populated suites (MB-NUM, MB-CIT) can actually measure are
declared; section 4's other rows (source-family F1, correction recall, ...)
belong to benchmark families this task does not populate (see this task's
"open specification questions").
"""

from __future__ import annotations

from typing import Final, Literal

#: docs/spec/05 section 4: "Numeric verification accuracy | >= 0.95".
#: ADR-updatable — change this constant only via an accepted ADR.
NUMERIC_VERIFICATION_ACCURACY_TARGET: Final[float] = 0.95

#: docs/spec/05 section 4: "Valid citation-anchor resolution | >= 0.95".
#: ADR-updatable — change this constant only via an accepted ADR.
VALID_CITATION_ANCHOR_RESOLUTION_TARGET: Final[float] = 0.95

#: docs/spec/05 section 4: "False support on MB-CIT | <= 0.02".
#: ADR-updatable — change this constant only via an accepted ADR.
FALSE_SUPPORT_ON_MB_CIT_TARGET: Final[float] = 0.02

#: The comparator each target above is checked with — section 4's own table
#: notation (">=" for the first two, "<=" for false support). Exposed
#: alongside the constants so ``promotion.py`` never hardcodes which
#: direction is "better" for a given target name.
TargetComparator = Literal[">=", "<="]

NUMERIC_VERIFICATION_ACCURACY_COMPARATOR: Final[TargetComparator] = ">="
VALID_CITATION_ANCHOR_RESOLUTION_COMPARATOR: Final[TargetComparator] = ">="
FALSE_SUPPORT_ON_MB_CIT_COMPARATOR: Final[TargetComparator] = "<="

# --- MB-CLS: the gold-standard classification targets -----------------------
#
# These three are `None`, and that is now a stronger statement than it was.
#
# The owner decided on 2026-08-01 that both the gold labels AND the thresholds
# for MB-CLS are set by an ENCOUNTER with another practice. The practice asked
# — Ulysses, The Atelier — answered on the same day, and REFUSED to supply
# three numbers. Its answer is recorded in
# docs/design/2026-08-01-antwort-ulysses-und-was-daraus-folgt.md; the reasoning
# is technical, not evasive, and it is why these constants must not be filled
# in with plain floats even once the labels arrive:
#
#   * KAPPA cannot take a floor fixed before the marginals are known.
#     Feinstein and Cicchetti (1990) showed high raw agreement can be driven to
#     a low kappa by marginal imbalance, and that kappa moves with the ASYMMETRY
#     of that imbalance. With four labels over sixty cases and an unknown class
#     distribution, a kappa floor is partly a statement about the corpus's
#     balance rather than about a classifier. A floor must therefore be stated
#     CONDITIONAL on the realized marginals and reported beside them.
#
#   * MACRO F1 over four classes on sixty cases cannot carry two decimals —
#     each class rests on roughly five to twenty cases. The target is an
#     INTERVAL published next to the per-class counts, not a point.
#
#   * FALSE SUPPORT should not be a rate at all. Ulysses' own words: a rate
#     needs a denominator that someone downstream will drop; a rule carries its
#     own warrant into every place it travels. Their proposal, accepted:
#     A `supports` THAT AN INDEPENDENT BLIND READER DOES NOT ALSO CALL
#     `supports` DOES NOT COUNT TOWARD THE CAP.
#     That is a governance rule about corroboration counting, not a benchmark
#     threshold, and implementing it touches the synthesis executor — a
#     separate packet, named here so the next builder finds it rather than
#     reinventing a rate.
#
# So the honest state is not "waiting for three numbers". It is: the shape of
# these three targets was refused by the practice entitled to set them, and the
# constructions above replace them. `decide_gold_classification_promotion`
# therefore still returns `hold`, and the reason it gives says which of the two
# it is.
#
# Ulysses' own summary, which this file agrees with: "if you need three numbers
# today, my answer is that you should not have three numbers today."

#: Chance-corrected agreement with the gold standard (Cohen's kappa). Accuracy
#: alone is not a target: with a 50% majority class, a constant classifier
#: reaches 0.50 while having learned nothing.
GOLD_CLASSIFICATION_KAPPA_TARGET: Final[float | None] = None

#: Macro-averaged F1 across the declared categories, so a classifier cannot
#: reach the target by being good at the majority class and hopeless at the
#: rest.
GOLD_CLASSIFICATION_MACRO_F1_TARGET: Final[float | None] = None

#: The share of non-`supports` gold items read as `supports`. The asymmetric
#: one, and the one that matters most: over-predicting `supports` inflates
#: corroboration and lifts the ceiling meant to cap what a claim may say.
FALSE_SUPPORT_ON_MB_CLS_TARGET: Final[float | None] = None

GOLD_CLASSIFICATION_KAPPA_COMPARATOR: Final[TargetComparator] = ">="
GOLD_CLASSIFICATION_MACRO_F1_COMPARATOR: Final[TargetComparator] = ">="
FALSE_SUPPORT_ON_MB_CLS_COMPARATOR: Final[TargetComparator] = "<="

#: The reason a check carries when its target has not been set yet. Distinct
#: from a measured failure on purpose: "nobody has said what good means" and
#: "this was measured and fell short" are different states, and collapsing them
#: into one generic failure is the kind of shortcut AGENTS.md's own prohibited
#: list names ("collapsing unknown, not_found, contradicted, and failed into one
#: generic error").
NO_THRESHOLD_SET_REASON: Final[str] = (
    "no threshold of this shape will be set: the practice entitled to set it refused a bare "
    "number and specified a construction instead (kappa conditional on the realized marginals; "
    "macro F1 as an interval beside the per-class counts; false support as a rule, not a rate). "
    "See targets.py's own MB-CLS section. This check cannot pass until the construction is "
    "implemented — it is not waiting for someone to type a float."
)

#: The corroboration rule Ulysses proposed in place of a false-support rate, on
#: 2026-08-01, and which Meridian accepted. Recorded here rather than in prose
#: alone because it is the one part of their answer that changes how a CLAIM is
#: capped, not merely how a classifier is scored — and the place a future
#: builder will look for it is beside the target it replaced.
#:
#: Implementing it belongs to a packet that may touch
#: ``mrr.services.node_runtime.synthesis_executor``; N1-T02 forbids that path,
#: deliberately, so this constant is a pointer and not a switch.
CORROBORATION_RULE_FROM_THE_ENCOUNTER: Final[str] = (
    "A `supports` that an independent blind reader does not also call `supports` does not count "
    "toward the corroboration cap."
)

__all__ = [
    "FALSE_SUPPORT_ON_MB_CIT_COMPARATOR",
    "FALSE_SUPPORT_ON_MB_CIT_TARGET",
    "FALSE_SUPPORT_ON_MB_CLS_COMPARATOR",
    "FALSE_SUPPORT_ON_MB_CLS_TARGET",
    "GOLD_CLASSIFICATION_KAPPA_COMPARATOR",
    "GOLD_CLASSIFICATION_KAPPA_TARGET",
    "GOLD_CLASSIFICATION_MACRO_F1_COMPARATOR",
    "GOLD_CLASSIFICATION_MACRO_F1_TARGET",
    "CORROBORATION_RULE_FROM_THE_ENCOUNTER",
    "NO_THRESHOLD_SET_REASON",
    "NUMERIC_VERIFICATION_ACCURACY_COMPARATOR",
    "NUMERIC_VERIFICATION_ACCURACY_TARGET",
    "VALID_CITATION_ANCHOR_RESOLUTION_COMPARATOR",
    "VALID_CITATION_ANCHOR_RESOLUTION_TARGET",
    "TargetComparator",
]
