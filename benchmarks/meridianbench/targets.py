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

__all__ = [
    "FALSE_SUPPORT_ON_MB_CIT_COMPARATOR",
    "FALSE_SUPPORT_ON_MB_CIT_TARGET",
    "NUMERIC_VERIFICATION_ACCURACY_COMPARATOR",
    "NUMERIC_VERIFICATION_ACCURACY_TARGET",
    "VALID_CITATION_ANCHOR_RESOLUTION_COMPARATOR",
    "VALID_CITATION_ANCHOR_RESOLUTION_TARGET",
    "TargetComparator",
]
