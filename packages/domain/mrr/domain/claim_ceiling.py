"""The claim-ceiling gate (task-packets/K1-T02.yaml, MRR-MTH-004/005/006): a
pure, framework-free decision function over already-resolved string values —
no persistence, no I/O, no provider import (MRR-NFR-010; enforced by the
import-linter contract in pyproject.toml). The closest template is
``mrr.domain.independence`` ("pure decision logic over an already-validated
value, no persistence, no I/O").

--- The narrow, honestly-disclosed mapping this module implements -----------

``Claim.claim_type`` (``observational``/``causal``/``statistical``/
``methodological``/``interpretive``/``normative``/``speculative``,
docs/spec/02_DOMAIN_MODEL.md section 2.11) and ``ClaimCeiling``
(``insufficient_evidence``/``mechanism_hypothesis``/``descriptive``/
``associational_unadjusted``/``associational_adjusted``/``causal_local``/
``causal_bounded``, docs/spec/08_RESEARCH_METHOD_KERNEL.md section 4) are two
DIFFERENT, orthogonal vocabularies — the former names the KIND of claim, the
latter the STRENGTH of language a ruling licenses. Spec 08 section 4's own
text ("the claim service MUST reject claim language above the ruled
ceiling") presumes some way to read a claim's own "language" against the
ceiling ladder, but neither spec 08 nor the ``Claim`` contract defines that
mapping. This module implements exactly TWO checks, both fully
machine-checkable with data already on hand — see
task-packets/K1-T02.yaml derived_decisions for the full derivation and its
disclosed limits:

1. **Universal** (every ``claim_type``): the attached ``MethodRuling.
   ruled_ceiling`` MUST NOT exceed its governing ``MethodProfile.
   max_claim_ceiling``, per ``CLAIM_CEILING_ORDER``'s declared order
   (MRR-MTH-003's own text, made structurally true for
   ``systematic_evidence_synthesis`` — spec 08 section 5's own committed
   ``max_claim_ceiling`` of ``associational_unadjusted`` — which is exactly
   what makes MRR-MTH-006 hold for the first real profile). This does NOT
   verify that a profile claiming to be "a synthesis or descriptive design"
   (MTH-006's own antecedent) actually IS one — ``MethodProfile.
   protocol_form`` is free text, not a closed taxonomy — it enforces "no
   ruling exceeds ITS OWN profile's declared max," not "this profile's
   declared max is itself correct for its design kind."

2. **Causal-specific** (only ``claim_type == "causal"``): the attached
   ``ruled_ceiling`` MUST be one of ``{"causal_local", "causal_bounded"}`` —
   the only two ceiling tiers whose NAME denotes causal language. Every
   other ``claim_type`` value is subject to check 1 ONLY — no
   claim-type-specific floor/ceiling correspondence is implemented for any
   of the other six values, since spec 08 gives no textual basis for one and
   inventing a full 7x7 mapping now, with no real profile driving the need,
   risks encoding made-up epistemology the spec never asked for. Flagged for
   reviewer confirmation before task-packets/K1-T03.yaml builds against it.

--- MRR-MTH-005: satisfied structurally, not by policy prose ----------------

"Statistical significance, model confidence, or output fluency MUST NOT
raise a ceiling" is proven, not merely asserted: ``ceiling_violation_reason``'s
own signature is exactly ``(*, claim_type: str, ruled_ceiling: str,
profile_max_ceiling: str) -> str | None`` — keyword-only, three parameters,
no ``**kwargs``, no confidence/significance/fluency-shaped parameter
anywhere. Nothing could enter this decision even if a caller wanted it to.
``tests/unit/domain/test_claim_ceiling.py`` proves this via
``inspect.signature`` introspection, not merely by reading the source.

--- Three enforcement checkpoints (MRR-MTH-004: "at submission and at ----
--- projection rendering") --------------------------------------------------

This module implements only the pure decision. It is wired at three
checkpoints, each documented at its own call site:

- ``mrr.services.claim.service.ClaimService.attach_ruling`` — the "at
  submission" gate: the ONLY place a ``ruled_by`` edge is ever written.
- ``ClaimService._transition``'s ceiling re-check — defense in depth,
  re-verifying every attached ``ruled_by`` chain whenever an already-ruled
  claim moves into a language-asserting status.
- ``mrr.domain.projection.build_claim_table_row`` (via its two new optional
  keyword parameters) plus ``mrr.services.projection.service.
  ProjectionService.build_claim_table`` — the "at ... projection rendering"
  half, re-deriving the same verdict at render time rather than trusting the
  claim's own stored status.
"""

from __future__ import annotations

from mrr.contracts.method_profile import CLAIM_CEILING_ORDER

#: MRR-MTH-004's causal-specific check: the only two ``ClaimCeiling`` tiers
#: whose name denotes causal language, per ``CLAIM_CEILING_ORDER``'s own
#: ordering (both strictly above ``associational_adjusted``).
_CAUSAL_LICENSING_CEILINGS = frozenset({"causal_local", "causal_bounded"})

#: The one ``Claim.claim_type`` value the causal-specific check applies to.
_CAUSAL_CLAIM_TYPE = "causal"


def ceiling_violation_reason(
    *, claim_type: str, ruled_ceiling: str, profile_max_ceiling: str
) -> str | None:
    """``None`` iff both the universal and (when applicable) the
    causal-specific ceiling checks pass; otherwise a human-readable reason
    string naming exactly which check failed. See the module docstring for
    the full derivation of both checks and their disclosed limits.

    Pure and deterministic: depends only on the three arguments, performs no
    I/O, and imports no persistence, provider, or framework module
    (MRR-NFR-010). The universal check is evaluated first — a claim whose
    ruling violates BOTH checks simultaneously (e.g. a causal claim ruled
    above its own profile's max AND ruled to a non-causal ceiling) reports
    the universal violation, since it is the more fundamental of the two
    ("no ruling may exceed its own profile's declared maximum" applies
    regardless of claim_type).

    Raises:
        ValueError: ``ruled_ceiling`` or ``profile_max_ceiling`` is not one
            of ``CLAIM_CEILING_ORDER``'s seven declared values — a caller
            error (both values originate from already schema-validated
            ``MethodRuling.ruled_ceiling``/``MethodProfile.max_claim_ceiling``
            fields in every real caller), not a case this gate silently
            passes.
    """
    if ruled_ceiling not in CLAIM_CEILING_ORDER:
        raise ValueError(f"ruled_ceiling {ruled_ceiling!r} is not one of {CLAIM_CEILING_ORDER!r}")
    if profile_max_ceiling not in CLAIM_CEILING_ORDER:
        raise ValueError(
            f"profile_max_ceiling {profile_max_ceiling!r} is not one of {CLAIM_CEILING_ORDER!r}"
        )

    ruled_rank = CLAIM_CEILING_ORDER.index(ruled_ceiling)
    profile_max_rank = CLAIM_CEILING_ORDER.index(profile_max_ceiling)

    if ruled_rank > profile_max_rank:
        return (
            f"ruled_ceiling {ruled_ceiling!r} exceeds the governing profile's own "
            f"max_claim_ceiling {profile_max_ceiling!r} (MRR-MTH-003/006)"
        )

    if claim_type == _CAUSAL_CLAIM_TYPE and ruled_ceiling not in _CAUSAL_LICENSING_CEILINGS:
        return (
            f"claim_type {claim_type!r} requires ruled_ceiling to be one of "
            f"{sorted(_CAUSAL_LICENSING_CEILINGS)!r}, got {ruled_ceiling!r} (MRR-MTH-004)"
        )

    return None
