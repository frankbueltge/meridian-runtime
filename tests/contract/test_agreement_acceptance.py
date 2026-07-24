"""Acceptance tests for task-packets/N1-T01.yaml (contract tier, DB-free):
running ``mrr.services.validation.service.ValidationService`` over the REAL
committed model-collapse verification set and its declared crosswalk
reproduces ``corpora/model-collapse/verification/comparison.md``'s own
18/18 agreement claim, stratified, hash-anchored, and honestly labeled.

--- AT1/objective text vs. the theory stratum's real data: a documented, provable spec conflict ---

task-packets/N1-T01.yaml's own objective/AT1 text says the report
"reproduces comparison.md exactly: observed agreement = 1.0 and Cohen's
kappa = 1.0 per stratum". Running the real computation shows this holds
EXACTLY for the instantiation stratum (n=15) but NOT for the theory stratum
(n=3): both raters classify all three theory papers as "supports" — the
OTHER declared category, "contradicts", has zero support on both sides. By
Cohen's kappa's own definition, ``p_e`` (chance-expected agreement) is then
exactly 1 (a constant classifier and both real raters are indistinguishable
from chance here, in the narrow sense that there is no variation across
categories at all), so ``kappa = (p_o - p_e) / (1 - p_e)`` divides by zero —
mathematically UNDEFINED, not 1.0. This is not a bug: it is exactly the
"single category used by both raters" edge case task-packets/N1-T01.yaml R1
itself names and requires be reported as null-with-reason, "never a
fabricated 0.0/1.0" (AGENTS.md rule 12). The packet's own stop_condition 2
("going green would require weakening an edge-case rule ... may not be
softened") governs here: this suite asserts the mathematically HONEST
result (kappa is null-with-reason for the theory stratum) rather than
forcing 1.0, and this conflict is flagged again, prominently, in the packet
report per AGENTS.md's "Required delivery format ... any specification
conflict discovered".

Observed agreement (p_o) = 1.0 DOES hold for BOTH strata, exactly as
comparison.md's "18/18 agreement" claims — it is specifically the
CHANCE-CORRECTED statistic that the theory stratum's degenerate,
zero-variation sample cannot support.
"""

from __future__ import annotations

import re
from pathlib import Path

from mrr.domain.agreement import UNDEFINED_NO_CHANCE_VARIATION
from mrr.domain.agreement_report import render_json, render_markdown
from mrr.services.validation.service import ValidationService

REPO_ROOT = Path(__file__).resolve().parents[2]
CROSSWALK_PATH = (
    REPO_ROOT / "corpora" / "model-collapse" / "verification" / "agreement-crosswalk.v1.json"
)
COMPARISON_MD_PATH = REPO_ROOT / "corpora" / "model-collapse" / "verification" / "comparison.md"

_INSTANTIATION_STRATUM_ID = "instantiation-vs-reference-classification"
_THEORY_STRATUM_ID = "model-collapse-mechanism-theory-confirmation"


def _parse_comparison_md_rows() -> list[dict[str, str]]:
    """Parse comparison.md's own Markdown table into a list of row dicts —
    read live from the committed oracle file at test time (never
    transcribed/hardcoded here), so this test can never silently drift from
    what comparison.md actually says.
    """
    text = COMPARISON_MD_PATH.read_text(encoding="utf-8")
    rows = []
    for line in text.splitlines():
        if not line.startswith("| "):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 5 or cells[0] in ("Item", "---"):
            continue
        if re.fullmatch(r"-+", cells[0]):
            continue
        item, title, blind_verdict, pipeline_relation, agreement = cells
        rows.append(
            {
                "item": item,
                "title": title,
                "blind_verdict": blind_verdict,
                "pipeline_relation": pipeline_relation,
                "agreement": agreement,
            }
        )
    return rows


def test_comparison_md_oracle_is_18_18_agreement_as_asserted() -> None:
    """Sanity check on the parser itself and the oracle file: exactly 18
    rows, every one YES — this is the "18/18" comparison.md's own prose
    claims (and this suite goes on to make auditable/hash-anchored).
    """
    rows = _parse_comparison_md_rows()
    assert len(rows) == 18
    assert all(row["agreement"] == "YES" for row in rows)


def test_at1_observed_agreement_is_1_0_on_both_strata() -> None:
    report = ValidationService().build_report(CROSSWALK_PATH)
    by_id = {stratum.stratum_id: stratum for stratum in report.strata}

    assert set(by_id) == {_INSTANTIATION_STRATUM_ID, _THEORY_STRATUM_ID}
    assert by_id[_INSTANTIATION_STRATUM_ID].observed_agreement == 1.0
    assert by_id[_THEORY_STRATUM_ID].observed_agreement == 1.0
    assert by_id[_INSTANTIATION_STRATUM_ID].n == 15
    assert by_id[_THEORY_STRATUM_ID].n == 3


