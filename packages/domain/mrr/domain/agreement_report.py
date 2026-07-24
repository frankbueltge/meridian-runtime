"""Pure, framework-free agreement-report projection (task-packets/N1-T01.yaml
R2). Mirrors ``mrr.domain.research_report``'s own split precedent (pure
model-building/rendering here; ``mrr.services.validation.service
.ValidationService`` performs every I/O step and hands this module
already-loaded values) and, for the Pydantic base, ``mrr.contracts.common
.MRRModel`` (``extra="forbid"``) — the SAME closure discipline
``mrr.domain`` already imports specific ``mrr.contracts`` types for
elsewhere (e.g. ``mrr.domain.claim_ceiling``'s ``CLAIM_CEILING_ORDER``,
``mrr.domain.task_trust``'s ``TaskBundle``), so importing ``MRRModel`` itself
here is the same established, non-circular pattern (``mrr.contracts.common``
imports ``mrr.domain.identity``/``mrr.crypto.hashing``, neither of which
imports anything from this module).

--- NOT a BaseObject, NOT a persisted object (task-packets/N1-T01.yaml R2) --

``AgreementReport`` and every nested model here are pure Pydantic
projections: no ``id``/``api_version``/``revision``/``content_hash``/audit
fields, no ``schemas/*.schema.json`` mirror, and this report is NEVER passed
to ``ObjectRepository.insert_revision`` or any other persistence call
anywhere in this packet (``mrr.services.validation.service.ValidationService``
opens no database connection at all — see that module's own docstring).
Matches ``mrr.domain.research_report.ResearchReport``'s identical "a
projection, not the primary research record" stance (AGENTS.md rule 7 / "no
model output may directly become authoritative state" combined with the
source-of-truth discipline's "narrative reports are projections").

--- The honesty header is structural, not advisory (R2) ---------------------

``measures_reliability_not_validity`` is a ``Literal[True]`` — it cannot be
constructed as ``False`` even by a caller mistake, because there is no other
value the type permits. ``reliability_note`` and ``reference_rater_note`` are
non-empty, fixed-content strings assembled by :func:`build_stratum_report`/
:func:`build_agreement_report` from the SAME two module-level constant
templates every report uses (:data:`_RELIABILITY_NOTE_TEMPLATE`/callers'
own ``reference_rater``) — never freehand text a caller could omit or drift.
The blind pass (K1-T06) is an independent INSTANCE, not a human gold
standard: this report measures inter-instance reproducibility only, and
validity against human labels is out of scope here (deferred, named, to
N1-T02 — task-packets/N1-T01.yaml specification_gaps).

--- No pooled cross-stratum kappa, structurally (R2/invariants) -------------

There is no field anywhere on :class:`AgreementReport` that could hold a
pooled kappa — not an ``Optional`` one, not one that happens to always be
``None``. The ONLY place "pooling" is mentioned at all is
:data:`AgreementReport.pooled_note`, a fixed explanatory string. This is a
stronger structural guarantee than "a nullable field nobody populates": a
field that COULD hold a pooled number invites a future caller to populate it
one day; the absence of the field cannot.

--- Determinism (task-packets/N1-T01.yaml invariant) ------------------------

:func:`render_markdown`/:func:`render_json` are pure — no wall clock, no
network, no filesystem. Every collection this module iterates is already a
``tuple`` in an explicit, caller/builder-established order (categories in
the crosswalk's own declared order; strata sorted by ``stratum_id``; items
in the crosswalk's own declared order) — never a ``dict``/``set`` iteration
order. Calling either renderer twice with an equal ``AgreementReport``
produces byte-identical output (tests/unit/domain/test_agreement_report.py).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Literal

from mrr.contracts.common import MRRModel

#: The single reason string every "single/no category used" null result in
#: this report carries — re-exported here (rather than re-typed) from
#: ``mrr.domain.agreement`` so callers of this module never need a second
#: import for the identical literal.
from mrr.domain.agreement import (
    UNDEFINED_NO_CHANCE_VARIATION as UNDEFINED_NO_CHANCE_VARIATION,
)
from mrr.domain.agreement import (
    AlphaResult,
    CategoryPrf,
    KappaResult,
)
from pydantic import Field

#: task-packets/N1-T01.yaml derived_decisions (g): 20-30 labels/category is
#: the documented minimum for a reliability estimate to be trusted (A4);
#: below it, a headline-perfect kappa/agreement must not be read as
#: publication-grade evidence. Fixed here as the ONE threshold every stratum
#: report is checked against.
BELOW_POWER_THRESHOLD = 20

_RELIABILITY_NOTE = (
    "This report measures RELIABILITY (inter-instance agreement between two "
    "independent classification passes over the same items), NOT VALIDITY "
    "against a human gold standard. The 'blind' rater is an independent "
    "verifying instance (K1-T06), not a human labeler — stable agreement "
    "between two instances is not evidence the shared classification is "
    "correct (Stubborn Consistency: stability is not a validity proof). "
    "Validity against human gold labels is a separate, named, not-yet-built "
    "use case (N1-T02)."
)

_POOLED_NOTE = (
    "No pooled cross-stratum kappa is emitted, on principle: instantiation "
    "(works, n=15) and theory (papers, n=3) are two methodologically "
    "distinct label tasks with two distinct label spaces. Pooling them into "
    "one headline kappa (as comparison.md's own prose '18/18 agreement' "
    "does) mixes two different measurements into one number and is exactly "
    "what this report corrects — see each stratum's own report instead."
)


def _reference_rater_note(reference_rater: str) -> str:
    return (
        f"Per-category precision/recall/F1 and the majority-class baseline "
        f"are computed against {reference_rater!r} as the NAMED reference "
        "rater. Without a human gold standard, this is an asymmetric "
        "convention, not a validity claim: naming the other rater as "
        "reference instead would change which side's marginal grounds "
        "precision/recall, never which classification is 'correct'."
    )


class CategoryPrfRow(MRRModel):
    """One category's precision/recall/F1 against the stratum's named
    reference rater — mirrors ``mrr.domain.agreement.CategoryPrf`` field for
    field (Pydantic projection of that plain dataclass).
    """

    category: str
    support: int = Field(ge=0)
    true_positive: int = Field(ge=0)
    false_positive: int = Field(ge=0)
    false_negative: int = Field(ge=0)
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None
    reason: str | None = None


class ItemAgreementRow(MRRModel):
    """One aligned item's common-space labels from both raters (task-
    packets/N1-T01.yaml AT1: "the item-by-item common-space labels match
    comparison.md's Agreement column exactly").
    """

    item_id: str
    corpus_entry_id: str
    title: str
    rater_a_label: str
    rater_b_label: str
    agree: bool


class KappaField(MRRModel):
    """A chance-corrected agreement value with its own null-with-reason
    contract, reused for Cohen's kappa and both weighted-kappa variants —
    mirrors ``mrr.domain.agreement.KappaResult`` (Pydantic projection).
    """

    value: float | None
    reason: str | None


class AlphaField(MRRModel):
    """Krippendorff's alpha with its own null-with-reason contract — mirrors
    ``mrr.domain.agreement.AlphaResult``.
    """

    value: float | None
    reason: str | None


def _kappa_field(result: KappaResult) -> KappaField:
    return KappaField(value=result.value, reason=result.reason)


def _alpha_field(result: AlphaResult) -> AlphaField:
    return AlphaField(value=result.value, reason=result.reason)


class StratumReport(MRRModel):
    """One analysis stratum's full agreement report (task-packets/
    N1-T01.yaml R2): the R1 metrics, the below-power flag, and the two
    raters' own ids.
    """

    stratum_id: str
    n: int = Field(ge=0)
    rater_a_id: str
    rater_b_id: str
    reference_rater: str
    categories: tuple[str, ...]
    confusion_matrix: tuple[tuple[int, ...], ...]
    observed_agreement: float = Field(ge=0.0, le=1.0)
    majority_baseline: float = Field(ge=0.0, le=1.0)
    prevalence_a: tuple[tuple[str, int], ...]
    prevalence_b: tuple[tuple[str, int], ...]
    cohen_kappa: KappaField
    weighted_kappa_linear: KappaField
    weighted_kappa_quadratic: KappaField
    krippendorff_alpha: AlphaField
    per_category: tuple[CategoryPrfRow, ...]
    items: tuple[ItemAgreementRow, ...]
    below_power: bool
    below_power_threshold: int = BELOW_POWER_THRESHOLD


class AgreementReport(MRRModel):
    """The full, top-level agreement report (task-packets/N1-T01.yaml R2): a
    fixed honesty header plus an ORDERED list of per-stratum reports
    (sorted by ``stratum_id``) and a ``pooled_note`` explaining why no
    pooled cross-stratum kappa is ever emitted (see the module docstring's
    "No pooled cross-stratum kappa, structurally" section).
    """

    measures_reliability_not_validity: Literal[True] = True
    reliability_note: str = _RELIABILITY_NOTE
    reference_rater: str
    reference_rater_note: str
    crosswalk_path: str
    crosswalk_sha256: str
    strata: tuple[StratumReport, ...]
    pooled_note: str = _POOLED_NOTE


def build_stratum_report(
    *,
    stratum_id: str,
    rater_a_id: str,
    rater_b_id: str,
    reference_rater: str,
    categories: Sequence[str],
    confusion_matrix: tuple[tuple[int, ...], ...],
    n: int,
    observed_agreement: float,
    majority_baseline: float,
    prevalence_a: Sequence[tuple[str, int]],
    prevalence_b: Sequence[tuple[str, int]],
    cohen_kappa: KappaResult,
    weighted_kappa_linear: KappaResult,
    weighted_kappa_quadratic: KappaResult,
    krippendorff_alpha: AlphaResult,
    per_category: Sequence[CategoryPrf],
    items: Sequence[ItemAgreementRow],
) -> StratumReport:
    """Assemble one :class:`StratumReport` from already-computed R1 results.
    Pure — no I/O, no repository access. ``below_power`` is computed here,
    the ONE place it is ever decided: ``True`` iff any declared category's
    support (either rater's own marginal) is below
    :data:`BELOW_POWER_THRESHOLD` (task-packets/N1-T01.yaml R2/derived_
    decisions (g)) — reading BOTH raters' marginals, not only the reference
    rater's, since the power concern is "do we have enough labeled instances
    of this category at all", not specifically about the reference side.
    """
    all_counts = [count for _, count in prevalence_a] + [count for _, count in prevalence_b]
    below_power = any(count < BELOW_POWER_THRESHOLD for count in all_counts)

    return StratumReport(
        stratum_id=stratum_id,
        n=n,
        rater_a_id=rater_a_id,
        rater_b_id=rater_b_id,
        reference_rater=reference_rater,
        categories=tuple(categories),
        confusion_matrix=confusion_matrix,
        observed_agreement=observed_agreement,
        majority_baseline=majority_baseline,
        prevalence_a=tuple(prevalence_a),
        prevalence_b=tuple(prevalence_b),
        cohen_kappa=_kappa_field(cohen_kappa),
        weighted_kappa_linear=_kappa_field(weighted_kappa_linear),
        weighted_kappa_quadratic=_kappa_field(weighted_kappa_quadratic),
        krippendorff_alpha=_alpha_field(krippendorff_alpha),
        per_category=tuple(
            CategoryPrfRow(
                category=row.category,
                support=row.support,
                true_positive=row.true_positive,
                false_positive=row.false_positive,
                false_negative=row.false_negative,
                precision=row.precision,
                recall=row.recall,
                f1=row.f1,
                reason=row.reason,
            )
            for row in per_category
        ),
        items=tuple(items),
        below_power=below_power,
    )


def build_agreement_report(
    *,
    reference_rater: str,
    crosswalk_path: str,
    crosswalk_sha256: str,
    strata: Sequence[StratumReport],
) -> AgreementReport:
    """Assemble the top-level :class:`AgreementReport`. ``strata`` is sorted
    by ``stratum_id`` here — the ONE place ordering is decided, so a caller
    handing in strata in any order still gets a deterministic report.
    """
    ordered = tuple(sorted(strata, key=lambda stratum: stratum.stratum_id))
    return AgreementReport(
        reference_rater=reference_rater,
        reference_rater_note=_reference_rater_note(reference_rater),
        crosswalk_path=crosswalk_path,
        crosswalk_sha256=crosswalk_sha256,
        strata=ordered,
    )


# ---------------------------------------------------------------------------
# Rendering — pure, deterministic, no wall clock.
# ---------------------------------------------------------------------------


def _kappa_line(label: str, field: KappaField) -> str:
    if field.value is not None:
        return f"- **{label}:** {field.value:.6f}"
    return f"- **{label}:** null ({field.reason})"


def _alpha_line(label: str, field: AlphaField) -> str:
    if field.value is not None:
        return f"- **{label}:** {field.value:.6f}"
    return f"- **{label}:** null ({field.reason})"


def _render_stratum_markdown(stratum: StratumReport) -> list[str]:
    lines: list[str] = []
    lines.append(f"### Stratum: {stratum.stratum_id}")
    lines.append("")
    lines.append(f"- **n:** {stratum.n}")
    a_is_reference = stratum.reference_rater == stratum.rater_a_id
    b_is_reference = stratum.reference_rater == stratum.rater_b_id
    lines.append(f"- **rater a (reference={a_is_reference}):** {stratum.rater_a_id}")
    lines.append(f"- **rater b (reference={b_is_reference}):** {stratum.rater_b_id}")
    lines.append(f"- **categories (ordered):** {', '.join(stratum.categories)}")
    lines.append(
        f"- **below power (< {stratum.below_power_threshold}/category):** {stratum.below_power}"
    )
    lines.append("")
    lines.append("#### Confusion matrix (rows = rater a, cols = rater b)")
    lines.append("")
    col_width = max(12, max(len(c) for c in stratum.categories))
    header = " " * col_width + "  " + "  ".join(f"{c:>{col_width}}" for c in stratum.categories)
    lines.append(header)
    for row_category, row in zip(stratum.categories, stratum.confusion_matrix, strict=True):
        cells = "  ".join(f"{value:>{col_width}}" for value in row)
        lines.append(f"{row_category:>{col_width}}  {cells}")
    lines.append("")
    lines.append("#### Metrics")
    lines.append("")
    lines.append(f"- **observed agreement (p_o):** {stratum.observed_agreement:.6f}")
    lines.append(f"- **majority-class baseline:** {stratum.majority_baseline:.6f}")
    lines.append(_kappa_line("Cohen's kappa", stratum.cohen_kappa))
    lines.append(_kappa_line("weighted kappa (linear)", stratum.weighted_kappa_linear))
    lines.append(_kappa_line("weighted kappa (quadratic)", stratum.weighted_kappa_quadratic))
    lines.append(_alpha_line("Krippendorff's alpha (nominal)", stratum.krippendorff_alpha))
    lines.append("")
    lines.append(
        "#### Per-category precision / recall / F1 (reference: " + stratum.reference_rater + ")"
    )
    lines.append("")
    for category_prf in stratum.per_category:
        if category_prf.reason is not None:
            lines.append(
                f"- **{category_prf.category}** (support={category_prf.support}): "
                f"null ({category_prf.reason})"
            )
        else:
            lines.append(
                f"- **{category_prf.category}** (support={category_prf.support}): "
                f"precision={category_prf.precision:.6f}, recall={category_prf.recall:.6f}, "
                f"f1={category_prf.f1:.6f}"
            )
    lines.append("")
    lines.append("#### Item-by-item agreement")
    lines.append("")
    for item in stratum.items:
        mark = "YES" if item.agree else "NO"
        lines.append(
            f"- {item.item_id} — {item.title}: {item.rater_a_label} / {item.rater_b_label} "
            f"(agree: {mark})"
        )
    lines.append("")
    return lines


def render_markdown(report: AgreementReport) -> str:
    """Deterministic Markdown rendering. Bullet-block layout throughout
    (never a ``|``-delimited table) mirroring ``mrr.domain.research_report``'s
    own disclosed choice — item titles are free text read from a
    bibliographic corpus and could in principle carry a literal ``|`` or a
    leading ``#``/``-``; a bullet block has no comparable structural-
    injection risk. Calling this twice with an equal ``report`` produces
    byte-identical output (no wall clock, no unordered iteration — every
    collection rendered is already an explicit, ordered tuple).
    """
    lines: list[str] = []
    lines.append("# Agreement report (task-packets/N1-T01.yaml)")
    lines.append("")
    lines.append("## Honesty header")
    lines.append("")
    lines.append(
        f"- **measures_reliability_not_validity:** {report.measures_reliability_not_validity}"
    )
    lines.append(f"- {report.reliability_note}")
    lines.append(f"- **reference rater:** {report.reference_rater}")
    lines.append(f"- {report.reference_rater_note}")
    lines.append(f"- **crosswalk path:** {report.crosswalk_path}")
    lines.append(f"- **crosswalk sha256:** {report.crosswalk_sha256}")
    lines.append("")
    lines.append("## Pooling")
    lines.append("")
    lines.append(f"- {report.pooled_note}")
    lines.append("")
    lines.append("## Strata")
    lines.append("")
    for stratum in report.strata:
        lines.extend(_render_stratum_markdown(stratum))
    return "\n".join(lines) + "\n"


def render_json(report: AgreementReport) -> str:
    """Deterministic JSON rendering: ``sort_keys=True`` so byte-identity
    across two renders never depends on Python dict/model-field insertion
    order, only on ``report``'s own already-fixed field values.
    """
    return json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
