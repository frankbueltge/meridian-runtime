"""Unit tests for ``mrr.domain.agreement`` (task-packets/N1-T01.yaml R1/R6,
unit tier). DB-free, no fixtures beyond plain confusion matrices / rater
mappings.

Acceptance-test mapping:

- AT4's hand oracle ("a 2x2 confusion with p_o=0.7, p_e=0.5 -> kappa=0.4") ->
  ``test_cohen_kappa_2x2_hand_oracle``.
- AT4's "a supplied weighted-kappa worked example matches" ->
  ``test_weighted_kappa_linear_hand_worked_example``/
  ``test_weighted_kappa_quadratic_hand_worked_example`` (both independently
  cross-checked against a from-scratch ``fractions.Fraction`` computation,
  not merely re-deriving the same code path — see each test's own comment).
- AT4's "a single-category input gives kappa=null-with-reason (no crash)" ->
  ``test_cohen_kappa_single_category_used_is_null_with_reason`` (also
  exercised for weighted kappa and Krippendorff's alpha, since all three
  share the identical zero-expected-chance-variation degeneracy).
- AT4's "a zero-support category gives F1=null-with-reason" ->
  ``test_per_category_prf_zero_support_is_null_with_reason``.
- R1's "raters of unequal length or mismatched item sets raise a typed
  error naming the offending items" ->
  ``test_align_ratings_mismatched_item_sets_raises_naming_the_gap``.
- R1's "a duplicate category / label outside the declared set" ->
  ``test_confusion_matrix_duplicate_categories_raises``,
  ``test_confusion_matrix_unknown_label_raises_naming_item_and_label``.
- Determinism (no ZeroDivisionError, exact equality on the degenerate case)
  is asserted directly via the null-with-reason results above; a
  identical-input-yields-identical-output determinism check for the whole
  report is in tests/unit/domain/test_agreement_report.py.
"""

from __future__ import annotations

from fractions import Fraction

import pytest
from mrr.domain.agreement import (
    NO_SUPPORT,
    UNDEFINED_NO_CHANCE_VARIATION,
    DuplicateCategoryError,
    MismatchedRatersError,
    UnknownCategoryLabelError,
    align_ratings,
    cohen_kappa,
    col_marginals,
    confusion_matrix,
    krippendorff_alpha_nominal,
    majority_baseline,
    observed_agreement,
    per_category_prf,
    row_marginals,
    total_n,
    weighted_kappa,
)

# ---------------------------------------------------------------------------
# align_ratings
# ---------------------------------------------------------------------------


def test_align_ratings_sorts_by_item_id() -> None:
    rater_a = {"b": "x", "a": "y"}
    rater_b = {"b": "x", "a": "y"}
    aligned = align_ratings(rater_a, rater_b)
    assert [r.item_id for r in aligned] == ["a", "b"]


def test_align_ratings_mismatched_item_sets_raises_naming_the_gap() -> None:
    rater_a = {"item-1": "x", "item-2": "y", "item-3": "z"}
    rater_b = {"item-1": "x", "item-2": "y", "item-4": "z"}

    with pytest.raises(MismatchedRatersError) as excinfo:
        align_ratings(rater_a, rater_b)

    assert excinfo.value.only_in_a == ("item-3",)
    assert excinfo.value.only_in_b == ("item-4",)


def test_align_ratings_unequal_length_is_a_mismatched_item_set() -> None:
    rater_a = {"item-1": "x"}
    rater_b = {"item-1": "x", "item-2": "y"}

    with pytest.raises(MismatchedRatersError) as excinfo:
        align_ratings(rater_a, rater_b)

    assert excinfo.value.only_in_a == ()
    assert excinfo.value.only_in_b == ("item-2",)


# ---------------------------------------------------------------------------
# confusion_matrix
# ---------------------------------------------------------------------------


def test_confusion_matrix_counts_correctly() -> None:
    rater_a = {"1": "x", "2": "x", "3": "y"}
    rater_b = {"1": "x", "2": "y", "3": "y"}
    ratings = align_ratings(rater_a, rater_b)

    matrix = confusion_matrix(ratings, ("x", "y"))

    assert matrix == ((1, 1), (0, 1))
    assert total_n(matrix) == 3


def test_confusion_matrix_duplicate_categories_raises() -> None:
    ratings = align_ratings({"1": "x"}, {"1": "x"})
    with pytest.raises(DuplicateCategoryError):
        confusion_matrix(ratings, ("x", "x"))


def test_confusion_matrix_unknown_label_raises_naming_item_and_label() -> None:
    ratings = align_ratings({"1": "z"}, {"1": "x"})
    with pytest.raises(UnknownCategoryLabelError) as excinfo:
        confusion_matrix(ratings, ("x", "y"))

    assert excinfo.value.item_id == "1"
    assert excinfo.value.rater == "a"
    assert excinfo.value.label == "z"


def test_confusion_matrix_retains_zero_support_category_with_zeros() -> None:
    ratings = align_ratings({"1": "x"}, {"1": "x"})
    matrix = confusion_matrix(ratings, ("x", "unused"))
    assert matrix == ((1, 0), (0, 0))


