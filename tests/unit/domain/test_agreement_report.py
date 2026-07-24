"""Unit tests for ``mrr.domain.agreement_report`` (task-packets/N1-T01.yaml
R2/R6, unit tier): report shape, the below-power flag, the honesty header,
no pooled kappa, and determinism (two renders byte-identical).
"""

from __future__ import annotations

from mrr.domain.agreement import (
    AlphaResult,
    CategoryPrf,
    KappaResult,
    col_marginals,
    row_marginals,
)
from mrr.domain.agreement_report import (
    ItemAgreementRow,
    StratumReport,
    build_agreement_report,
    build_stratum_report,
    render_json,
    render_markdown,
)


def _make_stratum(
    *,
    stratum_id: str,
    n: int,
    matrix: tuple[tuple[int, ...], ...],
    categories: tuple[str, ...],
    cohen_value: float | None,
    cohen_reason: str | None,
) -> StratumReport:
    per_category = tuple(
        CategoryPrf(
            category=c,
            support=row_marginals(matrix)[i],
            true_positive=matrix[i][i],
            false_positive=col_marginals(matrix)[i] - matrix[i][i],
            false_negative=row_marginals(matrix)[i] - matrix[i][i],
            precision=1.0 if row_marginals(matrix)[i] else None,
            recall=1.0 if row_marginals(matrix)[i] else None,
            f1=1.0 if row_marginals(matrix)[i] else None,
            reason=None if row_marginals(matrix)[i] else "no support",
        )
        for i, c in enumerate(categories)
    )
    items = tuple(
        ItemAgreementRow(
            item_id=f"item-{i}",
            corpus_entry_id=f"entry-{i}",
            title=f"Title {i}",
            rater_a_label=categories[0],
            rater_b_label=categories[0],
            agree=True,
        )
        for i in range(n)
    )
    return build_stratum_report(
        stratum_id=stratum_id,
        rater_a_id="pipeline",
        rater_b_id="blind",
        reference_rater="pipeline",
        categories=categories,
        confusion_matrix=matrix,
        n=n,
        observed_agreement=1.0,
        majority_baseline=1.0,
        prevalence_a=tuple(zip(categories, row_marginals(matrix), strict=True)),
        prevalence_b=tuple(zip(categories, col_marginals(matrix), strict=True)),
        cohen_kappa=KappaResult(value=cohen_value, reason=cohen_reason, observed=1.0, expected=0.0),
        weighted_kappa_linear=KappaResult(
            value=cohen_value, reason=cohen_reason, observed=1.0, expected=0.0
        ),
        weighted_kappa_quadratic=KappaResult(
            value=cohen_value, reason=cohen_reason, observed=1.0, expected=0.0
        ),
        krippendorff_alpha=AlphaResult(value=cohen_value, reason=cohen_reason),
        per_category=per_category,
        items=items,
    )


def test_below_power_true_when_a_category_has_fewer_than_20_instances() -> None:
    matrix = ((15,),)
    stratum = _make_stratum(
        stratum_id="s",
        n=15,
        matrix=matrix,
        categories=("only",),
        cohen_value=None,
        cohen_reason="undefined: no expected-chance variation",
    )
    assert stratum.below_power is True
    assert stratum.below_power_threshold == 20


def test_below_power_false_when_every_category_has_at_least_20() -> None:
    matrix = ((25,),)
    stratum = _make_stratum(
        stratum_id="s",
        n=25,
        matrix=matrix,
        categories=("only",),
        cohen_value=None,
        cohen_reason="undefined: no expected-chance variation",
    )
    assert stratum.below_power is False


def test_instantiation_and_theory_strata_reproduce_below_power_true_at_n_15_and_3() -> None:
    """task-packets/N1-T01.yaml R6: "R2 report shape incl. below_power=True
    at n=15/3" — the exact real strata shapes (14/1/0 split and 0/3 split).
    """
    instantiation_matrix = ((1, 0, 0), (0, 0, 0), (0, 0, 14))
    theory_matrix = ((0, 0), (0, 3))

    instantiation = _make_stratum(
        stratum_id="instantiation-vs-reference-classification",
        n=15,
        matrix=instantiation_matrix,
        categories=("instantiates", "qualifies", "references"),
        cohen_value=1.0,
        cohen_reason=None,
    )
    theory = _make_stratum(
        stratum_id="model-collapse-mechanism-theory-confirmation",
        n=3,
        matrix=theory_matrix,
        categories=("contradicts", "supports"),
        cohen_value=None,
        cohen_reason="undefined: no expected-chance variation",
    )

    assert instantiation.below_power is True
    assert theory.below_power is True


