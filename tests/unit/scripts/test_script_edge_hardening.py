"""S1-T01 (task-packets/S1-T01.yaml): the script edge under the security
gate. Covers three independent hardenings, each declared independently in
BOTH ``scripts/fetch_citation_resolutions.py`` and
``scripts/fetch_source_content.py`` (mirrors those two modules' existing
"decoupled read" precedent — neither imports from the other):

1. DTD/ENTITY refusal BEFORE any XML parser sees the bytes
   (``_refuse_if_dtd_declared`` / ``XmlDtdRefusedError``) — structural, not
   bomb-shaped: a bare ``<!DOCTYPE`` with no entity bomb at all is refused
   too, and the billion-laughs bomb named in the S1-T01 derivation is
   refused WITHOUT ``ET.fromstring`` ever being reached (proven here by
   poisoning it). This closes the entity-EXPANSION half of the XML-attack
   surface only — XXE is not reachable on this interpreter to begin with
   (``ET.fromstring`` already raises ``ParseError: undefined entity`` for an
   external-entity reference), and no test here claims otherwise.
2. A bounded response read (``_open_url`` / ``MAX_RESPONSE_BYTES`` /
   ``ResponseTooLargeError``) — an over-limit response body is refused, and
   the read itself is proven bounded (never "read everything, then
   measure").
3. Exactly four local bandit suppressions across scripts/*.py, each naming
   its test id (2x B310, 1x B404, 1x B603) with a written reason on the
   line, and ZERO for B314/B405 — a grep-based check so a fifth suppression,
   or a B314/B405 suppression, cannot appear unnoticed.

Also proves the real, already-committed arXiv Atom / Crossref JSON fixture
shapes (tests/unit/scripts/fixtures/) still parse under the changed
parsers — no false positive on real, DTD-free traffic — and that
corpora/research-records/verification/{content,resolution}-snapshot.json are
untouched (this packet re-runs neither fetch script and changes no
committed snapshot).

No test in this module makes a real network call.
"""

from __future__ import annotations

import re
import urllib.request
import xml.etree.ElementTree as ET
from hashlib import sha256
from pathlib import Path

import pytest

import scripts.fetch_citation_resolutions as fcr
import scripts.fetch_source_content as fsc

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "scripts"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

CONTENT_SNAPSHOT_PATH = (
    REPO_ROOT / "corpora" / "research-records" / "verification" / "content-snapshot.json"
)
RESOLUTION_SNAPSHOT_PATH = (
    REPO_ROOT / "corpora" / "research-records" / "verification" / "resolution-snapshot.json"
)

