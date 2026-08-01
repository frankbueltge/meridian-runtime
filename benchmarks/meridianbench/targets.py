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
# These three are `None`, and that is the whole point (task-packets/
# N1-T02.yaml R5 / derived_decisions (e)).
#
# The three constants above came from docs/spec/05 section 4 — from outside
# whoever was being measured. These three come from somewhere equally outside,
# but not from the specification: the owner decided on 2026-08-01 that both the
# gold labels AND the thresholds for MB-CLS are set by an ENCOUNTER with
# another practice via The Middle. Not by Meridian, whose classification is
# what gets measured; not by the builder of this file.
#
# Until that encounter delivers numbers, they stay `None`, and
# `promotion.decide_promotion` FAILS their checks with a stated reason rather
# than passing them by default. A provisional default here would let a
# self-modification report success against a threshold nobody set — which is
# exactly the "practice that agrees with itself" the whole ordering exists to
# prevent (docs/design/2026-07-24-primaerquellen-selbstoptimierung.md: the
# documented failure mode is the optimiser attacking its own evaluator).
#
# Setting them is therefore an act of the encounter, recorded as such — not a
# code change a builder may make on their own judgement.

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
    "no threshold set by an encounter yet — this check cannot pass until one is"
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
    "NO_THRESHOLD_SET_REASON",
    "NUMERIC_VERIFICATION_ACCURACY_COMPARATOR",
    "NUMERIC_VERIFICATION_ACCURACY_TARGET",
    "VALID_CITATION_ANCHOR_RESOLUTION_COMPARATOR",
    "VALID_CITATION_ANCHOR_RESOLUTION_TARGET",
    "TargetComparator",
]