def test_agreement_report_carries_the_honesty_header() -> None:
    stratum = _make_stratum(
        stratum_id="s",
        n=5,
        matrix=((5,),),
        categories=("only",),
        cohen_value=None,
        cohen_reason="undefined: no expected-chance variation",
    )
    report = build_agreement_report(
        reference_rater="pipeline",
        crosswalk_path="corpora/model-collapse/verification/agreement-crosswalk.v1.json",
        crosswalk_sha256="sha256:" + "0" * 64,
        strata=[stratum],
    )

    assert report.measures_reliability_not_validity is True
    assert "RELIABILITY" in report.reliability_note
    assert "NOT VALIDITY" in report.reliability_note
    assert report.reference_rater == "pipeline"
    assert "pipeline" in report.reference_rater_note
    assert report.crosswalk_sha256 == "sha256:" + "0" * 64


def test_agreement_report_has_no_pooled_kappa_field() -> None:
    """task-packets/N1-T01.yaml invariant: no pooled cross-stratum kappa is
    ever emitted — asserted structurally (the field does not exist at all),
    not merely that some value happens to be ``None``.
    """
    report = build_agreement_report(
        reference_rater="pipeline",
        crosswalk_path="x.json",
        crosswalk_sha256="sha256:" + "0" * 64,
        strata=[
            _make_stratum(
                stratum_id="s",
                n=5,
                matrix=((5,),),
                categories=("only",),
                cohen_value=None,
                cohen_reason="undefined: no expected-chance variation",
            )
        ],
    )
    field_names = set(type(report).model_fields)
    assert not any("pooled_kappa" in name for name in field_names)
    assert "pooled_note" in field_names
    assert report.pooled_note


def test_strata_are_ordered_by_stratum_id_regardless_of_input_order() -> None:
    stratum_b = _make_stratum(
        stratum_id="z-stratum",
        n=5,
        matrix=((5,),),
        categories=("only",),
        cohen_value=None,
        cohen_reason="undefined: no expected-chance variation",
    )
    stratum_a = _make_stratum(
        stratum_id="a-stratum",
        n=5,
        matrix=((5,),),
        categories=("only",),
        cohen_value=None,
        cohen_reason="undefined: no expected-chance variation",
    )
    report = build_agreement_report(
        reference_rater="pipeline",
        crosswalk_path="x.json",
        crosswalk_sha256="sha256:" + "0" * 64,
        strata=[stratum_b, stratum_a],
    )
    assert [s.stratum_id for s in report.strata] == ["a-stratum", "z-stratum"]


def test_render_markdown_is_byte_identical_across_two_calls() -> None:
    stratum = _make_stratum(
        stratum_id="s",
        n=5,
        matrix=((5,),),
        categories=("only",),
        cohen_value=None,
        cohen_reason="undefined: no expected-chance variation",
    )
    report = build_agreement_report(
        reference_rater="pipeline",
        crosswalk_path="x.json",
        crosswalk_sha256="sha256:" + "0" * 64,
        strata=[stratum],
    )
    assert render_markdown(report) == render_markdown(report)


def test_render_json_is_byte_identical_across_two_calls_and_sorted() -> None:
    stratum = _make_stratum(
        stratum_id="s",
        n=5,
        matrix=((5,),),
        categories=("only",),
        cohen_value=None,
        cohen_reason="undefined: no expected-chance variation",
    )
    report = build_agreement_report(
        reference_rater="pipeline",
        crosswalk_path="x.json",
        crosswalk_sha256="sha256:" + "0" * 64,
        strata=[stratum],
    )
    first = render_json(report)
    second = render_json(report)
    assert first == second
    assert '"pooled_note"' in first


def test_render_markdown_never_omits_pooling_or_reliability_sections() -> None:
    stratum = _make_stratum(
        stratum_id="s",
        n=5,
        matrix=((5,),),
        categories=("only",),
        cohen_value=None,
        cohen_reason="undefined: no expected-chance variation",
    )
    report = build_agreement_report(
        reference_rater="pipeline",
        crosswalk_path="x.json",
        crosswalk_sha256="sha256:" + "0" * 64,
        strata=[stratum],
    )
    rendered = render_markdown(report)
    assert "## Honesty header" in rendered
    assert "## Pooling" in rendered
    assert "below power" in rendered.lower()
