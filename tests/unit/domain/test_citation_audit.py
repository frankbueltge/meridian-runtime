"""Unit tests for ``mrr.domain.citation_audit`` (task-packets/N2-T01.yaml
R1/R6, unit tier). DB-free, no-network — every input here is a hand-built
:class:`CitationEntry`/:class:`CitationResolution`, never a fixture read from
disk (the real committed fixture is exercised separately, at the contract
tier, in tests/contract/test_citation_audit_acceptance.py).

Acceptance-test mapping (AT4 — "synthetic unit oracles hit every status"):

- made-up arxiv id + resolved=false -> not_found:
  ``test_classify_citation_not_found_when_resolution_says_resolved_false``.
- real id + differing claimed_title -> title_mismatch:
  ``test_classify_citation_title_mismatch_when_claimed_title_differs``.
- a metadata-only / unverifiable flag -> unverifiable:
  ``test_classify_citation_unverifiable_when_resolution_flags_it``.
- an ill-formed identifier -> malformed, decided BEFORE the snapshot is
  consulted: ``test_classify_citation_malformed_before_snapshot_consulted``.
- a manifest entry with no resolution -> MissingResolutionError:
  ``test_classify_citation_raises_missing_resolution_error_naming_citation_id``.
"""

from __future__ import annotations

import pytest
from mrr.domain.citation_audit import (
    CITATION_STATUSES,
    CitationEntry,
    CitationResolution,
    MissingResolutionError,
    classify_citation,
    classify_citations,
    is_wellformed_arxiv,
    is_wellformed_doi,
    is_wellformed_url,
    normalise_title,
    title_matches,
)

# ---------------------------------------------------------------------------
# is_wellformed_arxiv
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "2511.02824",  # real example from the e2e-survey fixture (Kosmos)
        "2501.04227",  # Agent Laboratory
        "2501.04227v2",  # optional version suffix
        "0001.01234",  # month 01, the low edge
        "9912.12345",  # month 12, the high edge, 5-digit sequence
    ],
)
def test_is_wellformed_arxiv_accepts_valid_ids(value: str) -> None:
    assert is_wellformed_arxiv(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "2513.02824",  # month 13 does not exist
        "2500.02824",  # month 00 does not exist
        "abcd.1234",  # not digits at all
        "251.02824",  # only 3 digits before the dot
        "2511.023",  # only 3 sequence digits (needs 4 or 5)
        "2511.023456",  # 6 sequence digits (too many)
        "2511.02824v",  # 'v' with no digit following
        "2511-02824",  # wrong separator
        "",  # empty
    ],
)
def test_is_wellformed_arxiv_rejects_malformed_ids(value: str) -> None:
    assert is_wellformed_arxiv(value) is False


# ---------------------------------------------------------------------------
# is_wellformed_doi
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "10.1038/s41586-026-10265-5",  # real example (Nature DOI)
        "10.1234/abc",
        "10.123456789/x",  # 9-digit registrant, the high edge
        "10.1234/x-y_z.1",
    ],
)
def test_is_wellformed_doi_accepts_valid_dois(value: str) -> None:
    assert is_wellformed_doi(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "11.1038/s41586-026-10265-5",  # wrong prefix, not "10."
        "10.123/x",  # only 3 registrant digits (needs 4-9)
        "10.1234567890/x",  # 10 registrant digits (too many)
        "10.1038/",  # empty suffix
        "10.1038",  # no slash / suffix at all
        "10.1038/has space",  # embedded whitespace in the suffix
        "",
    ],
)
def test_is_wellformed_doi_rejects_malformed_dois(value: str) -> None:
    assert is_wellformed_doi(value) is False


# ---------------------------------------------------------------------------
# is_wellformed_url
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "https://arxiv.org/abs/2511.02824",
        "http://example.com",
        "https://www.nature.com/articles/s41586-026-10265-5",
        "https://example.com:8443/path?query=1",
    ],
)
def test_is_wellformed_url_accepts_valid_urls(value: str) -> None:
    assert is_wellformed_url(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "ftp://example.com",  # wrong scheme
        "not a url",
        "https://",  # no host
        "example.com",  # no scheme at all
        "https://exa mple.com",  # embedded whitespace
        "",
    ],
)
def test_is_wellformed_url_rejects_malformed_urls(value: str) -> None:
    assert is_wellformed_url(value) is False


