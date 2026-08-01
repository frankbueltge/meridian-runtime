"""The VALIDITY report (task-packets/N1-T02.yaml R4): a Pydantic-validated
projection of how a classifying system's evidence-relation labels compare
against a FROZEN GOLD STANDARD, computed entirely from
:mod:`mrr.domain.agreement`'s existing functions with gold as rater "a".

--- Why this is a second type and not a flag on the first ------------------

:class:`mrr.domain.agreement_report.AgreementReport` carries
``measures_reliability_not_validity: Literal[True]`` — a field that, by its
own module's words, "cannot be ``False`` ... not merely a default a caller
could override". That was N1-T01's central honesty boundary: its two raters
were two independent INSTANCES, and stable agreement between two instances is
not evidence that the shared classification is correct.

Making validity a second value of that same field would silently reopen
exactly the claim N1-T01 closed, and would do it in the one place a reader
trusts. So there are two report types over one shared metric core. This one
carries the mirror invariant, :attr:`GoldValidityReport.
measures_validity_against_gold`, also ``Literal[True]``, and it is honest only
to the extent its gold standard is: hence :attr:`GoldValidityReport.
label_provenance`, which is required, and
:attr:`GoldValidityReport.blind_to_measured_labels`, which is carried through
to the rendered header rather than buried.

--- Gold is rater "a", always -----------------------------------------------

Every call into :mod:`mrr.domain.agreement` from the service that builds this
report passes gold as rater ``a`` and the system under test as rater ``b``,
with ``reference="a"`` throughout. That single convention is what makes
:func:`mrr.domain.agreement.per_category_prf` mean precision/recall/F1 against
truth (rather than an asymmetric inter-rater statistic), and what makes
:func:`mrr.domain.agreement.majority_baseline` the floor a constant classifier
predicting gold's own most frequent category would reach.

--- Accuracy never travels alone --------------------------------------------

:func:`render_markdown` prints observed agreement and the majority-class
baseline in the SAME block, and there is no code path that emits one without
the other (task-packets/N1-T02.yaml invariant). A classifier that beats
nothing but the majority floor has demonstrated nothing, and an accuracy
figure quoted alone is the standard way that fact gets lost.

--- The one new number ------------------------------------------------------

:func:`false_support_rate` is the only mathematics this module adds: of all
items whose GOLD label is not ``supports``, the share the system labelled
``supports``. It is derived from the confusion matrix, not measured
separately, and it mirrors the already-existing
``benchmarks.meridianbench.targets.FALSE_SUPPORT_ON_MB_CIT_TARGET``. It
matters more than accuracy here: reading a ``qualifies`` as a ``supports``
inflates corroboration and lifts the ceiling that is supposed to cap what a
claim may say (mrr.services.node_runtime.synthesis_executor's
``_SUPPORTING_RELATION`` counting).

Null-with-reason, never a fabricated ``0.0``: when the gold set contains no
non-``supports`` item at all, the rate is undefined and says so.

--- Determinism -------------------------------------------------------------

No wall clock anywhere. Every rendered byte is a function of the inputs, so
two renders of the same report are byte-identical and the report has a stable
content hash (mirrors :mod:`mrr.domain.agreement_report`'s own invariant).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from fractions import Fraction
from typing import Literal

from mrr.contracts.common import MRRModel
from mrr.domain.agreement import (
    AlphaResult,
    CategoryPrf,
    ConfusionMatrix,
    KappaResult,
    col_marginals,
    row_marginals,
)
from mrr.domain.agreement_report import BELOW_POWER_THRESHOLD
from pydantic import Field

#: The relation whose over-prediction the false-support rate measures. Kept as
#: a module constant rather than inlined so the coupling to
#: ``mrr.services.node_runtime.synthesis_executor._SUPPORTING_RELATION`` is
#: visible and greppable — the two must name the same relation, because the
#: whole point of the metric is over-prediction of the label that actually
#: moves the corroboration count.
SUPPORTING_RELATION = "supports"

#: The reason string when no gold item is available to compute a rate over —
#: mirrors :mod:`mrr.domain.agreement`'s null-with-reason contract rather than
#: inventing a second convention.
UNDEFINED_NO_NEGATIVE_GOLD = (
    "undefined: the gold standard contains no item whose label is not 'supports'"
)

_VALIDITY_NOTE = (
    "This report measures VALIDITY against a frozen gold standard: a fixed set of "
    "correct answers, set outside the practice being measured and pinned by content "
    "hash before any system was run against it. That is a different and stronger claim "
    "than the RELIABILITY reported by mrr validate agreement, which compares two "
    "independent instances to each other and can be perfectly stable while both are "
    "wrong. This report is honest exactly as far as its gold standard is: read "
    "label_provenance before reading any number below."
)

_NOT_BLIND_WARNING = (
    "The labelling party was NOT blind to the classifications being measured. Agreement "
    "under those conditions is a confirmation, not an independent standard, and every "
    "figure below must be read with that discount."
)

_BASELINE_NOTE = (
    "Accuracy is printed beside the majority-class baseline deliberately: a classifier "
    "that only predicts the gold standard's most frequent category reaches the baseline "
    "while having learned nothing. An accuracy above zero is not a result; an accuracy "
    "meaningfully above this floor is."
)


_COVERAGE_NOTE = (
    "Two counts below are not scores and must not be read as ones. UNDECIDABLE cases are "
    "cases the criteria failed to settle; they are excluded from the matrix because there is "
    "no correct answer to score against, and their count measures the criteria's coverage "
    "rather than any classifier's skill. TIE-BROKEN cases are cases where the conservative "
    "rule, not the definitions, produced the label — every one of them could have gone the "
    "other way, so the corroboration ceiling derived from this set is a point estimate with a "
    "width, and the tie count is that width. Both counts exist because an outside practice "
    "objected that the first version of these criteria produced a clean-looking output by "
    "destroying the evidence of its own uncertainty. It was right."
)


def _below_power_note(threshold: int) -> str:
    return (
        f"below_power is set when a category has fewer than {threshold} gold labels. "
        "Below that, a headline figure must not be read as publication-grade evidence "
        "(docs/design/2026-07-24-capability-roadmap-entwurf.md, N1: 20-30 labels per "
        "category)."
    )


class FalseSupportRate(MRRModel):
    """The share of non-``supports`` gold items a system labelled
    ``supports``. ``value`` is ``None`` exactly when ``reason`` is not
    ``None`` — the same null-with-reason contract
    :class:`mrr.domain.agreement.KappaResult` uses.
    """

    value: float | None
    reason: str | None
    #: How many gold items were NOT ``supports`` — the denominator, carried so
    #: a reader can see how thin the rate is without recomputing it.
    negative_gold_n: int = Field(ge=0)
    #: How many of those the system called ``supports`` — the numerator.
    false_supports: int = Field(ge=0)


def false_support_rate(matrix: ConfusionMatrix, categories: Sequence[str]) -> FalseSupportRate:
    """Compute the false-support rate from ``matrix`` (rows = gold, columns =
    system) over the declared ``categories`` order.

    Of every item whose GOLD label is not :data:`SUPPORTING_RELATION`, the
    share the system labelled :data:`SUPPORTING_RELATION`. Exact rational
    arithmetic over the integer counts, converted once at the end — the same
    discipline :mod:`mrr.domain.agreement` applies.

    Hand oracle (task-packets/N1-T02.yaml AT1): for the 20-item fixture matrix
    ``[[8,1,1,0],[1,3,1,0],[1,0,2,0],[1,0,0,1]]`` over
    ``[supports, contradicts, qualifies, contextualizes]``, 10 gold items are
    not ``supports`` and 3 of them were labelled ``supports``, so the rate is
    exactly ``3/10 = 0.30``.

    Returns null-with-reason (:data:`UNDEFINED_NO_NEGATIVE_GOLD`) when the gold
    standard has no non-``supports`` item at all — never a fabricated ``0.0``,
    which would read as "no false supports" when the truth is "the question
    could not be asked".
    """
    if SUPPORTING_RELATION not in categories:
        return FalseSupportRate(
            value=None,
            reason=(
                f"undefined: {SUPPORTING_RELATION!r} is not one of the declared "
                f"categories {list(categories)!r}"
            ),
            negative_gold_n=0,
            false_supports=0,
        )

    supports_index = list(categories).index(SUPPORTING_RELATION)
    rows = row_marginals(matrix)
    negative_gold_n = sum(rows[i] for i in range(len(categories)) if i != supports_index)
    false_supports = sum(
        matrix[i][supports_index] for i in range(len(categories)) if i != supports_index
    )

    if negative_gold_n == 0:
        return FalseSupportRate(
            value=None,
            reason=UNDEFINED_NO_NEGATIVE_GOLD,
            negative_gold_n=0,
            false_supports=false_supports,
        )

    return FalseSupportRate(
        value=float(Fraction(false_supports, negative_gold_n)),
        reason=None,
        negative_gold_n=negative_gold_n,
        false_supports=false_supports,
    )


class CategoryPrfRow(MRRModel):
    """One category's precision/recall/F1 against gold. Mirrors
    :class:`mrr.domain.agreement_report.CategoryPrfRow` field for field so the
    two reports read alike; ``support`` here is the GOLD standard's own count
    for the category, since gold is always the reference rater.
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
    #: Set when this category alone has fewer than
    #: :data:`mrr.domain.agreement_report.BELOW_POWER_THRESHOLD` gold labels.
    #: Per-category rather than per-report, because a set can be well-powered
    #: for ``supports`` and hopeless for ``contextualizes`` at the same time.
    below_power: bool


