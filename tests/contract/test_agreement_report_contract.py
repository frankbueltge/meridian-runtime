"""Contract test for ``mrr.domain.agreement_report`` (task-packets/
N1-T01.yaml R6, contract tier): "the R2 model validates its own instances
(extra='forbid' rejects an unknown field)". Mirrors ``mrr.contracts.common
.MRRModel``'s ``extra="forbid"`` closure — the same discipline every entity
schema in schemas/ enforces (tests/contract/test_examples.py), applied here
to a PURE PROJECTION model that has no JSON-Schema mirror at all (task-
packets/N1-T01.yaml R2: "NOT a BaseObject, NO schemas/*.schema.json
mirror").
"""

from __future__ import annotations

import pytest
from mrr.domain.agreement import AlphaResult, CategoryPrf, KappaResult
from mrr.domain.agreement_report import (
    AgreementReport,
    CategoryPrfRow,
    ItemAgreementRow,
    KappaField,
    StratumReport,
    build_agreement_report,
    build_stratum_report,
)
from pydantic import ValidationError

_UNDEFINED_REASON = "undefined: no expected-chance variation"


def _stratum() -> StratumReport:
    matrix = ((5,),)
    return build_stratum_report(
        stratum_id="s",
        rater_a_id="pipeline",
        rater_b_id="blind",
        reference_rater="pipeline",
        categories=("only",),
        confusion_matrix=matrix,
        n=5,
        observed_agreement=1.0,
        majority_baseline=1.0,
        prevalence_a=(("only", 5),),
        prevalence_b=(("only", 5),),
        cohen_kappa=KappaResult(value=None, reason=_UNDEFINED_REASON, observed=1.0, expected=1.0),
        weighted_kappa_linear=KappaResult(
            value=None, reason=_UNDEFINED_REASON, observed=1.0, expected=1.0
        ),
        weighted_kappa_quadratic=KappaResult(
            value=None, reason=_UNDEFINED_REASON, observed=1.0, expected=1.0
        ),
        krippendorff_alpha=AlphaResult(value=None, reason=_UNDEFINED_REASON),
        per_category=(
            CategoryPrf(
                category="only",
                support=5,
                true_positive=5,
                false_positive=0,
                false_negative=0,
                precision=1.0,
                recall=1.0,
                f1=1.0,
                reason=None,
            ),
        ),
        items=(
            ItemAgreementRow(
                item_id="i1",
                corpus_entry_id="e1",
                title="Title",
                rater_a_label="only",
                rater_b_label="only",
                agree=True,
            ),
        ),
    )


def _report() -> AgreementReport:
    return build_agreement_report(
        reference_rater="pipeline",
        crosswalk_path="x.json",
        crosswalk_sha256="sha256:" + "0" * 64,
        strata=[_stratum()],
    )


def test_agreement_report_model_validates_a_well_formed_instance() -> None:
    report = _report()
    # Round-trips through model_dump/model_validate without error.
    AgreementReport.model_validate(report.model_dump())


def test_agreement_report_rejects_an_unknown_top_level_field() -> None:
    payload = _report().model_dump()
    payload["not_a_declared_field"] = "surprise"

    with pytest.raises(ValidationError):
        AgreementReport.model_validate(payload)


def test_stratum_report_rejects_an_unknown_field() -> None:
    payload = _stratum().model_dump()
    payload["pooled_kappa"] = 1.0  # exactly the field this report must never have

    with pytest.raises(ValidationError):
        StratumReport.model_validate(payload)


def test_category_prf_row_rejects_an_unknown_field() -> None:
    payload = {
        "category": "x",
        "support": 5,
        "true_positive": 5,
        "false_positive": 0,
        "false_negative": 0,
        "precision": 1.0,
        "recall": 1.0,
        "f1": 1.0,
        "reason": None,
        "extra_field": "not declared",
    }
    with pytest.raises(ValidationError):
        CategoryPrfRow.model_validate(payload)


def test_kappa_field_rejects_an_unknown_field() -> None:
    with pytest.raises(ValidationError):
        KappaField.model_validate({"value": 1.0, "reason": None, "surprise": True})


def test_item_agreement_row_rejects_an_unknown_field() -> None:
    payload = {
        "item_id": "A1",
        "corpus_entry_id": "e1",
        "title": "Title",
        "rater_a_label": "x",
        "rater_b_label": "x",
        "agree": True,
        "surprise": True,
    }
    with pytest.raises(ValidationError):
        ItemAgreementRow.model_validate(payload)


def test_agreement_report_measures_reliability_field_cannot_be_false() -> None:
    payload = _report().model_dump()
    payload["measures_reliability_not_validity"] = False

    with pytest.raises(ValidationError):
        AgreementReport.model_validate(payload)


def test_agreement_report_has_no_pooled_kappa_field_declared_at_all() -> None:
    assert not any("pooled_kappa" in name for name in AgreementReport.model_fields)