# ---------------------------------------------------------------------------
# observed_agreement / majority_baseline
# ---------------------------------------------------------------------------


def test_observed_agreement_perfect() -> None:
    matrix = ((2, 0), (0, 3))
    assert observed_agreement(matrix) == 1.0


def test_observed_agreement_partial() -> None:
    matrix = ((3, 1), (2, 4))
    assert observed_agreement(matrix) == pytest.approx(7 / 10)


def test_majority_baseline_reads_the_named_reference_marginal() -> None:
    # rater a (rows): 6 + 4 = 10; rater b (cols): 5 + 5 = 10
    matrix = ((4, 2), (1, 3))
    assert row_marginals(matrix) == (6, 4)
    assert col_marginals(matrix) == (5, 5)
    assert majority_baseline(matrix, reference="a") == pytest.approx(0.6)
    assert majority_baseline(matrix, reference="b") == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# cohen_kappa — AT4 hand oracle
# ---------------------------------------------------------------------------


def test_cohen_kappa_2x2_hand_oracle() -> None:
    """task-packets/N1-T01.yaml AT4: a 2x2 confusion with p_o=0.7, p_e=0.5
    gives kappa=0.4.

    Hand derivation: N=100, matrix=((35, 15), (15, 35)) — both raters split
    50/50 (row/col marginals are (50, 50) both sides), so
    p_e = (50/100)*(50/100) + (50/100)*(50/100) = 0.25 + 0.25 = 0.5. The
    diagonal sums to 35 + 35 = 70, so p_o = 70/100 = 0.7.
    kappa = (0.7 - 0.5) / (1 - 0.5) = 0.2 / 0.5 = 0.4.
    """
    matrix = ((35, 15), (15, 35))

    assert observed_agreement(matrix) == pytest.approx(0.7)

    result = cohen_kappa(matrix)
    assert result.observed == pytest.approx(0.7)
    assert result.expected == pytest.approx(0.5)
    assert result.value == pytest.approx(0.4)
    assert result.reason is None


def test_cohen_kappa_single_category_used_is_null_with_reason() -> None:
    """Both raters used only one shared category (the model-collapse theory
    stratum's own real, degenerate shape) -> p_e=1, kappa is undefined —
    never a ZeroDivisionError, never a fabricated 0.0/1.0.
    """
    matrix = ((0, 0), (0, 3))  # only the second category ever used, by both raters

    result = cohen_kappa(matrix)
    assert result.value is None
    assert result.reason == UNDEFINED_NO_CHANCE_VARIATION


def test_cohen_kappa_perfect_agreement_with_more_than_one_category_is_one() -> None:
    matrix = ((5, 0), (0, 5))
    result = cohen_kappa(matrix)
    assert result.value == 1.0
    assert result.reason is None


def test_cohen_kappa_no_data_is_null_with_reason_not_a_crash() -> None:
    matrix = ((0,),)
    result = cohen_kappa(matrix)
    assert result.value is None
    assert result.reason == UNDEFINED_NO_CHANCE_VARIATION


def test_cohen_kappa_is_symmetric_under_swapping_raters() -> None:
    matrix = ((35, 15), (15, 35))
    transposed = tuple(zip(*matrix, strict=True))
    a = cohen_kappa(matrix)
    b = cohen_kappa(transposed)
    assert a.value == pytest.approx(b.value)


# ---------------------------------------------------------------------------
# weighted_kappa — AT4 "a supplied weighted-kappa worked example matches"
# ---------------------------------------------------------------------------


def test_weighted_kappa_linear_hand_worked_example() -> None:
    """Hand derivation (categories ordered low/mid/high, matrix rows=rater
    a, cols=rater b)::

        [[5, 2, 1],
         [1, 6, 1],
         [0, 2, 7]]

    N = 25; row marginals (8, 8, 9); col marginals (6, 10, 9).

    weighted_observed (w=|i-j|) = |0-1|*2 + |0-2|*1 + |1-0|*1 + |1-2|*1 +
    |2-1|*2 = 2 + 2 + 1 + 1 + 2 = 8.

    weighted_expected = sum_ij |i-j| * row_i * col_j / N =
    (8*10 + 2*8*9 + 8*6 + 8*9 + 2*9*6 + 9*10) / 25
    = (80 + 144 + 48 + 72 + 108 + 90) / 25 = 542/25.

    kappa_w = 1 - 8 / (542/25) = 1 - 200/542 = 1 - 100/271 = 171/271.

    Independently cross-checked with a from-scratch ``Fraction``
    computation at authoring time (not merely re-deriving the same code
    path) — see docs/design/2026-07-24-n1-t01-report.md.
    """
    matrix = ((5, 2, 1), (1, 6, 1), (0, 2, 7))

    result = weighted_kappa(matrix, weights="linear")

    assert result.reason is None
    assert result.value == pytest.approx(float(Fraction(171, 271)))