class KappaField(MRRModel):
    """A chance-corrected statistic as carried in the report — ``value`` is
    ``None`` exactly when ``reason`` is not ``None``.
    """

    value: float | None
    reason: str | None


class AlphaField(MRRModel):
    """Krippendorff's alpha as carried in the report, same null-with-reason
    contract as :class:`KappaField`.
    """

    value: float | None
    reason: str | None


def _kappa_field(result: KappaResult) -> KappaField:
    return KappaField(value=result.value, reason=result.reason)


def _alpha_field(result: AlphaResult) -> AlphaField:
    return AlphaField(value=result.value, reason=result.reason)


class ItemValidityRow(MRRModel):
    """One case, with gold's label and the system's beside it. Present so a
    disputed aggregate can be argued at the level of the individual case
    rather than only at the level of the headline figure.
    """

    case_id: str
    gold_label: str
    system_label: str
    correct: bool


class GoldValidityReport(MRRModel):
    """The full validity projection. NEVER a persisted object and never
    authoritative (AGENTS.md rule 7) — it is Pydantic-validated because it is
    externally visible (rule 6), and it enters no object store, no database,
    and no migration.
    """

    #: The mirror of N1-T01's own invariant, and equally not overridable: a
    #: report of this type always claims validity against a gold standard, and
    #: a caller who wants to claim something weaker must use
    #: :class:`mrr.domain.agreement_report.AgreementReport` instead.
    measures_validity_against_gold: Literal[True] = True
    validity_note: str = _VALIDITY_NOTE

    #: The gold set's own version name and the sha256 of the exact bytes
    #: measured against — together, what "frozen" means operationally.
    gold_set_id: str
    gold_set_sha256: str
    #: ``<gold_set_id>@sha256:<hex>``, the string that goes into
    #: ``benchmarks.meridianbench.promotion.EvaluationProfile.fixture_set_id``.
    fixture_set_id: str

    #: Which criteria the labels were made under, and the proof they were
    #: locked first (task-packets/N1-T02.yaml R1's order gate).
    criteria_version: str
    criteria_locked_at: str
    criteria_lock_content_hash: str
    labelled_at: str

    #: Who produced the labels. Required — a gold standard that cannot say
    #: where its answers came from is not a standard.
    label_provenance: str
    producing_practice: str
    encounter_id: str | None = None
    blind_to_measured_labels: bool
    #: Non-empty exactly when ``blind_to_measured_labels`` is ``False``.
    not_blind_warning: str | None = None

    #: The system whose labels were measured, named.
    system_id: str

    n: int = Field(ge=0)
    categories: tuple[str, ...]
    confusion_matrix: tuple[tuple[int, ...], ...]
    observed_agreement: float = Field(ge=0.0, le=1.0)
    majority_baseline: float = Field(ge=0.0, le=1.0)
    baseline_note: str = _BASELINE_NOTE
    gold_prevalence: tuple[tuple[str, int], ...]
    system_prevalence: tuple[tuple[str, int], ...]
    cohen_kappa: KappaField
    weighted_kappa_linear: KappaField
    weighted_kappa_quadratic: KappaField
    krippendorff_alpha: AlphaField
    per_category: tuple[CategoryPrfRow, ...]
    false_support: FalseSupportRate
    items: tuple[ItemValidityRow, ...]
    below_power: bool
    below_power_threshold: int = BELOW_POWER_THRESHOLD
    below_power_note: str = _below_power_note(BELOW_POWER_THRESHOLD)

    # --- Criteria v2, both fields added because a sibling practice showed the
    #     first version produced a tidy number by destroying the evidence of
    #     its own uncertainty. Neither is scored; both are REPORTED, which is
    #     the entire point.
    #: Cases the labelling practice could not decide under the criteria. Not
    #: scored — excluded from the matrix above — because there is no correct
    #: answer to score against. Their COUNT is the measurement: coverage is a
    #: property of a criteria set, invisible if every case is forced to a label.
    undecidable_case_ids: tuple[str, ...] = ()
    #: Cases where the conservative tie-break, rather than the definitions,
    #: produced the label. Ulysses' objection in one number: without it the
    #: corroboration ceiling is a point estimate whose distance from its own
    #: alternative cannot be recovered. Their sentence — "the first thing a
    #: conservative rule should be required to report is how often it fired."
    tie_broken_case_ids: tuple[str, ...] = ()
    coverage_note: str = _COVERAGE_NOTE