# ---------------------------------------------------------------------------
# normalise_title
# ---------------------------------------------------------------------------


def test_normalise_title_casefolds() -> None:
    assert normalise_title("KOSMOS") == "kosmos"


def test_normalise_title_collapses_internal_whitespace() -> None:
    assert normalise_title("Kosmos:   An   AI  Scientist") == "kosmos: an ai scientist"


def test_normalise_title_strips_leading_and_trailing_whitespace() -> None:
    assert normalise_title("   Kosmos   ") == "kosmos"


def test_normalise_title_strips_surrounding_punctuation_but_not_internal() -> None:
    assert normalise_title("'Kosmos: An AI Scientist.'") == "kosmos: an ai scientist"


def test_normalise_title_strips_surrounding_unicode_punctuation() -> None:
    assert normalise_title("“Kosmos”") == "kosmos"


def test_normalise_title_of_empty_string_is_empty() -> None:
    assert normalise_title("   ") == ""


# ---------------------------------------------------------------------------
# title_matches
# ---------------------------------------------------------------------------


def test_title_matches_exact_after_normalisation() -> None:
    result = title_matches("Kosmos: An AI Scientist", "kosmos: an ai scientist  ")
    assert result.matches is True
    assert result.method == "exact"


def test_title_matches_prefix_when_claimed_is_a_prefix_of_resolved() -> None:
    result = title_matches("Kosmos", "Kosmos: An AI Scientist for Autonomous Discovery")
    assert result.matches is True
    assert result.method == "prefix"


def test_title_matches_prefix_when_resolved_is_a_prefix_of_claimed() -> None:
    result = title_matches("Kosmos: An AI Scientist for Autonomous Discovery", "Kosmos")
    assert result.matches is True
    assert result.method == "prefix"


def test_title_matches_no_match_for_unrelated_titles() -> None:
    result = title_matches("Kosmos", "Agent Laboratory")
    assert result.matches is False
    assert result.method == "no_match"


# ---------------------------------------------------------------------------
# classify_citation — every one of the five statuses (AT4), plus the raise.
# ---------------------------------------------------------------------------


def _entry(
    *,
    citation_id: str = "c1",
    cited_as: str = "Some Paper",
    cited_url: str = "https://arxiv.org/abs/2511.02824",
    arxiv_id: str | None = "2511.02824",
    doi: str | None = None,
    claimed_title: str | None = None,
) -> CitationEntry:
    return CitationEntry(
        citation_id=citation_id,
        cited_as=cited_as,
        cited_url=cited_url,
        arxiv_id=arxiv_id,
        doi=doi,
        claimed_title=claimed_title,
    )


def _resolution(
    *,
    citation_id: str = "c1",
    resolved: bool = True,
    resolved_title: str | None = "Kosmos: An AI Scientist for Autonomous Discovery",
    unverifiable: bool = False,
) -> CitationResolution:
    return CitationResolution(
        citation_id=citation_id,
        resolved=resolved,
        resolved_title=resolved_title,
        unverifiable=unverifiable,
    )


def test_classify_citation_resolved_when_no_claimed_title_declared() -> None:
    entry = _entry(claimed_title=None)
    verdict = classify_citation(entry, _resolution())
    assert verdict.status == "resolved"
    assert verdict.citation_id == "c1"
    assert verdict.identifier == "arxiv:2511.02824"
    assert verdict.resolved_title == "Kosmos: An AI Scientist for Autonomous Discovery"


def test_classify_citation_resolved_when_claimed_title_matches() -> None:
    entry = _entry(claimed_title="Kosmos")
    verdict = classify_citation(entry, _resolution())
    assert verdict.status == "resolved"


def test_classify_citation_not_found_when_resolution_says_resolved_false() -> None:
    """AT4: a made-up (but well-formed) arxiv id whose resolution reports
    ``resolved=False`` -> ``"not_found"`` (potential fabrication).
    """
    entry = _entry(
        citation_id="fictitious-paper",
        arxiv_id="2512.99999",  # well-formed, but resolves to nothing real
        cited_url="https://arxiv.org/abs/2512.99999",
    )
    resolution = _resolution(citation_id="fictitious-paper", resolved=False, resolved_title=None)
    verdict = classify_citation(entry, resolution)
    assert verdict.status == "not_found"
    assert "potential fabrication" in verdict.reason


