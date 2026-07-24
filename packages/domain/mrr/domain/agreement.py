"""Pure, dependency-free inter-rater agreement metrics (task-packets/
N1-T01.yaml R1). No ``numpy``/``scipy``/``scikit-learn``/``statsmodels`` — a
handful of closed-form computations over small confusion matrices does not
need a numeric dependency, and pulling one in would widen this repo's
dependency footprint and ``make security-check`` surface for no benefit
(task-packets/N1-T01.yaml derived_decisions (d)). No framework/service/
repository import anywhere in this module (enforced by the existing global
import-linter contract in pyproject.toml, which already forbids
``mrr.services`` from every ``mrr.domain`` module, plus a dedicated AST
boundary test, tests/unit/architecture/test_agreement_boundary.py, mirroring
tests/unit/architecture/test_research_report_boundary.py's own precedent).

This module computes RELIABILITY (inter-instance agreement), never validity
against a human gold standard — see ``mrr.domain.agreement_report`` for the
honesty header every report built from these functions carries. The two
"raters" this module is agnostic about are, in the N1-T01 use case, the
K1-T04 pipeline classification and the K1-T06 blind verifying instance —
neither is a human, and this module does not know or care which is which; it
only knows "rater a" and "rater b" (:mod:`mrr.services.validation.service`
names them "pipeline"/"blind" and picks one as a NAMED reference rater for
the asymmetric precision/recall/F1, per R1's own requirement).

--- Determinism (task-packets/N1-T01.yaml invariant) ---------------------

No wall clock, no float non-determinism from unordered iteration: every
category axis is an EXPLICIT, caller-supplied ordered sequence (never a
``set``/``dict`` iteration order), and every internal probability
(``p_o``/``p_e``/weighted-kappa numerator and denominator/Krippendorff's
``D_o``/``D_e``) is accumulated with :class:`fractions.Fraction` — EXACT
rational arithmetic over the integer confusion-matrix counts — before a
single, final conversion to ``float`` for the result. This both avoids
floating-point drift between two runs over identical inputs (Python's
``float()`` conversion from a ``Fraction`` is deterministic) and makes the
"is the denominator exactly zero" edge-case checks below exact equality
tests, never a fragile ``abs(x) < epsilon`` comparison.

--- Edge cases are explicit, never silent (AGENTS.md rule 12) ------------

- A rater pair with mismatched item sets or unequal length raises
  :class:`MismatchedRatersError`, naming the offending items — never a
  positional zip that silently drops or misaligns data.
- A label outside the caller-declared category list raises
  :class:`UnknownCategoryLabelError`, naming the item and the label — never
  silently coerced into "other" or dropped.
- Duplicate categories in a caller-supplied category list raise
  :class:`DuplicateCategoryError` — never silently deduplicated (which would
  quietly change the confusion matrix's own shape).
- Cohen's kappa / weighted kappa / Krippendorff's alpha: when the relevant
  expected-agreement denominator is EXACTLY zero (every unit — both raters,
  every item — used only a single shared category, so there is no
  expected-chance variation to correct for), the result is ``None`` with an
  explicit ``reason`` string, NEVER ``ZeroDivisionError`` and NEVER a
  fabricated ``0.0``/``1.0``. This is not a rare corner case in this
  packet's own real acceptance data: the model-collapse corpus's theory
  stratum (n=3) has both raters using only "supports", so its kappa/alpha
  are genuinely undefined by this exact rule — see the packet report for the
  full worked consequence and why AT1's "kappa = 1.0 per stratum" wording
  does not survive contact with that stratum's real, degenerate data.
- Per-category precision/recall/F1: a category with zero support in the
  NAMED REFERENCE rater's own marginal has ``f1=None`` (also ``precision``/
  ``recall`` — see :class:`CategoryPrf`) with ``reason="no support"``, never
  ``0.0`` and never a division error. When the reference rater DOES support a
  category but the true-positive count is zero (a total miss), F1 is the
  well-defined limit ``0.0`` (the universal precision=recall=0 -> F1=0
  convention), not an undefined case.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

#: The reason string used by every "single/no category used, zero expected-
#: chance variation" null result (Cohen's kappa, weighted kappa, and
#: Krippendorff's alpha alike) — one literal string shared across all three,
#: since the underlying cause is identical in every case: zero variation
#: means zero expected disagreement to correct for, not perfect-by-chance
#: agreement.
UNDEFINED_NO_CHANCE_VARIATION = "undefined: no expected-chance variation"

#: The reason string for a category with zero support in the reference
#: rater's own marginal (task-packets/N1-T01.yaml R1: "F1 ... reported as
#: null with reason 'no support'").
NO_SUPPORT = "no support"

WeightScheme = Literal["linear", "quadratic"]
RaterSide = Literal["a", "b"]


class AgreementError(Exception):
    """Base class for every typed error this module raises."""


class DuplicateCategoryError(AgreementError):
    """Raised when a caller-supplied ``categories`` sequence contains a
    repeated label — silently deduplicating would change the confusion
    matrix's own declared shape without the caller noticing.
    """

    def __init__(self, categories: Sequence[str]) -> None:
        self.categories = tuple(categories)
        duplicates = sorted({c for c in categories if categories.count(c) > 1})
        super().__init__(f"duplicate categories in {categories!r}: {duplicates!r}")


class UnknownCategoryLabelError(AgreementError):
    """Raised when an item's rater label is not a member of the declared
    ``categories`` sequence. Carries ``item_id``, ``rater`` (``"a"``/``"b"``),
    and the offending ``label`` so a caller can tell exactly which
    observation is out of bounds without parsing the message string.
    """

    def __init__(
        self, item_id: str, rater: RaterSide, label: str, categories: Sequence[str]
    ) -> None:
        self.item_id = item_id
        self.rater = rater
        self.label = label
        self.categories = tuple(categories)
        super().__init__(
            f"item {item_id!r}: rater {rater!r} label {label!r} is not one of the declared "
            f"categories {list(categories)!r}"
        )


class MismatchedRatersError(AgreementError):
    """Raised by :func:`align_ratings` when the two raters' item id sets are
    not identical (an item rated by one but not the other) — never a silent
    partial alignment over only the intersection. Carries ``only_in_a`` and
    ``only_in_b`` (sorted tuples of the offending item ids) so a caller can
    tell exactly which items are missing from which rater.
    """

    def __init__(self, only_in_a: tuple[str, ...], only_in_b: tuple[str, ...]) -> None:
        self.only_in_a = only_in_a
        self.only_in_b = only_in_b
        super().__init__(
            "rater item sets do not match: "
            f"only in rater a: {list(only_in_a)!r}; only in rater b: {list(only_in_b)!r}"
        )


@dataclass(frozen=True, slots=True)
class AlignedRating:
    """One item, aligned: its raw label from each of the two raters. Built
    only by :func:`align_ratings`, which is the sole place item-set
    validation happens.
    """

    item_id: str
    rater_a_label: str
    rater_b_label: str


def align_ratings(
    rater_a: Mapping[str, str], rater_b: Mapping[str, str]
) -> tuple[AlignedRating, ...]:
    """Pair up ``rater_a``/``rater_b`` (each ``item_id -> raw label``) into a
    sequence of :class:`AlignedRating`, sorted by ``item_id`` for a
    deterministic, caller-independent order.

    Raises:
        MismatchedRatersError: the two mappings' key sets differ — an item
            present in one rater but absent from the other.
    """
    ids_a = set(rater_a)
    ids_b = set(rater_b)
    if ids_a != ids_b:
        raise MismatchedRatersError(
            only_in_a=tuple(sorted(ids_a - ids_b)),
            only_in_b=tuple(sorted(ids_b - ids_a)),
        )
    return tuple(
        AlignedRating(
            item_id=item_id, rater_a_label=rater_a[item_id], rater_b_label=rater_b[item_id]
        )
        for item_id in sorted(ids_a)
    )


ConfusionMatrix = tuple[tuple[int, ...], ...]


def _check_categories(categories: Sequence[str]) -> None:
    if len(set(categories)) != len(categories):
        raise DuplicateCategoryError(categories)


def confusion_matrix(
    ratings: Sequence[AlignedRating], categories: Sequence[str]
) -> ConfusionMatrix:
    """Build the ``len(categories) x len(categories)`` confusion matrix over
    an EXPLICIT, caller-supplied ``categories`` order (task-packets/
    N1-T01.yaml R1: "no implicit ordering") — ``matrix[i][j]`` is the count
    of items where rater a's label is ``categories[i]`` and rater b's label
    is ``categories[j]``. A category that is never actually observed still
    occupies its declared row/column with zeros (never silently dropped from
    the matrix's own shape) — see :func:`per_category_prf`'s "no support"
    handling for exactly this case.

    Raises:
        DuplicateCategoryError: ``categories`` contains a repeated label.
        UnknownCategoryLabelError: an item's rater label is not in
            ``categories``.
    """
    _check_categories(categories)
    index = {category: i for i, category in enumerate(categories)}
    size = len(categories)
    matrix = [[0] * size for _ in range(size)]
    for rating in ratings:
        if rating.rater_a_label not in index:
            raise UnknownCategoryLabelError(rating.item_id, "a", rating.rater_a_label, categories)
        if rating.rater_b_label not in index:
            raise UnknownCategoryLabelError(rating.item_id, "b", rating.rater_b_label, categories)
        matrix[index[rating.rater_a_label]][index[rating.rater_b_label]] += 1
    return tuple(tuple(row) for row in matrix)


def total_n(matrix: ConfusionMatrix) -> int:
    """Total number of rated items — the sum of every cell."""
    return sum(sum(row) for row in matrix)


def row_marginals(matrix: ConfusionMatrix) -> tuple[int, ...]:
    """Rater a's per-category counts (row sums), in the matrix's own
    category order.
    """
    return tuple(sum(row) for row in matrix)


def col_marginals(matrix: ConfusionMatrix) -> tuple[int, ...]:
    """Rater b's per-category counts (column sums), in the matrix's own
    category order.
    """
    size = len(matrix)
    return tuple(sum(matrix[i][j] for i in range(size)) for j in range(size))


def observed_agreement(matrix: ConfusionMatrix) -> float:
    """``p_o``: the fraction of items where both raters agree (the trace of
    ``matrix`` divided by its total). ``0.0`` if the matrix is empty of any
    items — an empty confusion matrix is a caller error the service layer
    should never actually construct (every stratum this packet reads has
    n=15 or n=3), but this function does not itself raise for it, mirroring
    ``mrr.domain.research_report``'s established "narrow reader, do not
    invent a new refusal for a structurally-impossible-given-the-caller
    input" stance.
    """
    n = total_n(matrix)
    if n == 0:
        return 0.0
    agree = sum(matrix[i][i] for i in range(len(matrix)))
    return float(Fraction(agree, n))


def majority_baseline(matrix: ConfusionMatrix, *, reference: RaterSide) -> float:
    """The agreement a constant classifier, always predicting the NAMED
    ``reference`` rater's own most-frequent category, would reach:
    ``max(marginal) / N`` (task-packets/N1-T01.yaml R1). Documented choice:
    this reads the REFERENCE rater's marginal (row marginals for rater a,
    column marginals for rater b) — not some symmetric or averaged notion —
    since the whole point is "how well would a trivial predictor of the
    reference's own most common label do", the same reference asymmetry
    :func:`per_category_prf` already carries.
    """
    n = total_n(matrix)
    if n == 0:
        return 0.0
    marginals = row_marginals(matrix) if reference == "a" else col_marginals(matrix)
    return float(Fraction(max(marginals), n))


@dataclass(frozen=True, slots=True)
class KappaResult:
    """A chance-corrected agreement statistic (Cohen's kappa or weighted
    kappa): ``value`` is ``None`` exactly when ``reason`` is not ``None``
    (task-packets/N1-T01.yaml R1's null-with-reason contract) — never both
    populated, never both absent.
    """

    value: float | None
    reason: str | None
    observed: float
    expected: float


def cohen_kappa(matrix: ConfusionMatrix) -> KappaResult:
    """Cohen's kappa: ``(p_o - p_e) / (1 - p_e)``, where ``p_e`` is the
    chance-agreement expectation from the two raters' own marginals
    (task-packets/N1-T01.yaml R1). Hand oracle (AT4): a 2x2 confusion with
    ``p_o=0.7``/``p_e=0.5`` gives ``kappa=0.4`` — see
    tests/unit/domain/test_agreement.py.

    ``value`` is ``None`` with ``reason=``:data:`UNDEFINED_NO_CHANCE_VARIATION`
    when ``1 - p_e == 0`` (both raters used exactly one shared category, so
    there is no expected-chance variation to correct against) — NEVER a
    ``ZeroDivisionError`` and never a fabricated ``0.0``/``1.0``.
    """
    n = total_n(matrix)
    if n == 0:
        return KappaResult(
            value=None, reason=UNDEFINED_NO_CHANCE_VARIATION, observed=0.0, expected=0.0
        )

    agree = sum(matrix[i][i] for i in range(len(matrix)))
    p_o = Fraction(agree, n)

    rows = row_marginals(matrix)
    cols = col_marginals(matrix)
    p_e = sum(Fraction(rows[k] * cols[k], n * n) for k in range(len(matrix)))

    denominator = 1 - p_e
    if denominator == 0:
        return KappaResult(
            value=None,
            reason=UNDEFINED_NO_CHANCE_VARIATION,
            observed=float(p_o),
            expected=float(p_e),
        )
    kappa = (p_o - p_e) / denominator
    return KappaResult(value=float(kappa), reason=None, observed=float(p_o), expected=float(p_e))


def weighted_kappa(matrix: ConfusionMatrix, *, weights: WeightScheme) -> KappaResult:
    """Weighted kappa over the confusion matrix's OWN category axis order
    (already the caller's explicit order from :func:`confusion_matrix` — no
    second, separate "ordered category list" parameter is needed here, since
    that order IS the matrix's row/column index): disagreement weight
    ``w[i][j] = |i-j|`` (``"linear"``) or ``(i-j)**2`` (``"quadratic"``).
    ``kappa_w = 1 - sum(w*O) / sum(w*E)`` where ``O[i][j] = matrix[i][j]``
    (raw counts) and ``E[i][j] = row_i * col_j / N`` (task-packets/
    N1-T01.yaml exact metric definitions).

    ``value`` is ``None`` with ``reason=``:data:`UNDEFINED_NO_CHANCE_VARIATION`
    when the weighted expected-disagreement denominator is exactly zero (the
    same "single shared category used" degeneracy as :func:`cohen_kappa` —
    every weight touching the one used category is ``w[i][i] == 0`` by
    construction).
    """
    n = total_n(matrix)
    if n == 0:
        return KappaResult(
            value=None, reason=UNDEFINED_NO_CHANCE_VARIATION, observed=0.0, expected=0.0
        )

    size = len(matrix)
    rows = row_marginals(matrix)
    cols = col_marginals(matrix)

    def weight(i: int, j: int) -> int:
        distance = abs(i - j)
        return distance if weights == "linear" else distance * distance

    weighted_observed = sum(
        Fraction(weight(i, j) * matrix[i][j], 1) for i in range(size) for j in range(size)
    )
    weighted_expected = sum(
        Fraction(weight(i, j) * rows[i] * cols[j], n) for i in range(size) for j in range(size)
    )

    if weighted_expected == 0:
        return KappaResult(
            value=None,
            reason=UNDEFINED_NO_CHANCE_VARIATION,
            observed=float(weighted_observed),
            expected=float(weighted_expected),
        )
    kappa = 1 - (weighted_observed / weighted_expected)
    return KappaResult(
        value=float(kappa),
        reason=None,
        observed=float(weighted_observed),
        expected=float(weighted_expected),
    )


@dataclass(frozen=True, slots=True)
class AlphaResult:
    """Krippendorff's alpha result — ``value`` is ``None`` exactly when
    ``reason`` is not ``None``, mirroring :class:`KappaResult`'s own
    null-with-reason contract.
    """

    value: float | None
    reason: str | None


def krippendorff_alpha_nominal(matrix: ConfusionMatrix) -> AlphaResult:
    """Krippendorff's alpha, nominal distance, for exactly two coders over
    complete (fully-crossed) data — task-packets/N1-T01.yaml's exact
    definition: build the symmetric coincidence matrix ``o[c][k] =
    matrix[c][k] + matrix[k][c]`` (each unit contributes to both
    ``o[a][b]`` and ``o[b][a]``), ``n_c = sum_k o[c][k]``, ``n = sum_c n_c``
    (``= 2*N``). ``D_o = sum_{c!=k} o[c][k]``; ``D_e = (n^2 - sum_c
    n_c^2) / (n - 1)``; ``alpha = 1 - D_o/D_e``.

    On perfect agreement over MORE THAN ONE used category, ``D_o = 0`` and
    ``D_e > 0``, so ``alpha = 1.0`` exactly. When only a single category is
    used across the whole sample (every unit, both coders — the model-
    collapse theory stratum's own real data), ``D_o = 0`` AND ``D_e = 0``:
    this is the identical "zero expected-chance variation" degeneracy
    :func:`cohen_kappa` reports, and this function reports it the same way —
    ``value=None``, ``reason=``:data:`UNDEFINED_NO_CHANCE_VARIATION` — rather
    than the mathematically indefensible ``0/0 -> 1.0``.
    """
    size = len(matrix)
    coincidence = [[matrix[c][k] + matrix[k][c] for k in range(size)] for c in range(size)]
    n_c = [sum(coincidence[c]) for c in range(size)]
    n = sum(n_c)

    if n == 0 or n == 1:
        # n == 0: no data at all. n == 1 is structurally unreachable here
        # (n = 2*N is always even for N >= 1, and N == 0 is the n == 0 case
        # above) but is guarded explicitly rather than left to divide by
        # zero in D_e's own (n - 1) denominator.
        return AlphaResult(value=None, reason=UNDEFINED_NO_CHANCE_VARIATION)

    d_o = sum(coincidence[c][k] for c in range(size) for k in range(size) if c != k)
    d_e_numerator = n * n - sum(nc * nc for nc in n_c)
    d_e = Fraction(d_e_numerator, n - 1)

    if d_e == 0:
        return AlphaResult(value=None, reason=UNDEFINED_NO_CHANCE_VARIATION)

    alpha = 1 - Fraction(d_o, 1) / d_e
    return AlphaResult(value=float(alpha), reason=None)


@dataclass(frozen=True, slots=True)
class CategoryPrf:
    """Per-category precision/recall/F1 against a NAMED reference rater
    (task-packets/N1-T01.yaml R1). ``support`` is the reference rater's own
    count for this category. When ``support == 0``, ``precision``/
    ``recall``/``f1`` are all ``None`` with ``reason=``:data:`NO_SUPPORT` —
    never ``0.0``, never a division error.
    """

    category: str
    support: int
    true_positive: int
    false_positive: int
    false_negative: int
    precision: float | None
    recall: float | None
    f1: float | None
    reason: str | None


def per_category_prf(
    matrix: ConfusionMatrix, categories: Sequence[str], *, reference: RaterSide
) -> tuple[CategoryPrf, ...]:
    """Precision/recall/F1 per category, treating ``reference`` as "truth"
    and the other rater as "prediction" — standard TP/FP/FN counted directly
    off the confusion matrix (task-packets/N1-T01.yaml R1), in
    ``categories`` order.

    For category ``k`` with ``reference == "a"`` (rows are truth, columns
    are prediction): ``TP = matrix[k][k]``, ``FN = row_k - TP`` (truth said
    k, prediction said something else), ``FP = col_k - TP`` (prediction said
    k, truth said something else). ``reference == "b"`` is the transpose.

    A category with zero support in the reference rater's own marginal
    yields ``precision=recall=f1=None``, ``reason="no support"`` (task-
    packets/N1-T01.yaml R1's exact edge case: "F1 ... null with reason 'no
    support', never 0.0 and never a division error" — extended here to
    precision/recall too, since both are equally undefined, not just F1,
    for a category the reference rater never actually used). A category
    WITH support but zero true positives (a total miss: precision=recall=0)
    reports the well-defined ``f1=0.0`` limit, not ``None`` — that is not an
    undefined case, it is total predictive failure on an otherwise-scored
    category.
    """
    size = len(categories)
    rows = row_marginals(matrix)
    cols = col_marginals(matrix)
    results = []
    for k in range(size):
        if reference == "a":
            true_positive = matrix[k][k]
            support = rows[k]
            predicted = cols[k]
        else:
            true_positive = matrix[k][k]
            support = cols[k]
            predicted = rows[k]
        false_negative = support - true_positive
        false_positive = predicted - true_positive

        if support == 0:
            results.append(
                CategoryPrf(
                    category=categories[k],
                    support=support,
                    true_positive=true_positive,
                    false_positive=false_positive,
                    false_negative=false_negative,
                    precision=None,
                    recall=None,
                    f1=None,
                    reason=NO_SUPPORT,
                )
            )
            continue

        recall = Fraction(true_positive, support)
        precision = Fraction(true_positive, predicted) if predicted > 0 else Fraction(0)
        if true_positive == 0:
            f1 = Fraction(0)
        else:
            f1 = Fraction(2) * precision * recall / (precision + recall)

        results.append(
            CategoryPrf(
                category=categories[k],
                support=support,
                true_positive=true_positive,
                false_positive=false_positive,
                false_negative=false_negative,
                precision=float(precision),
                recall=float(recall),
                f1=float(f1),
                reason=None,
            )
        )
    return tuple(results)
