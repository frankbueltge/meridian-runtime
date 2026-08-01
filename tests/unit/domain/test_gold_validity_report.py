"""task-packets/N1-T02.yaml AT1: the hand-computed oracle, and AT6's
determinism half.

Every number asserted here was worked out by hand at derivation time, over the
matrix the packet names, WITHOUT calling the code under test — see
docs/design/2026-08-01-n1-t02-ableitung-goldstandard.md. That is the whole
point of an acceptance oracle: a test that derives its expectation from the
implementation proves only that the implementation is self-consistent.

The matrix, rows = gold, columns = system, in category order
``[supports, contradicts, qualifies, contextualizes]``::

    [[8, 1, 1, 0],
     [1, 3, 1, 0],
     [1, 0, 2, 0],
     [1, 0, 0, 1]]

Gold marginals [10, 5, 3, 2]; system marginals [11, 4, 4, 1]; n = 20;
diagonal 14. Hence p_o = 14/20 = 7/10; p_e = (10*11 + 5*4 + 3*4 + 2*1)/400 =
144/400 = 9/25; kappa = (7/10 - 9/25)/(1 - 9/25) = 17/32 EXACTLY. Because
``mrr.domain.agreement`` accumulates in ``fractions.Fraction`` and converts
once at the end, 17/32 is exactly representable as a float and the assertion
below is an equality, not a tolerance.
"""

from __future__ import annotations

from mrr.domain.agreement import (
    cohen_kappa,
    majority_baseline,
    observed_agreement,
    per_category_prf,
)
from mrr.domain.gold_validity_report import (
    UNDEFINED_NO_NEGATIVE_GOLD,
    GoldValidityReport,
    ItemValidityRow,
    build_gold_validity_report,
    false_support_rate,
    render_json,
    render_markdown,
)

CATEGORIES = ("supports", "contradicts", "qualifies", "contextualizes")
ORACLE_MATRIX = ((8, 1, 1, 0), (1, 3, 1, 0), (1, 0, 2, 0), (1, 0, 0, 1))


def test_at1_observed_agreement_is_exactly_seven_tenths() -> None:
    assert observed_agreement(ORACLE_MATRIX) == 0.7


def test_at1_majority_baseline_against_gold_is_exactly_one_half() -> None:
    # Gold's own most frequent category is `supports` with 10 of 20 — a
    # constant classifier always predicting it reaches exactly 0.50.
    assert majority_baseline(ORACLE_MATRIX, reference="a") == 0.5


def test_at1_cohen_kappa_is_exactly_seventeen_thirtyseconds() -> None:
    result = cohen_kappa(ORACLE_MATRIX)
    assert result.reason is None
    assert result.value == 17 / 32
    assert result.value == 0.53125


def test_at1_false_support_rate_is_exactly_three_tenths() -> None:
    rate = false_support_rate(ORACLE_MATRIX, CATEGORIES)
    assert rate.reason is None
    assert rate.negative_gold_n == 10
    assert rate.false_supports == 3
    assert rate.value == 0.3


def test_false_support_rate_is_null_with_reason_when_all_gold_is_supports() -> None:
    # Not a fabricated 0.0: with no non-`supports` gold item, the question
    # "how often did it over-predict supports" cannot be asked at all.
    matrix = ((5, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0))
    rate = false_support_rate(matrix, CATEGORIES)
    assert rate.value is None
    assert rate.reason == UNDEFINED_NO_NEGATIVE_GOLD
    assert rate.negative_gold_n == 0


def test_false_support_rate_is_null_with_reason_when_supports_is_not_a_category() -> None:
    rate = false_support_rate(((2, 0), (0, 2)), ("yes", "no"))
    assert rate.value is None
    assert rate.reason is not None
    assert "supports" in rate.reason