def build_gold_validity_report(
    *,
    gold_set_id: str,
    gold_set_sha256: str,
    criteria_version: str,
    criteria_locked_at: str,
    criteria_lock_content_hash: str,
    labelled_at: str,
    label_provenance: str,
    producing_practice: str,
    encounter_id: str | None,
    blind_to_measured_labels: bool,
    system_id: str,
    categories: Sequence[str],
    confusion_matrix: ConfusionMatrix,
    n: int,
    observed_agreement: float,
    majority_baseline: float,
    cohen_kappa: KappaResult,
    weighted_kappa_linear: KappaResult,
    weighted_kappa_quadratic: KappaResult,
    krippendorff_alpha: AlphaResult,
    per_category: Sequence[CategoryPrf],
    items: Sequence[ItemValidityRow],
    undecidable_case_ids: Sequence[str] = (),
    tie_broken_case_ids: Sequence[str] = (),
) -> GoldValidityReport:
    """Assemble the report from already-computed metric results.

    This function computes no agreement mathematics of its own — every
    statistic arrives as a :mod:`mrr.domain.agreement` result object. It
    derives exactly two things: the per-category and overall ``below_power``
    flags, and the false-support rate (which is a projection of the confusion
    matrix, not a separate measurement).

    ``below_power`` at report level is set when ANY declared category has
    fewer than :data:`mrr.domain.agreement_report.BELOW_POWER_THRESHOLD` gold
    labels — the pessimistic reading on purpose, so a set that is comfortable
    for its majority class and threadbare everywhere else is not reported as
    adequately powered.
    """
    gold_marginals = row_marginals(confusion_matrix)
    system_marginals = col_marginals(confusion_matrix)

    rows: list[CategoryPrfRow] = []
    for index, prf in enumerate(per_category):
        rows.append(
            CategoryPrfRow(
                category=prf.category,
                support=prf.support,
                true_positive=prf.true_positive,
                false_positive=prf.false_positive,
                false_negative=prf.false_negative,
                precision=prf.precision,
                recall=prf.recall,
                f1=prf.f1,
                reason=prf.reason,
                below_power=gold_marginals[index] < BELOW_POWER_THRESHOLD,
            )
        )

    return GoldValidityReport(
        gold_set_id=gold_set_id,
        gold_set_sha256=gold_set_sha256,
        fixture_set_id=f"{gold_set_id}@{gold_set_sha256}",
        criteria_version=criteria_version,
        criteria_locked_at=criteria_locked_at,
        criteria_lock_content_hash=criteria_lock_content_hash,
        labelled_at=labelled_at,
        label_provenance=label_provenance,
        producing_practice=producing_practice,
        encounter_id=encounter_id,
        blind_to_measured_labels=blind_to_measured_labels,
        not_blind_warning=None if blind_to_measured_labels else _NOT_BLIND_WARNING,
        system_id=system_id,
        n=n,
        categories=tuple(categories),
        confusion_matrix=confusion_matrix,
        observed_agreement=observed_agreement,
        majority_baseline=majority_baseline,
        gold_prevalence=tuple(zip(categories, gold_marginals, strict=True)),
        system_prevalence=tuple(zip(categories, system_marginals, strict=True)),
        cohen_kappa=_kappa_field(cohen_kappa),
        weighted_kappa_linear=_kappa_field(weighted_kappa_linear),
        weighted_kappa_quadratic=_kappa_field(weighted_kappa_quadratic),
        krippendorff_alpha=_alpha_field(krippendorff_alpha),
        per_category=tuple(rows),
        false_support=false_support_rate(confusion_matrix, categories),
        items=tuple(items),
        below_power=any(count < BELOW_POWER_THRESHOLD for count in gold_marginals),
        undecidable_case_ids=tuple(undecidable_case_ids),
        tie_broken_case_ids=tuple(tie_broken_case_ids),
    )