def test_at1_instantiation_stratum_cohen_kappa_is_1_0() -> None:
    report = ValidationService().build_report(CROSSWALK_PATH)
    by_id = {stratum.stratum_id: stratum for stratum in report.strata}

    assert by_id[_INSTANTIATION_STRATUM_ID].cohen_kappa.value == 1.0
    assert by_id[_INSTANTIATION_STRATUM_ID].cohen_kappa.reason is None


def test_at1_theory_stratum_kappa_is_honestly_undefined_not_fabricated_one() -> None:
    """See this module's own docstring: the packet's objective/AT1 prose
    ("Cohen's kappa = 1.0 per stratum") does not survive contact with the
    theory stratum's real data (n=3, both raters use only "supports",
    "contradicts" has zero support on both sides -> p_e == 1 exactly). This
    is the documented, provable spec conflict — asserted here as the
    correct, honest behavior (null-with-reason), never coerced to 1.0.
    """
    report = ValidationService().build_report(CROSSWALK_PATH)
    by_id = {stratum.stratum_id: stratum for stratum in report.strata}

    theory = by_id[_THEORY_STRATUM_ID]
    assert theory.cohen_kappa.value is None
    assert theory.cohen_kappa.reason == UNDEFINED_NO_CHANCE_VARIATION
    assert theory.weighted_kappa_linear.value is None
    assert theory.weighted_kappa_quadratic.value is None
    assert theory.krippendorff_alpha.value is None


def test_at1_item_by_item_common_space_labels_match_comparison_md_agreement_column() -> None:
    """task-packets/N1-T01.yaml AT1: "the item-by-item common-space labels
    match comparison.md's Agreement column exactly" — parsed live from
    comparison.md, cross-checked against the service's own per-item rows.

    ``comparison.md``'s own Markdown table TRUNCATES several long titles to
    fit a fixed-width column (e.g. row A4's title is literally cut off mid-
    word at "... Tales of Narrativeles", row A11's at "... Curated by a
    Mach", row B1's at "... Training on Generated") — a display artifact of
    the oracle file itself, not a transcription error in the crosswalk
    (which carries the full, real title, verbatim from corpus-entries.json/
    blind-returns.json). Title comparison here is therefore "the full title
    starts with whatever comparison.md's own (possibly truncated) column
    says", not byte-for-byte equality — item identity is established by
    ``item``/``entry_id``, never by the title string.
    """
    oracle_rows = {row["item"]: row for row in _parse_comparison_md_rows()}
    report = ValidationService().build_report(CROSSWALK_PATH)

    seen_items: set[str] = set()
    for stratum in report.strata:
        for item in stratum.items:
            seen_items.add(item.item_id)
            oracle_row = oracle_rows[item.item_id]
            expected_agree = oracle_row["agreement"] == "YES"
            assert item.agree == expected_agree, (
                f"{item.item_id}: computed agree={item.agree}, "
                f"comparison.md says {oracle_row['agreement']}"
            )
            assert item.title.startswith(oracle_row["title"]), (
                f"{item.item_id}: full title {item.title!r} does not start with "
                f"comparison.md's own (possibly truncated) title {oracle_row['title']!r}"
            )

    assert seen_items == set(oracle_rows)


def test_at2_below_power_flagged_true_on_both_strata_with_documented_threshold() -> None:
    report = ValidationService().build_report(CROSSWALK_PATH)
    for stratum in report.strata:
        assert stratum.below_power is True
        assert stratum.below_power_threshold == 20


def test_at2_no_pooled_kappa_is_emitted_pooled_note_present_instead() -> None:
    report = ValidationService().build_report(CROSSWALK_PATH)
    assert not any("pooled_kappa" in name for name in type(report).model_fields)
    assert report.pooled_note
    assert "pool" in report.pooled_note.lower()


def test_at3_crosswalk_sha256_in_report_equals_actual_file_sha256() -> None:
    import hashlib

    report = ValidationService().build_report(CROSSWALK_PATH)
    expected = f"sha256:{hashlib.sha256(CROSSWALK_PATH.read_bytes()).hexdigest()}"
    assert report.crosswalk_sha256 == expected


def test_at5_two_markdown_renders_of_the_real_set_are_byte_identical() -> None:
    report = ValidationService().build_report(CROSSWALK_PATH)
    assert render_markdown(report) == render_markdown(report)

    report_again = ValidationService().build_report(CROSSWALK_PATH)
    assert render_markdown(report) == render_markdown(report_again)


def test_at5_two_json_renders_of_the_real_set_are_byte_identical() -> None:
    report = ValidationService().build_report(CROSSWALK_PATH)
    report_again = ValidationService().build_report(CROSSWALK_PATH)
    assert render_json(report) == render_json(report_again)


def test_reliability_not_validity_header_present_on_the_real_report() -> None:
    report = ValidationService().build_report(CROSSWALK_PATH)
    assert report.measures_reliability_not_validity is True
    assert "RELIABILITY" in report.reliability_note
    assert "NOT VALIDITY" in report.reliability_note
    assert report.reference_rater == "pipeline"