# A well-formed 4-level "billion laughs" document — exactly the shape the
# S1-T01 derivation measured expanding to 30,000 characters on this
# interpreter (docs/design/2026-07-26-s1-derivation-script-edge-security.md).
_BILLION_LAUGHS_XML = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
 <!ENTITY lol2 "&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;">
 <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
]>
<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>&lol3;</id></entry></feed>
"""

#: A DOCTYPE with NO entity bomb at all — the guard must be structural, not
#: bomb-shaped (task-packets/S1-T01.yaml acceptance_criteria).
_DOCTYPE_ONLY_XML = (
    b'<?xml version="1.0"?>\n'
    b'<!DOCTYPE feed SYSTEM "atom.dtd">\n'
    b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>\n'
)

#: Leading whitespace before the DOCTYPE must not hide it from a naive
#: "starts with" check.
_LEADING_WHITESPACE_DOCTYPE_XML = b"   \n\t  <!DOCTYPE feed>\n<feed></feed>"

#: A lower-case ``<!doctype`` must still be caught even though the XML spec
#: itself requires the upper-case form.
_LOWERCASE_DOCTYPE_XML = b"<?xml version='1.0'?><!doctype feed><feed></feed>"

_CLEAN_ATOM_XML = b"<feed xmlns='http://www.w3.org/2005/Atom'></feed>"


class _AssertNotCalled:
    """A stand-in for ``ET.fromstring`` that fails the test immediately if
    it is ever invoked — proves the DTD guard refuses BEFORE any parser is
    reached, not merely that the guard also happens to raise.
    """

    def __call__(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("ET.fromstring was called — the DTD guard did not refuse first")


# ---------------------------------------------------------------------------
# 1. DTD/ENTITY refusal — scripts/fetch_citation_resolutions.py.
# ---------------------------------------------------------------------------


class TestDtdRefusalInCitationResolutions:
    def test_billion_laughs_is_refused_without_reaching_the_parser(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ET, "fromstring", _AssertNotCalled())
        with pytest.raises(fcr.XmlDtdRefusedError):
            fcr.parse_arxiv_atom(_BILLION_LAUGHS_XML, ["2511.02824"])

    def test_bare_doctype_with_no_bomb_is_refused_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The guard is structural, not bomb-shaped."""
        monkeypatch.setattr(ET, "fromstring", _AssertNotCalled())
        with pytest.raises(fcr.XmlDtdRefusedError):
            fcr.parse_arxiv_atom(_DOCTYPE_ONLY_XML, ["2511.02824"])

    def test_leading_whitespace_before_doctype_does_not_hide_it(self) -> None:
        with pytest.raises(fcr.XmlDtdRefusedError):
            fcr._refuse_if_dtd_declared(_LEADING_WHITESPACE_DOCTYPE_XML)

    def test_lowercase_doctype_is_still_caught(self) -> None:
        with pytest.raises(fcr.XmlDtdRefusedError):
            fcr._refuse_if_dtd_declared(_LOWERCASE_DOCTYPE_XML)

    def test_document_with_no_dtd_at_all_passes_the_guard(self) -> None:
        fcr._refuse_if_dtd_declared(_CLEAN_ATOM_XML)  # must not raise

    def test_clean_document_still_parses_end_to_end(self) -> None:
        resolutions = fcr.parse_arxiv_atom(_CLEAN_ATOM_XML, ["2511.02824"])
        assert resolutions == (
            fcr.ArxivResolution(
                requested_id="2511.02824", resolved=False, resolved_title=None, resolved_detail=None
            ),
        )


# ---------------------------------------------------------------------------
# 1. DTD/ENTITY refusal — scripts/fetch_source_content.py (independently
#    declared guard, same scenarios).
# ---------------------------------------------------------------------------


class TestDtdRefusalInSourceContent:
    def test_billion_laughs_is_refused_without_reaching_the_parser(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ET, "fromstring", _AssertNotCalled())
        with pytest.raises(fsc.XmlDtdRefusedError):
            fsc.parse_arxiv_summaries(_BILLION_LAUGHS_XML, ["2511.02824"])

    def test_bare_doctype_with_no_bomb_is_refused_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ET, "fromstring", _AssertNotCalled())
        with pytest.raises(fsc.XmlDtdRefusedError):
            fsc.parse_arxiv_summaries(_DOCTYPE_ONLY_XML, ["2511.02824"])

    def test_leading_whitespace_before_doctype_does_not_hide_it(self) -> None:
        with pytest.raises(fsc.XmlDtdRefusedError):
            fsc._refuse_if_dtd_declared(_LEADING_WHITESPACE_DOCTYPE_XML)

    def test_lowercase_doctype_is_still_caught(self) -> None:
        with pytest.raises(fsc.XmlDtdRefusedError):
            fsc._refuse_if_dtd_declared(_LOWERCASE_DOCTYPE_XML)

    def test_document_with_no_dtd_at_all_passes_the_guard(self) -> None:
        fsc._refuse_if_dtd_declared(_CLEAN_ATOM_XML)  # must not raise

    def test_clean_document_still_parses_end_to_end(self) -> None:
        results = fsc.parse_arxiv_summaries(_CLEAN_ATOM_XML, ["2511.02824"])
        assert results == (
            fsc.ArxivExcerptResult(
                requested_id="2511.02824",
                excerpt_available=False,
                excerpt_text=None,
                unavailable_reason="arxiv_entry_not_found",
            ),
        )


# ---------------------------------------------------------------------------
# 2. Bounded response-size limit — both scripts' _open_url.
# ---------------------------------------------------------------------------


