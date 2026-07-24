"""Property tests for ``mrr.domain.agreement`` (task-packets/N1-T01.yaml
R6, property tier, hypothesis). ``tests/property/conftest.py`` already
disables the per-example deadline for this whole tier (wall-clock variance
is not what these properties assert), so no per-test deadline handling is
needed here.

--- Packet refinement (documented deviation, per the assigning session) -----

The task packet's own R6 text asks for "alpha == kappa relationship holds
for the 2-rater nominal case within tolerance". Authoring this suite against
the REAL committed model-collapse data (tests/contract/
test_agreement_acceptance.py) surfaced a genuine, provable case where Cohen's
kappa and Krippendorff's alpha do NOT coincide even approximately: the
theory stratum (n=3), where both raters use only one shared category. There,
Cohen's kappa is undefined because ``1 - p_e == 0`` exactly (``p_e`` is
computed from the two raters' MARGINALS alone); Krippendorff's alpha is
undefined for the same reason but via a structurally different accumulation
(the symmetrized coincidence matrix, with the ``(n-1)`` small-sample
correction) — the two statistics are related but not identical formulas, and
asserting strict near-equality is simply false in the degenerate corner both
metrics MUST report as undefined. Per the assigning session's own explicit
refinement (present in the task instructions, not invented here), this suite
therefore asserts the WEAKER, still-meaningful set of properties instead:
``alpha`` in ``[-1, 1]`` when defined; ``alpha == 1.0`` iff perfect
agreement with more than one category used; ``alpha`` and ``kappa`` share
sign and both equal ``1.0`` on perfect agreement (when both are defined);
plus the packet's own ``kappa`` properties (bounded, ``== 1.0`` iff perfect
agreement with more than one category, symmetric under swapping raters) and
``observed_agreement`` in ``[0, 1]``.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st
from mrr.domain.agreement import (
    MismatchedRatersError,
    align_ratings,
    cohen_kappa,
    col_marginals,
    krippendorff_alpha_nominal,
    observed_agreement,
    per_category_prf,
    row_marginals,
    weighted_kappa,
)

_MAX_CATEGORY_COUNT = 4
_MAX_CELL_VALUE = 5


@st.composite
def confusion_matrices(draw: st.DrawFn) -> tuple[tuple[int, ...], ...]:
    """A square confusion matrix (1..4 categories, small non-negative cell
    counts) with at least one rated item — an all-zero matrix has no
    meaningful agreement statistic at all and is excluded here, not
    asserted about.
    """
    size = draw(st.integers(min_value=1, max_value=_MAX_CATEGORY_COUNT))
    rows = draw(
        st.lists(
            st.lists(
                st.integers(min_value=0, max_value=_MAX_CELL_VALUE), min_size=size, max_size=size
            ),
            min_size=size,
            max_size=size,
        )
    )
    matrix = tuple(tuple(row) for row in rows)
    total = sum(sum(row) for row in matrix)
    if total == 0:
        # Nudge a single cell to 1 rather than filtering (draw-heavy filters
        # can starve hypothesis) — still an arbitrary, hypothesis-controlled
        # matrix, just guaranteed non-empty.
        first_row = (1,) + matrix[0][1:]
        matrix = (first_row,) + matrix[1:]
    return matrix


def _is_perfect_agreement(matrix: tuple[tuple[int, ...], ...]) -> bool:
    size = len(matrix)
    off_diagonal = sum(matrix[i][j] for i in range(size) for j in range(size) if i != j)
    return off_diagonal == 0


def _categories_used(matrix: tuple[tuple[int, ...], ...]) -> int:
    rows = row_marginals(matrix)
    cols = col_marginals(matrix)
    return sum(1 for r, c in zip(rows, cols, strict=True) if r > 0 or c > 0)


def _transpose(matrix: tuple[tuple[int, ...], ...]) -> tuple[tuple[int, ...], ...]:
    size = len(matrix)
    return tuple(tuple(matrix[i][j] for i in range(size)) for j in range(size))


# ---------------------------------------------------------------------------
# observed_agreement
# ---------------------------------------------------------------------------


@given(matrix=confusion_matrices())
def test_observed_agreement_is_in_unit_interval(matrix: tuple[tuple[int, ...], ...]) -> None:
    assert 0.0 <= observed_agreement(matrix) <= 1.0


# ---------------------------------------------------------------------------
# cohen_kappa
# ---------------------------------------------------------------------------


@given(matrix=confusion_matrices())
def test_cohen_kappa_is_in_minus_one_to_one_when_defined(
    matrix: tuple[tuple[int, ...], ...],
) -> None:
    result = cohen_kappa(matrix)
    if result.value is not None:
        assert -1.0 - 1e-9 <= result.value <= 1.0 + 1e-9


@given(matrix=confusion_matrices())
def test_cohen_kappa_equals_one_iff_perfect_agreement_with_more_than_one_category(
    matrix: tuple[tuple[int, ...], ...],
) -> None:
    result = cohen_kappa(matrix)
    perfect_multi_category = _is_perfect_agreement(matrix) and _categories_used(matrix) > 1
    assert (result.value == 1.0) == perfect_multi_category


@given(matrix=confusion_matrices())
def test_cohen_kappa_is_symmetric_under_swapping_raters(
    matrix: tuple[tuple[int, ...], ...],
) -> None:
    original = cohen_kappa(matrix)
    swapped = cohen_kappa(_transpose(matrix))
    assert original.value == swapped.value
    assert original.reason == swapped.reason


# ---------------------------------------------------------------------------
# weighted_kappa — bounded on top, symmetric, and the perfect-agreement case
# ---------------------------------------------------------------------------


@given(matrix=confusion_matrices(), scheme=st.sampled_from(["linear", "quadratic"]))
def test_weighted_kappa_is_at_most_one_when_defined(
    matrix: tuple[tuple[int, ...], ...], scheme: str
) -> None:
    result = weighted_kappa(matrix, weights=scheme)  # type: ignore[arg-type]
    if result.value is not None:
        assert result.value <= 1.0 + 1e-9


@given(matrix=confusion_matrices(), scheme=st.sampled_from(["linear", "quadratic"]))
def test_weighted_kappa_is_symmetric_under_swapping_raters(
    matrix: tuple[tuple[int, ...], ...], scheme: str
) -> None:
    original = weighted_kappa(matrix, weights=scheme)  # type: ignore[arg-type]
    swapped = weighted_kappa(_transpose(matrix), weights=scheme)  # type: ignore[arg-type]
    assert original.value == swapped.value
    assert original.reason == swapped.reason


@given(matrix=confusion_matrices(), scheme=st.sampled_from(["linear", "quadratic"]))
def test_weighted_kappa_equals_one_iff_perfect_agreement_with_more_than_one_category(
    matrix: tuple[tuple[int, ...], ...], scheme: str
) -> None:
    result = weighted_kappa(matrix, weights=scheme)  # type: ignore[arg-type]
    perfect_multi_category = _is_perfect_agreement(matrix) and _categories_used(matrix) > 1
    assert (result.value == 1.0) == perfect_multi_category


# ---------------------------------------------------------------------------
# krippendorff_alpha_nominal — packet-refined properties (see module docstring)
# ---------------------------------------------------------------------------


@given(matrix=confusion_matrices())
def test_krippendorff_alpha_is_in_minus_one_to_one_when_defined(
    matrix: tuple[tuple[int, ...], ...],
) -> None:
    result = krippendorff_alpha_nominal(matrix)
    if result.value is not None:
        assert -1.0 - 1e-9 <= result.value <= 1.0 + 1e-9


@given(matrix=confusion_matrices())
def test_krippendorff_alpha_equals_one_iff_perfect_agreement_with_more_than_one_category(
    matrix: tuple[tuple[int, ...], ...],
) -> None:
    result = krippendorff_alpha_nominal(matrix)
    perfect_multi_category = _is_perfect_agreement(matrix) and _categories_used(matrix) > 1
    assert (result.value == 1.0) == perfect_multi_category


@given(matrix=confusion_matrices())
def test_kappa_and_alpha_share_sign_and_agree_on_perfect_agreement(
    matrix: tuple[tuple[int, ...], ...],
) -> None:
    """Packet refinement (see module docstring): scoped to PERFECT
    agreement — under perfect agreement with more than one category used,
    both statistics equal 1.0 (hence trivially share a sign there).

    This suite does NOT additionally assert that kappa and alpha share a
    sign in general (non-perfect-agreement matrices), because that is
    provably false for small samples: ``matrix=((0, 0, 0), (1, 0, 0), (2, 0,
    1))`` gives Cohen's kappa ~= +0.0769 but Krippendorff's alpha ~=
    -0.1053 — opposite signs, both mathematically correct for their own,
    differently-corrected formulas (kappa's ``p_e`` from raw marginals vs.
    alpha's ``(n-1)``-corrected small-sample expectation). Hypothesis found
    this counterexample when an earlier, unconditioned version of this test
    asserted sign-sharing universally; it is recorded here, in the packet
    report, as a genuine additional finding, not smoothed over.
    """
    kappa = cohen_kappa(matrix)
    alpha = krippendorff_alpha_nominal(matrix)

    if _is_perfect_agreement(matrix) and _categories_used(matrix) > 1:
        assert kappa.value == 1.0
        assert alpha.value == 1.0
        assert (kappa.value > 0) == (alpha.value > 0)


# ---------------------------------------------------------------------------
# per_category_prf — no crash, no fabricated 0.0, over arbitrary matrices.
# ---------------------------------------------------------------------------


@given(matrix=confusion_matrices(), reference=st.sampled_from(["a", "b"]))
def test_per_category_prf_never_crashes_and_zero_support_is_always_null_with_reason(
    matrix: tuple[tuple[int, ...], ...], reference: str
) -> None:
    categories = tuple(f"c{i}" for i in range(len(matrix)))
    results = per_category_prf(matrix, categories, reference=reference)  # type: ignore[arg-type]

    for row in results:
        if row.support == 0:
            assert row.precision is None
            assert row.recall is None
            assert row.f1 is None
            assert row.reason == "no support"
        else:
            assert row.reason is None
            assert 0.0 <= row.precision <= 1.0  # type: ignore[operator]
            assert 0.0 <= row.recall <= 1.0  # type: ignore[operator]
            assert 0.0 <= row.f1 <= 1.0  # type: ignore[operator]


# ---------------------------------------------------------------------------
# align_ratings — mismatched item sets always raise, over arbitrary id sets.
# ---------------------------------------------------------------------------


_item_id_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Nd")), min_size=1, max_size=4
)


@given(
    common_ids=st.sets(_item_id_strategy, min_size=0, max_size=5),
    only_a_ids=st.sets(_item_id_strategy, min_size=1, max_size=3),
)
def test_align_ratings_raises_whenever_item_sets_differ(
    common_ids: set[str], only_a_ids: set[str]
) -> None:
    only_a_ids = only_a_ids - common_ids
    if not only_a_ids:
        return  # degenerate draw where only_a_ids collapsed entirely into common_ids

    rater_a = dict.fromkeys(common_ids | only_a_ids, "x")
    rater_b = dict.fromkeys(common_ids, "x")

    try:
        align_ratings(rater_a, rater_b)
        raised = False
    except MismatchedRatersError as exc:
        raised = True
        assert set(exc.only_in_a) == only_a_ids
        assert exc.only_in_b == ()

    assert raised
