"""Unit tests for ``mrr.domain.citation_audit_report`` (task-packets/
N2-T01.yaml R2/R6, unit tier): report shape, per-status summary counts, the
honesty header, and determinism (two renders byte-identical). Hand-built
:class:`~mrr.domain.citation_audit.CitationVerdict` inputs throughout — the
real committed e2e-survey fixture is exercised separately at the contract
tier (tests/contract/test_citation_audit_acceptance.py).
"""

from __future__ import annotations

from mrr.domain.citation_audit import CitationVerdict
from mrr.domain.citation_audit_report import (
    CitationAuditReport,
    build_citation_audit_report,
    render_json,
    render_markdown,
)


def _verdict(
    *,
    citation_id: str,
    status: str = "resolved",
    cited_as: str = "Some Paper",
    identifier: str = "arxiv:2511.02824",
    resolved_title: str | None = "Some Resolved Title",
    reason: str = "identifier resolves",
) -> CitationVerdict:
    return CitationVerdict(
        citation_id=citation_id,
        cited_as=cited_as,
        identifier=identifier,
        status=status,  # type: ignore[arg-type]
        resolved_title=resolved_title,
        reason=reason,
    )


def _report(verdicts: list[CitationVerdict]) -> CitationAuditReport:
    return build_citation_audit_report(
        audit_target="a test target",
        manifest_path="manifest.json",
        snapshot_path="snapshot.json",
        snapshot_sha256="sha256:" + "0" * 64,
        verdicts=verdicts,
    )


# ---------------------------------------------------------------------------
# build_citation_audit_report: ordering + summary counts.
# ---------------------------------------------------------------------------


def test_build_report_orders_citations_by_citation_id() -> None:
    report = _report(
        [_verdict(citation_id="zeta"), _verdict(citation_id="alpha"), _verdict(citation_id="mid")]
    )
    assert [row.citation_id for row in report.citations] == ["alpha", "mid", "zeta"]


def test_build_report_summary_counts_every_status_distinctly() -> None:
    report = _report(
        [
            _verdict(citation_id="a", status="resolved"),
            _verdict(citation_id="b", status="resolved"),
            _verdict(citation_id="c", status="not_found"),
            _verdict(citation_id="d", status="title_mismatch"),
            _verdict(citation_id="e", status="unverifiable"),
            _verdict(citation_id="f", status="malformed"),
        ]
    )
    assert report.summary.resolved == 2
    assert report.summary.not_found == 1
    assert report.summary.title_mismatch == 1
    assert report.summary.unverifiable == 1
    assert report.summary.malformed == 1
    assert report.summary.total == 6


def test_build_report_summary_counts_are_zero_for_absent_statuses() -> None:
    report = _report([_verdict(citation_id="only-one", status="resolved")])
    assert report.summary.resolved == 1
    assert report.summary.not_found == 0
    assert report.summary.title_mismatch == 0
    assert report.summary.unverifiable == 0
    assert report.summary.malformed == 0
    assert report.summary.total == 1


def test_build_report_empty_verdicts_gives_all_zero_counts() -> None:
    report = _report([])
    assert report.summary.total == 0
    assert report.citations == ()


# ---------------------------------------------------------------------------
# The honesty header is structural (task-packets/N2-T01.yaml R2/AT2).
# ---------------------------------------------------------------------------


def test_honesty_header_is_always_present_and_true() -> None:
    report = _report([_verdict(citation_id="a")])
    assert report.verifies_existence_not_support is True


def test_honesty_header_names_n2_t02_and_n2_t03_as_out_of_scope() -> None:
    report = _report([_verdict(citation_id="a")])
    assert "N2-T02" in report.existence_note
    assert "N2-T03" in report.existence_note
    assert "SUPPORT" in report.existence_note
    assert "consistent" in report.existence_note


def test_report_has_no_field_that_could_hold_a_pass_fail_collapse() -> None:
    """AGENTS.md's prohibited shortcut is "collapsing unknown/not_found/
    contradicted/failed into one generic error" — there is no field on this
    report that could hold such a collapsed verdict; the summary always
    reports all five statuses distinctly (tested above).
    """
    assert not any(name in ("pass", "fail", "ok") for name in CitationAuditReport.model_fields)


# ---------------------------------------------------------------------------
# render_markdown / render_json: determinism + content shape (R4/AT5).
# ---------------------------------------------------------------------------


def test_render_markdown_contains_the_per_citation_table() -> None:
    report = _report(
        [
            _verdict(
                citation_id="kosmos",
                cited_as="Kosmos",
                identifier="arxiv:2511.02824",
                status="resolved",
                resolved_title="Kosmos: An AI Scientist for Autonomous Discovery",
            )
        ]
    )
    rendered = render_markdown(report)
    assert "| citation_id | cited_as | identifier | status | resolved_title | reason |" in rendered
    assert "kosmos" in rendered
    assert "arxiv:2511.02824" in rendered
    assert "Kosmos: An AI Scientist for Autonomous Discovery" in rendered


def test_render_markdown_shows_none_resolved_title_as_em_dash() -> None:
    report = _report([_verdict(citation_id="a", status="malformed", resolved_title=None)])
    rendered = render_markdown(report)
    assert "| a |" in rendered
    assert "—" in rendered


def test_render_markdown_escapes_pipe_characters_in_cells() -> None:
    report = _report([_verdict(citation_id="a", cited_as="Title | With Pipe")])
    rendered = render_markdown(report)
    assert "Title \\| With Pipe" in rendered


def test_render_markdown_includes_honesty_header_and_summary() -> None:
    report = _report([_verdict(citation_id="a", status="resolved")])
    rendered = render_markdown(report)
    assert "verifies_existence_not_support" in rendered
    assert "**resolved:** 1" in rendered


def test_two_markdown_renders_of_equal_report_are_byte_identical() -> None:
    report = _report([_verdict(citation_id="a"), _verdict(citation_id="b", status="not_found")])
    assert render_markdown(report) == render_markdown(report)

    report_again = _report(
        [_verdict(citation_id="a"), _verdict(citation_id="b", status="not_found")]
    )
    assert render_markdown(report) == render_markdown(report_again)


def test_two_json_renders_of_equal_report_are_byte_identical() -> None:
    report = _report([_verdict(citation_id="a"), _verdict(citation_id="b", status="not_found")])
    report_again = _report(
        [_verdict(citation_id="a"), _verdict(citation_id="b", status="not_found")]
    )
    assert render_json(report) == render_json(report_again)


def test_render_json_is_sorted_and_round_trips() -> None:
    import json

    report = _report([_verdict(citation_id="a")])
    payload = json.loads(render_json(report))
    assert payload["verifies_existence_not_support"] is True
    assert payload["summary"]["resolved"] == 1
    assert payload["citations"][0]["citation_id"] == "a"