class _BoundedFakeResponse:
    """A fake response whose ``read`` raises if ever called WITHOUT an
    explicit size — i.e. it fails the test if ``_open_url`` ever tries to
    "read everything", proving the real implementation's read is bounded.
    Otherwise, it fills exactly the requested amount (as a real buffered
    socket read does while more data remains).
    """

    def __enter__(self) -> _BoundedFakeResponse:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        return None

    def read(self, amt: int | None = None) -> bytes:
        if amt is None:
            raise AssertionError(
                "response.read() was called with no size limit — an unbounded "
                "read makes the size limit meaningless"
            )
        return b"x" * amt


class _SmallFakeResponse:
    """A fake response that returns a short, fixed body regardless of how
    much was asked for — exactly like a real short HTTP response once EOF is
    reached before the requested amount is filled.
    """

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _SmallFakeResponse:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        return None

    def read(self, amt: int | None = None) -> bytes:
        return self._body


class TestResponseSizeLimitInCitationResolutions:
    def test_over_limit_response_is_refused_via_a_bounded_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _fake_urlopen(request: urllib.request.Request, timeout: float) -> _BoundedFakeResponse:
            return _BoundedFakeResponse()

        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
        with pytest.raises(fcr.ResponseTooLargeError):
            fcr._open_url("https://export.arxiv.org/api/query?id_list=2511.02824")

    def test_under_limit_response_is_returned_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _fake_urlopen(request: urllib.request.Request, timeout: float) -> _SmallFakeResponse:
            return _SmallFakeResponse(b"a small body, well under the limit")

        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
        body = fcr._open_url("https://export.arxiv.org/api/query?id_list=2511.02824")
        assert body == b"a small body, well under the limit"

    def test_max_response_bytes_is_a_named_module_constant_in_the_low_single_digit_mb_range(
        self,
    ) -> None:
        assert 1 * 1024 * 1024 <= fcr.MAX_RESPONSE_BYTES <= 9 * 1024 * 1024


class TestResponseSizeLimitInSourceContent:
    def test_over_limit_response_is_refused_via_a_bounded_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _fake_urlopen(request: urllib.request.Request, timeout: float) -> _BoundedFakeResponse:
            return _BoundedFakeResponse()

        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
        with pytest.raises(fsc.ResponseTooLargeError):
            fsc._open_url("https://export.arxiv.org/api/query?id_list=2511.02824")

    def test_under_limit_response_is_returned_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _fake_urlopen(request: urllib.request.Request, timeout: float) -> _SmallFakeResponse:
            return _SmallFakeResponse(b"a small body, well under the limit")

        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
        body = fsc._open_url("https://export.arxiv.org/api/query?id_list=2511.02824")
        assert body == b"a small body, well under the limit"

    def test_max_response_bytes_is_a_named_module_constant_in_the_low_single_digit_mb_range(
        self,
    ) -> None:
        assert 1 * 1024 * 1024 <= fsc.MAX_RESPONSE_BYTES <= 9 * 1024 * 1024


# ---------------------------------------------------------------------------
# Real committed response shapes still parse (no false positive from the
# new guards on real, DTD-free traffic).
# ---------------------------------------------------------------------------


