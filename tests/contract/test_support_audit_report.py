"""Contract test for ``mrr.domain.support_audit_report`` (task-packets/
N2-T03b.yaml, contract tier): the R3-equivalent model validates its own
instances (``extra="forbid"`` rejects an unknown field) — mirrors
``tests/contract/test_anchoring_integrity_report_contract.py``'s/``tests
/contract/test_field_observation_report_contract.py``'s own precedent,
applied here to a PURE PROJECTION model with no JSON-Schema mirror.

This file ALSO carries the acceptance test running
``mrr.services.support_audit.service.SupportAuditService`` over the REAL
committed ``corpora/research-records/support-batch.v1.json`` and reproducing
the full 34-entry acceptance oracle fixed in task-packets/N2-T03b.yaml
BEFORE the build — task-packets/N2-T03b.yaml's own allowed_paths names this
one contract-tier file, so both concerns (Pydantic contract, real-input
acceptance) live here together rather than in a separate acceptance module.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mrr.domain.support_audit import (
    ExclusionVerdict,
    FigureVerdict,
    MatchedWindow,
    QuotationVerdict,
    build_exclusion_verdict,
)
from mrr.domain.support_audit_report import (
    ExclusionVerdictRow,
    FigureVerdictRow,
    SupportAuditCounts,
    SupportAuditReport,
    build_support_audit_report,
    render_json,
    render_markdown,
)
from mrr.services.support_audit.service import SupportAuditService
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
BATCH_PATH = REPO_ROOT / "corpora" / "research-records" / "support-batch.v1.json"


def _report() -> SupportAuditReport:
    figure_verdicts = [
        FigureVerdict(
            claim_id="fig-1",
            citation_id="cit-1",
            status="figure_supported_in_excerpt",
            matched_windows=(MatchedWindow(token="12", window_text="runs for 12 hours"),),
        ),
        FigureVerdict(
            claim_id="fig-2",
            citation_id="cit-1",
            status="figure_absent_from_checked_excerpt",
            matched_windows=(),
        ),
    ]
    quotation_verdicts = [
        QuotationVerdict(
            claim_id="quote-1",
            citation_id="cit-1",
            status="quotation_verbatim",
            matched_text="exact phrase",
        ),
    ]
    exclusion_verdicts: list[ExclusionVerdict] = [
        build_exclusion_verdict(claim_id="ex-1", citation_id="cit-1", exclusion_reason="withdrawn")
    ]
    return build_support_audit_report(
        batch_id="test-batch",
        audit_target="a test target",
        quotation_similarity_threshold=0.75,
        figure_verdicts=figure_verdicts,
        quotation_verdicts=quotation_verdicts,
        exclusion_verdicts=exclusion_verdicts,
    )


# ---------------------------------------------------------------------------
# Pydantic contract.
# ---------------------------------------------------------------------------


def test_support_audit_report_model_validates_a_well_formed_instance() -> None:
    report = _report()
    SupportAuditReport.model_validate(report.model_dump())


def test_support_audit_report_rejects_an_unknown_top_level_field() -> None:
    payload = _report().model_dump()
    payload["not_a_declared_field"] = "surprise"
    with pytest.raises(ValidationError):
        SupportAuditReport.model_validate(payload)


def test_support_audit_report_presence_is_not_support_cannot_be_false() -> None:
    payload = _report().model_dump()
    payload["presence_is_not_support"] = False
    with pytest.raises(ValidationError):
        SupportAuditReport.model_validate(payload)


def test_support_audit_report_checked_excerpt_is_abstract_only_cannot_be_false() -> None:
    payload = _report().model_dump()
    payload["checked_excerpt_is_abstract_only"] = False
    with pytest.raises(ValidationError):
        SupportAuditReport.model_validate(payload)


@pytest.mark.parametrize("invalid_threshold", [0.0, -0.1, 1.0001, 2.0])
def test_support_audit_report_rejects_a_quotation_similarity_threshold_outside_0_1(
    invalid_threshold: float,
) -> None:
    """Post-review correction: the threshold is a required, bounded field —
    it cannot be constructed as 0, negative, or above 1.
    """
    payload = _report().model_dump()
    payload["quotation_similarity_threshold"] = invalid_threshold
    with pytest.raises(ValidationError):
        SupportAuditReport.model_validate(payload)


def test_support_audit_report_accepts_quotation_similarity_threshold_of_exactly_one() -> None:
    payload = _report().model_dump()
    payload["quotation_similarity_threshold"] = 1.0
    SupportAuditReport.model_validate(payload)  # must not raise


def test_support_audit_report_requires_quotation_similarity_threshold() -> None:
    payload = _report().model_dump()
    del payload["quotation_similarity_threshold"]
    with pytest.raises(ValidationError):
        SupportAuditReport.model_validate(payload)


def test_figure_verdict_row_rejects_a_status_outside_the_closed_set() -> None:
    payload = {
        "claim_id": "fig-1",
        "citation_id": "cit-1",
        "status": "figure_contradicted",
        "matched_windows": [],
    }
    with pytest.raises(ValidationError):
        FigureVerdictRow.model_validate(payload)


def test_no_figure_contradicted_status_exists_anywhere_in_the_closed_set() -> None:
    """The hardest single rule of this packet: there is no
    'figure_contradicted' status. Proven structurally — Pydantic's
    ``Literal`` validator rejects it outright.
    """
    payload = {
        "claim_id": "fig-1",
        "citation_id": "cit-1",
        "status": "figure_contradicted",
        "matched_windows": [],
    }
    with pytest.raises(ValidationError, match="figure_contradicted|Input should be"):
        FigureVerdictRow.model_validate(payload)


def test_exclusion_verdict_row_rejects_an_unknown_field() -> None:
    payload = {
        "claim_id": "ex-1",
        "citation_id": "cit-1",
        "status": "claim_excluded",
        "exclusion_reason": "withdrawn",
        "extra_field": "not declared",
    }
    with pytest.raises(ValidationError):
        ExclusionVerdictRow.model_validate(payload)


def test_support_audit_counts_rejects_negative_values() -> None:
    payload = {
        "figure_supported_in_excerpt": -1,
        "figure_absent_from_checked_excerpt": 0,
        "quotation_verbatim": 0,
        "quotation_absent_from_checked_excerpt": 0,
        "quotation_altered": 0,
        "claim_excluded": 0,
        "total": 0,
        "violations": 0,
        "observations": 0,
    }
    with pytest.raises(ValidationError):
        SupportAuditCounts.model_validate(payload)


def test_support_audit_counts_rejects_an_unknown_field_including_a_collapsed_problems_field() -> (
    None
):
    """The exact collapsing this packet must never do: a "problems" field
    combining violations and observations."""
    payload = {
        "figure_supported_in_excerpt": 0,
        "figure_absent_from_checked_excerpt": 0,
        "quotation_verbatim": 0,
        "quotation_absent_from_checked_excerpt": 0,
        "quotation_altered": 0,
        "claim_excluded": 0,
        "total": 0,
        "violations": 0,
        "observations": 0,
        "problems": 0,
    }
    with pytest.raises(ValidationError):
        SupportAuditCounts.model_validate(payload)


def test_build_support_audit_report_computes_violations_and_observations_correctly() -> None:
    report = _report()
    assert report.counts.total == 4
    assert report.counts.violations == 0
    assert report.counts.observations == 1
    assert report.counts.figure_supported_in_excerpt == 1
    assert report.counts.figure_absent_from_checked_excerpt == 1
    assert report.counts.quotation_verbatim == 1
    assert report.counts.claim_excluded == 1


def test_figures_quotations_exclusions_are_sorted_by_claim_id() -> None:
    report = _report()
    assert [row.claim_id for row in report.figures] == sorted(
        row.claim_id for row in report.figures
    )
    assert [row.claim_id for row in report.quotations] == sorted(
        row.claim_id for row in report.quotations
    )
    assert [row.claim_id for row in report.exclusions] == sorted(
        row.claim_id for row in report.exclusions
    )


# ---------------------------------------------------------------------------
# Renderer byte-stability.
# ---------------------------------------------------------------------------


def test_two_markdown_renders_are_byte_identical() -> None:
    report = _report()
    assert render_markdown(report) == render_markdown(report)
    assert render_markdown(_report()) == render_markdown(_report())


def test_two_json_renders_are_byte_identical() -> None:
    report = _report()
    assert render_json(report) == render_json(_report())


def test_rendered_markdown_names_the_honesty_header() -> None:
    rendered = render_markdown(_report())
    assert "presence_is_not_support" in rendered
    assert "checked_excerpt_is_abstract_only" in rendered
    assert "abstract" in rendered.lower()


def test_rendered_markdown_shows_a_resolved_figures_matched_window() -> None:
    rendered = render_markdown(_report())
    assert "runs for 12 hours" in rendered


def test_rendered_markdown_shows_the_quotation_similarity_threshold_used() -> None:
    """task-packets/N2-T03b.yaml post-review correction: a reader must be
    able to see, in the rendered report itself, which threshold decided
    every quotation_altered/quotation_absent_from_checked_excerpt verdict.
    """
    rendered = render_markdown(_report())
    assert "quotation_similarity_threshold" in rendered
    assert "0.75" in rendered


def test_rendered_json_carries_the_quotation_similarity_threshold_used() -> None:
    report = _report()
    rendered = render_json(report)
    assert '"quotation_similarity_threshold": 0.75' in rendered


# ---------------------------------------------------------------------------
# Acceptance: the REAL committed corpora/research-records/support-batch.v1.json
# reproduces the full 34-entry oracle fixed in task-packets/N2-T03b.yaml
# BEFORE this build, computed by a separate implementation (AGENTS rule 8).
# ---------------------------------------------------------------------------

#: Copied verbatim from task-packets/N2-T03b.yaml's own acceptance_oracle.expected.
_EXPECTED_STATUSES = {
    "sakana-six-stages": "figure_absent_from_checked_excerpt",
    "sakana-workshop-score": "figure_absent_from_checked_excerpt",
    "sakana-acceptance-ratio": "figure_absent_from_checked_excerpt",
    "sakana-manual-filtering": "quotation_absent_from_checked_excerpt",
    "kosmos-run-hours": "figure_supported_in_excerpt",
    "kosmos-rollouts": "figure_supported_in_excerpt",
    "kosmos-code-lines": "figure_supported_in_excerpt",
    "kosmos-papers-read": "figure_supported_in_excerpt",
    "kosmos-overall-accuracy": "figure_supported_in_excerpt",
    "kosmos-data-analysis-accuracy": "figure_absent_from_checked_excerpt",
    "kosmos-literature-accuracy": "figure_absent_from_checked_excerpt",
    "kosmos-synthesis-accuracy": "figure_absent_from_checked_excerpt",
    "kosmos-audit-statement-count": "figure_absent_from_checked_excerpt",
    "agent-laboratory-stages": "figure_supported_in_excerpt",
    "agent-laboratory-neurips-scores": "claim_excluded",
    "beel-sample-size": "figure_absent_from_checked_excerpt",
    "beel-coding-failure-rate": "figure_supported_in_excerpt",
    "beel-all-ideas-novel": "figure_absent_from_checked_excerpt",
    "beel-median-citations": "figure_absent_from_checked_excerpt",
    "deeptrace-citation-accuracy": "figure_supported_in_excerpt",
    "zhu-experimental-weakness-share": "figure_absent_from_checked_excerpt",
    "zhu-methodological-flaw-share": "figure_absent_from_checked_excerpt",
    "neurips-fabricated-reference-count": "claim_excluded",
    "sciintegrity-model-count": "figure_supported_in_excerpt",
    "sciintegrity-run-count": "figure_supported_in_excerpt",
    "sciintegrity-problem-rate": "figure_supported_in_excerpt",
    "sciintegrity-disclosure-ablation": "figure_supported_in_excerpt",
    "sciintegrity-fabrication-invariance": "figure_absent_from_checked_excerpt",
    "llm-judge-accuracy-ceiling": "figure_absent_from_checked_excerpt",
    "iclr-desk-rejects": "claim_excluded",
    "aar-inference-chain-transient": "quotation_absent_from_checked_excerpt",
    "aar-contradiction-transparency": "quotation_verbatim",
    "citation-hallucination-range": "figure_absent_from_checked_excerpt",
    "inspectable-llm-not-author": "quotation_absent_from_checked_excerpt",
}


def _build_real_report() -> SupportAuditReport:
    return SupportAuditService().build_report(BATCH_PATH)


def test_real_committed_batch_descriptor_exists() -> None:
    assert BATCH_PATH.exists()


def test_real_report_carries_the_committed_quotation_similarity_threshold() -> None:
    """The value pinned in corpora/research-records/claims.manifest.json's
    own ``quotation_similarity_threshold`` — 0.75, chosen at build time and
    now hashed/gated exactly like ``anchor_window_chars``.
    """
    report = _build_real_report()
    assert report.quotation_similarity_threshold == 0.75


def test_real_rendered_report_shows_the_threshold_in_both_formats() -> None:
    report = _build_real_report()
    assert "0.75" in render_markdown(report)
    assert '"quotation_similarity_threshold": 0.75' in render_json(report)


def test_real_inputs_reproduce_the_full_34_entry_acceptance_oracle_exactly() -> None:
    report = _build_real_report()

    computed: dict[str, str] = {}
    for figure_row in report.figures:
        computed[figure_row.claim_id] = figure_row.status
    for quotation_row in report.quotations:
        computed[quotation_row.claim_id] = quotation_row.status
    for exclusion_row in report.exclusions:
        computed[exclusion_row.claim_id] = exclusion_row.status

    assert computed == _EXPECTED_STATUSES


def test_real_inputs_total_is_34() -> None:
    report = _build_real_report()
    assert report.counts.total == 34


def test_real_inputs_violation_total_is_zero_and_observation_total_is_18() -> None:
    """task-packets/N2-T03b.yaml acceptance_criteria: "a test asserts the
    report's violation total is 0 and its observation total is 18 on the
    real committed inputs".
    """
    report = _build_real_report()
    assert report.counts.violations == 0
    assert report.counts.observations == 18


def test_real_inputs_counts_match_the_oracles_totals_block_exactly() -> None:
    report = _build_real_report()
    assert report.counts.figure_supported_in_excerpt == 12
    assert report.counts.figure_absent_from_checked_excerpt == 15
    assert report.counts.quotation_verbatim == 1
    assert report.counts.quotation_absent_from_checked_excerpt == 3
    assert report.counts.quotation_altered == 0
    assert report.counts.claim_excluded == 3


def test_sharp_case_deeptrace_citation_accuracy_is_supported() -> None:
    """task-packets/N2-T03b.yaml's own sharp_cases: the abstract carries it
    verbatim ("citation accuracy ranging from 40--80% across systems") —
    proves the audit does not simply answer "absent" to everything, and is
    the very figure four design documents misattributed as a gap until the
    2026-07-25 correction.
    """
    report = _build_real_report()
    row = next(r for r in report.figures if r.claim_id == "deeptrace-citation-accuracy")
    assert row.status == "figure_supported_in_excerpt"
    assert {window.token for window in row.matched_windows} == {"40", "80"}


def test_sharp_case_kosmos_synthesis_accuracy_is_absent_not_refuted() -> None:
    """task-packets/N2-T03b.yaml's own sharp_cases: the 57.9% the capability
    roadmap's whole staging rests on is NOT in the Kosmos abstract — an
    observation, never a refutation.
    """
    report = _build_real_report()
    row = next(r for r in report.figures if r.claim_id == "kosmos-synthesis-accuracy")
    assert row.status == "figure_absent_from_checked_excerpt"
    assert row.matched_windows == ()


def test_no_run_may_count_an_absent_status_as_a_violation() -> None:
    report = _build_real_report()
    absent_figures = sum(
        1
        for figure_row in report.figures
        if figure_row.status == "figure_absent_from_checked_excerpt"
    )
    absent_quotations = sum(
        1
        for quotation_row in report.quotations
        if quotation_row.status == "quotation_absent_from_checked_excerpt"
    )
    assert absent_figures + absent_quotations == report.counts.observations
    assert report.counts.violations == 0


def test_every_supported_figure_renders_a_non_empty_matched_window() -> None:
    report = _build_real_report()
    for row in report.figures:
        if row.status == "figure_supported_in_excerpt":
            assert len(row.matched_windows) >= 1
            for window in row.matched_windows:
                assert window.window_text
        else:
            assert row.matched_windows == ()


def test_real_report_renders_deterministically_across_two_service_runs() -> None:
    first = render_json(_build_real_report())
    second = render_json(_build_real_report())
    assert first == second

    first_md = render_markdown(_build_real_report())
    second_md = render_markdown(_build_real_report())
    assert first_md == second_md