def _format_optional(value: float | None, reason: str | None) -> str:
    """Render a null-with-reason metric. A missing value ALWAYS shows its
    reason — never an empty cell, never a dash a reader could mistake for
    zero.
    """
    if value is None:
        return f"null ({reason or 'no reason given'})"
    return f"{value:.4f}"


def render_markdown(report: GoldValidityReport) -> str:
    """Render the report as deterministic Markdown. Pure: no wall clock, no
    unordered iteration — two calls with the same report produce identical
    bytes.
    """
    lines: list[str] = []
    lines.append("# Gold-standard validity report")
    lines.append("")
    lines.append(report.validity_note)
    lines.append("")
    lines.append("## The standard")
    lines.append("")
    lines.append(f"- Gold set: `{report.gold_set_id}`")
    lines.append(f"- Gold sha256: `{report.gold_set_sha256}`")
    lines.append(f"- Fixture set id: `{report.fixture_set_id}`")
    lines.append(f"- Labels produced by: {report.producing_practice}")
    if report.encounter_id is not None:
        lines.append(f"- Encounter: `{report.encounter_id}`")
    lines.append(f"- Provenance: {report.label_provenance}")
    lines.append(f"- Blind to the measured labels: {report.blind_to_measured_labels}")
    if report.not_blind_warning is not None:
        lines.append("")
        lines.append(f"> {report.not_blind_warning}")
    lines.append("")
    lines.append("## The order gate")
    lines.append("")
    lines.append(f"- Criteria version: `{report.criteria_version}`")
    lines.append(f"- Criteria locked at: `{report.criteria_locked_at}`")
    lines.append(f"- Criteria lock hash: `{report.criteria_lock_content_hash}`")
    lines.append(f"- Labelled at: `{report.labelled_at}` (strictly after the lock)")
    lines.append("")
    lines.append("## The measurement")
    lines.append("")
    lines.append(f"- System under test: `{report.system_id}`")
    lines.append(f"- n = {report.n}")
    lines.append("")
    lines.append("| | |")
    lines.append("|---|---|")
    lines.append(f"| Accuracy (observed agreement) | {report.observed_agreement:.4f} |")
    lines.append(f"| Majority-class baseline | {report.majority_baseline:.4f} |")
    lines.append("")
    lines.append(report.baseline_note)
    lines.append("")
    lines.append("| Statistic | Value |")
    lines.append("|---|---|")
    lines.append(
        "| Cohen's kappa | "
        f"{_format_optional(report.cohen_kappa.value, report.cohen_kappa.reason)} |"
    )
    linear = report.weighted_kappa_linear
    quadratic = report.weighted_kappa_quadratic
    lines.append(f"| Weighted kappa (linear) | {_format_optional(linear.value, linear.reason)} |")
    lines.append(
        f"| Weighted kappa (quadratic) | {_format_optional(quadratic.value, quadratic.reason)} |"
    )
    lines.append(
        "| Krippendorff's alpha (nominal) | "
        f"{_format_optional(report.krippendorff_alpha.value, report.krippendorff_alpha.reason)} |"
    )
    lines.append(
        "| False-support rate | "
        f"{_format_optional(report.false_support.value, report.false_support.reason)} "
        f"({report.false_support.false_supports}/{report.false_support.negative_gold_n}) |"
    )
    lines.append("")
    lines.append("### Confusion matrix (rows = gold, columns = system)")
    lines.append("")
    lines.append("| gold \\ system | " + " | ".join(report.categories) + " |")
    lines.append("|---" * (len(report.categories) + 1) + "|")
    for category, row in zip(report.categories, report.confusion_matrix, strict=True):
        lines.append(f"| {category} | " + " | ".join(str(count) for count in row) + " |")
    lines.append("")
    lines.append("### Per category, against gold")
    lines.append("")
    lines.append("| Category | Gold n | TP | FP | FN | Precision | Recall | F1 | Below power |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for prf in report.per_category:
        lines.append(
            f"| {prf.category} | {prf.support} | {prf.true_positive} | {prf.false_positive} "
            f"| {prf.false_negative} | {_format_optional(prf.precision, prf.reason)} "
            f"| {_format_optional(prf.recall, prf.reason)} "
            f"| {_format_optional(prf.f1, prf.reason)} | {prf.below_power} |"
        )
    lines.append("")
    lines.append(f"**below_power: {report.below_power}** — {report.below_power_note}")
    lines.append("")
    lines.append("### Coverage and ties — not scores")
    lines.append("")
    lines.append(report.coverage_note)
    lines.append("")
    lines.append("| | Count | Cases |")
    lines.append("|---|---|---|")
    lines.append(
        f"| Undecidable under the criteria | {len(report.undecidable_case_ids)} | "
        + (", ".join(report.undecidable_case_ids) or "—")
        + " |"
    )
    lines.append(
        f"| Decided by the conservative tie-break | {len(report.tie_broken_case_ids)} | "
        + (", ".join(report.tie_broken_case_ids) or "—")
        + " |"
    )
    lines.append("")
    lines.append("### Case by case")
    lines.append("")
    lines.append("| Case | Gold | System | Correct |")
    lines.append("|---|---|---|---|")
    for item in report.items:
        lines.append(
            f"| {item.case_id} | {item.gold_label} | {item.system_label} | {item.correct} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_json(report: GoldValidityReport) -> str:
    """Render the report as deterministic, sorted JSON — the machine-readable
    twin of :func:`render_markdown`, with a trailing newline so the file is
    POSIX-clean.
    """
    return json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


__all__ = [
    "SUPPORTING_RELATION",
    "UNDEFINED_NO_NEGATIVE_GOLD",
    "AlphaField",
    "CategoryPrfRow",
    "FalseSupportRate",
    "GoldValidityReport",
    "ItemValidityRow",
    "KappaField",
    "build_gold_validity_report",
    "false_support_rate",
    "render_json",
    "render_markdown",
]