def _read_fixture(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


class TestRealFixtureShapesStillParse:
    def test_real_arxiv_atom_fixture_parses_via_fetch_citation_resolutions(self) -> None:
        raw = _read_fixture("arxiv_atom_present_and_absent.xml")
        resolutions = fcr.parse_arxiv_atom(raw, ["2511.02824"])
        assert resolutions[0].resolved is True

    def test_real_arxiv_atom_fixture_parses_via_fetch_source_content(self) -> None:
        raw = _read_fixture("arxiv_atom_present_and_absent.xml")
        results = fsc.parse_arxiv_summaries(raw, ["2511.02824"])
        assert results[0].excerpt_available is True

    def test_real_crossref_json_fixture_parses_via_fetch_citation_resolutions(self) -> None:
        raw = _read_fixture("crossref_work_sakana_nature.json")
        resolution = fcr.parse_crossref_work(raw, "10.1038/s41586-026-10265-5")
        assert resolution.resolved is True

    def test_real_crossref_json_fixture_parses_via_fetch_source_content(self) -> None:
        """This fixture carries no ``abstract`` field — the parser must
        still run to completion (no exception), reporting a typed
        unavailable reason rather than failing.
        """
        raw = _read_fixture("crossref_work_sakana_nature.json")
        result = fsc.parse_crossref_abstract(raw, "10.1038/s41586-026-10265-5")
        assert result.doi == "10.1038/s41586-026-10265-5"
        assert result.excerpt_available is False
        assert result.unavailable_reason == "crossref_abstract_absent"


# ---------------------------------------------------------------------------
# 3. Exactly four local bandit suppressions; zero for B314/B405.
# ---------------------------------------------------------------------------

_NOSEC_RE = re.compile(r"#\s*nosec\s*(B\d+)?", re.IGNORECASE)


def _scan_nosec_occurrences() -> list[tuple[str, int, str | None, str]]:
    occurrences: list[tuple[str, int, str | None, str]] = []
    for path in sorted(SCRIPTS_DIR.glob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            match = _NOSEC_RE.search(line)
            if match:
                test_id = match.group(1).upper() if match.group(1) else None
                occurrences.append((path.name, lineno, test_id, line.strip()))
    return occurrences


class TestNosecSuppressionCount:
    """task-packets/S1-T01.yaml acceptance_criteria: exactly four local
    suppressions (2x B310, 1x B404, 1x B603), each with a named test id and
    a written reason, and zero for B314/B405.
    """

    def test_exactly_four_nosec_occurrences_in_scripts(self) -> None:
        occurrences = _scan_nosec_occurrences()
        assert len(occurrences) == 4, occurrences

    def test_every_occurrence_names_a_specific_test_id_never_a_blanket_nosec(self) -> None:
        occurrences = _scan_nosec_occurrences()
        for file_name, lineno, test_id, line in occurrences:
            assert test_id is not None, (
                f"{file_name}:{lineno} blanket nosec with no test id: {line!r}"
            )

    def test_suppressed_ids_are_exactly_2x_b310_1x_b404_1x_b603(self) -> None:
        occurrences = _scan_nosec_occurrences()
        ids = sorted(test_id for _f, _l, test_id, _line in occurrences if test_id)
        assert ids == ["B310", "B310", "B404", "B603"]

    def test_no_suppression_for_b314_or_b405_anywhere_in_scripts(self) -> None:
        occurrences = _scan_nosec_occurrences()
        ids = {test_id for _f, _l, test_id, _line in occurrences if test_id}
        assert "B314" not in ids
        assert "B405" not in ids

    def test_every_suppression_carries_a_written_reason_on_the_line(self) -> None:
        occurrences = _scan_nosec_occurrences()
        for file_name, lineno, test_id, line in occurrences:
            assert test_id is not None
            after_tag = line.split(test_id, 1)[1].lstrip(" -#")
            assert len(after_tag) >= 20, (
                f"{file_name}:{lineno} nosec has no real reason attached: {line!r}"
            )

    def test_no_blanket_scripts_exclusion_in_the_makefile(self) -> None:
        makefile_text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        security_check_lines = [
            line for line in makefile_text.splitlines() if "bandit" in line and "-r" in line
        ]
        assert security_check_lines, "no bandit invocation found in the Makefile"
        for line in security_check_lines:
            assert "scripts" in line, f"bandit invocation does not cover scripts: {line!r}"
            assert "exclude" not in line.lower(), f"a scripts exclusion appeared: {line!r}"


# ---------------------------------------------------------------------------
# Committed snapshots are untouched by this packet.
# ---------------------------------------------------------------------------


class TestCommittedSnapshotsAreUntouched:
    """task-packets/S1-T01.yaml: this packet re-runs neither fetch script
    and changes no committed snapshot — asserted here by sha256, computed
    before this test file was written (pre-change) and re-checked on every
    run.
    """

    def test_content_snapshot_json_is_bit_identical(self) -> None:
        actual = sha256(CONTENT_SNAPSHOT_PATH.read_bytes()).hexdigest()
        assert actual == "85e9173cc91f445891dcbebb569d793653213d47119d0f09ec7fc14b5a491a0e"

    def test_resolution_snapshot_json_is_bit_identical(self) -> None:
        actual = sha256(RESOLUTION_SNAPSHOT_PATH.read_bytes()).hexdigest()
        assert actual == "c6048147a2bf5a0fd990c0cb9869b5a37040cfdc274989527a923198cf2bb6c3"