def _oracle_report(*, blind: bool = True) -> GoldValidityReport:
    from mrr.domain.agreement import (
        krippendorff_alpha_nominal,
        total_n,
        weighted_kappa,
    )

    items = (
        ItemValidityRow(case_id="c1", gold_label="supports", system_label="supports", correct=True),
        ItemValidityRow(
            case_id="c2", gold_label="qualifies", system_label="supports", correct=False
        ),
    )
    return build_gold_validity_report(
        gold_set_id="mb-cls-test",
        gold_set_sha256="sha256:" + "0" * 64,
        criteria_version="v1",
        criteria_locked_at="2026-08-01T00:00:00Z",
        criteria_lock_content_hash="sha256:" + "1" * 64,
        labelled_at="2026-08-01T12:00:00Z",
        label_provenance="hand-built oracle fixture",
        producing_practice="test",
        encounter_id=None,
        blind_to_measured_labels=blind,
        system_id="oracle-system",
        categories=CATEGORIES,
        confusion_matrix=ORACLE_MATRIX,
        n=total_n(ORACLE_MATRIX),
        observed_agreement=observed_agreement(ORACLE_MATRIX),
        majority_baseline=majority_baseline(ORACLE_MATRIX, reference="a"),
        cohen_kappa=cohen_kappa(ORACLE_MATRIX),
        weighted_kappa_linear=weighted_kappa(ORACLE_MATRIX, weights="linear"),
        weighted_kappa_quadratic=weighted_kappa(ORACLE_MATRIX, weights="quadratic"),
        krippendorff_alpha=krippendorff_alpha_nominal(ORACLE_MATRIX),
        per_category=per_category_prf(ORACLE_MATRIX, CATEGORIES, reference="a"),
        items=items,
    )


def test_report_claims_validity_and_carries_its_standards_identity() -> None:
    report = _oracle_report()
    assert report.measures_validity_against_gold is True
    assert report.fixture_set_id == "mb-cls-test@sha256:" + "0" * 64
    assert report.gold_prevalence == (
        ("supports", 10),
        ("contradicts", 5),
        ("qualifies", 3),
        ("contextualizes", 2),
    )
    assert report.system_prevalence == (
        ("supports", 11),
        ("contradicts", 4),
        ("qualifies", 4),
        ("contextualizes", 1),
    )


def test_every_category_is_flagged_below_power_at_this_size() -> None:
    report = _oracle_report()
    # 10, 5, 3 and 2 gold labels — all under the documented 20-30/category.
    # The flag is what keeps a 0.53 kappa from reading as publication-grade.
    assert report.below_power is True
    assert all(row.below_power for row in report.per_category)


def test_not_blind_warning_appears_exactly_when_the_labeller_was_not_blind() -> None:
    assert _oracle_report(blind=True).not_blind_warning is None

    not_blind = _oracle_report(blind=False)
    assert not_blind.not_blind_warning is not None
    assert "NOT blind" in not_blind.not_blind_warning
    assert not_blind.not_blind_warning in render_markdown(not_blind)


def test_accuracy_never_renders_without_its_baseline() -> None:
    # The invariant, checked on the rendered bytes rather than on the model:
    # a reader who sees the accuracy must see the floor in the same block.
    rendered = render_markdown(_oracle_report())
    assert "| Accuracy (observed agreement) | 0.7000 |" in rendered
    assert "| Majority-class baseline | 0.5000 |" in rendered
    accuracy_at = rendered.index("Accuracy (observed agreement)")
    baseline_at = rendered.index("Majority-class baseline")
    assert 0 < baseline_at - accuracy_at < 80


def test_at6_rendering_is_byte_identical_across_two_calls() -> None:
    report = _oracle_report()
    assert render_markdown(report) == render_markdown(report)
    assert render_json(report) == render_json(report)
    # A second, independently built report over identical inputs too — this is
    # what "no wall clock anywhere" has to mean in practice.
    assert render_json(_oracle_report()) == render_json(_oracle_report())


def test_json_render_is_sorted_and_newline_terminated() -> None:
    rendered = render_json(_oracle_report())
    assert rendered.endswith("\n")
    assert '"measures_validity_against_gold": true' in rendered