def test_weighted_kappa_quadratic_hand_worked_example() -> None:
    """Same matrix as :func:`test_weighted_kappa_linear_hand_worked_example`,
    weight ``w=(i-j)**2``::

        weighted_observed = 1*2 + 4*1 + 1*1 + 1*1 + 1*2 = 10
        weighted_expected = (8*10 + 4*8*9 + 8*6 + 8*9 + 4*9*6 + 9*10) / 25
                           = (80 + 288 + 48 + 72 + 216 + 90) / 25 = 794/25
        kappa_w = 1 - 10 / (794/25) = 1 - 250/794 = 1 - 125/397 = 272/397.
    """
    matrix = ((5, 2, 1), (1, 6, 1), (0, 2, 7))

    result = weighted_kappa(matrix, weights="quadratic")

    assert result.reason is None
    assert result.value == pytest.approx(float(Fraction(272, 397)))


def test_weighted_kappa_single_category_used_is_null_with_reason() -> None:
    matrix = ((0, 0, 0), (0, 0, 0), (0, 0, 3))
    for scheme in ("linear", "quadratic"):
        result = weighted_kappa(matrix, weights=scheme)
        assert result.value is None
        assert result.reason == UNDEFINED_NO_CHANCE_VARIATION


def test_weighted_kappa_perfect_agreement_with_more_than_one_category_is_one() -> None:
    matrix = ((5, 0, 0), (0, 4, 0), (0, 0, 3))
    for scheme in ("linear", "quadratic"):
        result = weighted_kappa(matrix, weights=scheme)
        assert result.value == 1.0


# ---------------------------------------------------------------------------
# krippendorff_alpha_nominal
# ---------------------------------------------------------------------------


def test_krippendorff_alpha_perfect_agreement_more_than_one_category_is_one() -> None:
    matrix = ((5, 0), (0, 5))
    result = krippendorff_alpha_nominal(matrix)
    assert result.value == 1.0
    assert result.reason is None


def test_krippendorff_alpha_single_category_used_is_null_with_reason() -> None:
    matrix = ((0, 0), (0, 3))
    result = krippendorff_alpha_nominal(matrix)
    assert result.value is None
    assert result.reason == UNDEFINED_NO_CHANCE_VARIATION


def test_krippendorff_alpha_no_data_is_null_with_reason_not_a_crash() -> None:
    matrix = ((0,),)
    result = krippendorff_alpha_nominal(matrix)
    assert result.value is None
    assert result.reason == UNDEFINED_NO_CHANCE_VARIATION


def test_krippendorff_alpha_shares_sign_with_kappa_on_partial_agreement() -> None:
    matrix = ((35, 15), (15, 35))
    kappa = cohen_kappa(matrix)
    alpha = krippendorff_alpha_nominal(matrix)
    assert kappa.value is not None
    assert alpha.value is not None
    assert (kappa.value > 0) == (alpha.value > 0)


# ---------------------------------------------------------------------------
# per_category_prf
# ---------------------------------------------------------------------------


def test_per_category_prf_zero_support_is_null_with_reason() -> None:
    """task-packets/N1-T01.yaml AT4: a zero-support category gives
    F1=null-with-reason (extended here to precision/recall too — both are
    equally undefined, not just F1, for a category the reference rater
    never used).
    """
    matrix = ((5, 0, 0), (0, 3, 0), (0, 0, 0))  # third category never used by rater a
    results = per_category_prf(matrix, ("x", "y", "z"), reference="a")

    third = results[2]
    assert third.category == "z"
    assert third.support == 0
    assert third.precision is None
    assert third.recall is None
    assert third.f1 is None
    assert third.reason == NO_SUPPORT


def test_per_category_prf_perfect_prediction_is_one() -> None:
    matrix = ((5, 0), (0, 3))
    results = per_category_prf(matrix, ("x", "y"), reference="a")
    assert results[0].precision == 1.0
    assert results[0].recall == 1.0
    assert results[0].f1 == 1.0
    assert results[1].precision == 1.0
    assert results[1].recall == 1.0
    assert results[1].f1 == 1.0


def test_per_category_prf_total_miss_with_support_is_zero_not_none() -> None:
    """A category WITH support but zero true positives is the well-defined
    ``f1=0.0`` limit, not an undefined case — distinct from "no support".
    """
    matrix = ((0, 5), (0, 3))  # rater a never predicts "x", but "x" has support 5
    results = per_category_prf(matrix, ("x", "y"), reference="a")
    assert results[0].support == 5
    assert results[0].true_positive == 0
    assert results[0].precision == 0.0
    assert results[0].recall == 0.0
    assert results[0].f1 == 0.0
    assert results[0].reason is None


def test_per_category_prf_reference_side_b_is_the_transpose_view() -> None:
    matrix = ((4, 1), (2, 3))
    a_side = per_category_prf(matrix, ("x", "y"), reference="a")
    b_side = per_category_prf(matrix, ("x", "y"), reference="b")
    # rater a's row marginal for "x" is 5; rater b's column marginal for "x" is 6.
    assert a_side[0].support == 5
    assert b_side[0].support == 6