def test_classify_citation_title_mismatch_when_claimed_title_differs() -> None:
    """AT4: a real id whose claimed title differs from the resolved title ->
    ``"title_mismatch"`` (potential misattribution).
    """
    entry = _entry(claimed_title="A Completely Different Paper About Something Else")
    verdict = classify_citation(entry, _resolution())
    assert verdict.status == "title_mismatch"
    assert "potential misattribution" in verdict.reason


def test_classify_citation_unverifiable_when_resolution_flags_it() -> None:
    """AT4: a resolution flagged ``unverifiable=True`` -> ``"unverifiable"``
    — even when ``resolved=True`` (unverifiable is checked BEFORE resolved,
    task-packets/N2-T01.yaml R1's own order).
    """
    entry = _entry()
    resolution = _resolution(resolved=True, unverifiable=True)
    verdict = classify_citation(entry, resolution)
    assert verdict.status == "unverifiable"


def test_classify_citation_unverifiable_overrides_not_resolved_too() -> None:
    entry = _entry()
    resolution = _resolution(resolved=False, unverifiable=True)
    verdict = classify_citation(entry, resolution)
    assert verdict.status == "unverifiable"


def test_classify_citation_malformed_before_snapshot_consulted() -> None:
    """AT4: an entry with no well-formed declared identifier at all ->
    ``"malformed"``, decided BEFORE the snapshot would even be consulted —
    passing ``resolution=None`` proves the snapshot was never needed.
    """
    entry = _entry(
        citation_id="bad-id",
        arxiv_id="abcd.1234",  # ill-formed
        doi=None,
        cited_url="not a url",  # ill-formed
    )
    verdict = classify_citation(entry, None)
    assert verdict.status == "malformed"
    assert "before consulting the resolution snapshot" in verdict.reason


def test_classify_citation_raises_missing_resolution_error_naming_citation_id() -> None:
    """AT4: a manifest entry with at least one well-formed identifier but no
    matching resolution -> a typed ``MissingResolutionError``, never a
    silent ``"not_found"``.
    """
    entry = _entry(citation_id="orphan-citation", doi="10.1038/s41586-026-10265-5")
    with pytest.raises(MissingResolutionError) as excinfo:
        classify_citation(entry, None)
    assert excinfo.value.citation_id == "orphan-citation"
    assert "orphan-citation" in str(excinfo.value)


def test_classify_citation_malformed_takes_all_declared_identifiers_into_account() -> None:
    """A citation with an ill-formed arxiv id but a WELL-FORMED cited_url is
    not malformed — only one declared identifier needs to be well-formed.
    """
    entry = _entry(arxiv_id="abcd.1234", cited_url="https://arxiv.org/abs/2511.02824")
    verdict = classify_citation(entry, _resolution())
    assert verdict.status != "malformed"


# ---------------------------------------------------------------------------
# classify_citations — ordering + aggregate raise behavior.
# ---------------------------------------------------------------------------


def test_classify_citations_orders_output_by_citation_id() -> None:
    entries = [
        _entry(citation_id="zeta", arxiv_id="2511.02824"),
        _entry(citation_id="alpha", arxiv_id="2511.02824"),
        _entry(citation_id="mid", arxiv_id="2511.02824"),
    ]
    resolutions = [
        _resolution(citation_id="zeta"),
        _resolution(citation_id="alpha"),
        _resolution(citation_id="mid"),
    ]
    verdicts = classify_citations(entries, resolutions)
    assert [v.citation_id for v in verdicts] == ["alpha", "mid", "zeta"]


def test_classify_citations_raises_missing_resolution_error_for_the_gap() -> None:
    entries = [_entry(citation_id="has-resolution"), _entry(citation_id="missing-resolution")]
    resolutions = [_resolution(citation_id="has-resolution")]
    with pytest.raises(MissingResolutionError) as excinfo:
        classify_citations(entries, resolutions)
    assert excinfo.value.citation_id == "missing-resolution"


def test_classify_citations_empty_input_is_empty_output() -> None:
    assert classify_citations([], []) == ()


# ---------------------------------------------------------------------------
# The closed set of five statuses (AGENTS.md prohibited shortcut).
# ---------------------------------------------------------------------------


def test_citation_statuses_is_the_closed_set_of_exactly_five() -> None:
    assert CITATION_STATUSES == (
        "resolved",
        "not_found",
        "title_mismatch",
        "unverifiable",
        "malformed",
    )
