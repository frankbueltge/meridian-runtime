"""Tests for ``scripts/fetch_source_content.py`` (task-packets/N2-T03a.yaml).
No test here makes a real network call — every ``urllib.request.urlopen``
touchpoint is either monkeypatched with a poison pill (proving the egress
guard fires first) or with canned bytes (proving the parsing/orchestration
logic without ever leaving the process). Fixture XML/JSON bodies are inline
string literals rather than files under tests/unit/scripts/fixtures/, since
this packet's own allowed_paths names exactly one test file and no fixtures
directory (task-packets/N2-T03a.yaml allowed_paths).

Covers task-packets/N2-T03a.yaml's acceptance_criteria in full:

- The allowlist gate is real (monkeypatched urlopen fails the test if ever
  called) -> ``TestEgressAllowlist``.
- JATS stripping is a pure, separately testable function ->
  ``TestJatsStripping``.
- The snapshot is byte-stable for equal input -> ``TestSnapshotDeterminism``.
- Every one of the 21 manifest citations appears in the snapshot, including
  any whose excerpt could not be obtained -> ``TestManifestCoverage``.
- resolution-snapshot.json is bit-identical before and after ->
  ``test_resolution_snapshot_json_is_untouched_by_this_module``.
- Exit semantics mirror N2-T01 (0/2/3) -> ``TestCliExitSemantics``.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from hashlib import sha256
from pathlib import Path

import pytest

from scripts.fetch_source_content import (
    ALLOWED_HOSTS,
    EXCERPT_KIND,
    USER_AGENT,
    ArxivExcerptResult,
    EgressRefusedError,
    ExcerptRecord,
    ManifestCitation,
    ManifestInputError,
    UnresolvableManifestEntryError,
    UpstreamRefusedError,
    _check_allowlisted,
    _normalize_excerpt_whitespace,
    _open_url,
    build_snapshot_document,
    fetch_all_excerpts,
    fetch_arxiv_batch,
    fetch_crossref_abstract,
    group_by_resolver,
    main,
    parse_arxiv_summaries,
    parse_crossref_abstract,
    read_manifest,
    render_snapshot_json,
    strip_jats_tags,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
REAL_MANIFEST_PATH = REPO_ROOT / "corpora" / "research-records" / "citations.manifest.json"
REAL_RESOLUTION_SNAPSHOT_PATH = (
    REPO_ROOT / "corpora" / "research-records" / "verification" / "resolution-snapshot.json"
)

# ---------------------------------------------------------------------------
# Inline fixture bodies (no fixtures/ directory — see module docstring).
# ---------------------------------------------------------------------------

_ARXIV_ATOM_TWO_ENTRIES = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2511.02824v2</id>
    <title>Kosmos: An AI Scientist for Autonomous Discovery</title>
    <summary>
      Kosmos runs for up to 12 hours performing cycles of data analysis.
      Independent scientists found 79.4% of statements in Kosmos reports
      to be accurate.
    </summary>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2502.14297v2</id>
    <title>Evaluating Sakana's AI Scientist</title>
    <summary>An independent evaluation of the AI Scientist.</summary>
  </entry>
</feed>
"""

_ARXIV_ATOM_EMPTY_SUMMARY_ENTRY = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2511.02824v1</id>
    <title>A Paper With No Summary Text</title>
    <summary>   </summary>
  </entry>
</feed>
"""

_ARXIV_ATOM_NO_SUMMARY_ELEMENT = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2511.02824v1</id>
    <title>A Paper With No Summary Element At All</title>
  </entry>
</feed>
"""

_CROSSREF_WORK_WITH_JATS_ABSTRACT = json.dumps(
    {
        "message": {
            "title": ["Towards end-to-end automation of AI research"],
            "abstract": (
                "<jats:title>Abstract</jats:title>\n"
                "<jats:p>\n"
                "  The automation of science is a long-standing ambition\n"
                "  <jats:sup>1,2</jats:sup>\n"
                "  . The workshop had an acceptance rate of 70%.\n"
                "</jats:p>"
            ),
        }
    }
).encode("utf-8")

_CROSSREF_WORK_NO_ABSTRACT_FIELD = json.dumps(
    {"message": {"title": ["A Work With No Abstract Field"]}}
).encode("utf-8")

_CROSSREF_WORK_BLANK_ABSTRACT_FIELD = json.dumps(
    {"message": {"title": ["A Work"], "abstract": "   \n  "}}
).encode("utf-8")


# ---------------------------------------------------------------------------
# The allowlist gate: real, not cosmetic.
# ---------------------------------------------------------------------------


class TestEgressAllowlist:
    """Mirrors tests/unit/scripts/test_fetch_citation_resolutions_egress.py's
    own poison-pill discipline: every test here proves the refusal happens
    BEFORE ``urllib.request.urlopen`` is ever called.
    """

    @pytest.fixture(autouse=True)
    def _poison_urlopen(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _fail_if_called(*_args: object, **_kwargs: object) -> object:
            raise AssertionError(
                "urllib.request.urlopen was called — the egress guard did not refuse first"
            )

        monkeypatch.setattr(urllib.request, "urlopen", _fail_if_called)

    @pytest.mark.parametrize(
        "url",
        [
            "http://export.arxiv.org/api/query?id_list=2511.02824",
            "http://api.crossref.org/works/10.1038/s41586-026-10265-5",
        ],
    )
    def test_non_https_scheme_is_refused_before_any_socket_opens(self, url: str) -> None:
        with pytest.raises(EgressRefusedError, match="not https"):
            _open_url(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.example.com/api/query?id_list=2511.02824",
            "https://arxiv.org/api/query?id_list=2511.02824",
            "https://export.arxiv.org.attacker.example/api/query",
            "https://export.arxiv.org@evil.example.com/api/query?id_list=2511.02824",
        ],
    )
    def test_non_allowlisted_host_is_refused_before_any_socket_opens(self, url: str) -> None:
        with pytest.raises(EgressRefusedError, match="not in the allowlist"):
            _open_url(url)

    @pytest.mark.parametrize("host", sorted(ALLOWED_HOSTS))
    def test_check_allowlisted_accepts_https_and_the_two_allowlisted_hosts(self, host: str) -> None:
        _check_allowlisted(f"https://{host}/some/path")

    def test_allowed_hosts_is_exactly_the_two_documented_hosts(self) -> None:
        assert frozenset({"export.arxiv.org", "api.crossref.org"}) == ALLOWED_HOSTS

    def test_user_agent_carries_no_credential_marker(self) -> None:
        lowered = USER_AGENT.lower()
        for forbidden_marker in ("token", "key=", "authorization", "bearer", "secret"):
            assert forbidden_marker not in lowered


def test_request_sends_no_authorization_header(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[urllib.request.Request] = []

    class _FakeResponse:
        def __enter__(self) -> _FakeResponse:
            return self

        def __exit__(self, *_exc_info: object) -> None:
            return None

        def read(self) -> bytes:
            return b"<feed xmlns='http://www.w3.org/2005/Atom'></feed>"

    def _fake_urlopen(request: urllib.request.Request, timeout: float) -> _FakeResponse:
        captured.append(request)
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    _open_url("https://export.arxiv.org/api/query?id_list=2511.02824")

    assert len(captured) == 1
    header_names = {name.lower() for name in captured[0].headers}
    assert "authorization" not in header_names
    assert captured[0].get_header("User-agent") == USER_AGENT


# ---------------------------------------------------------------------------
# JATS stripping — pure, separately testable.
# ---------------------------------------------------------------------------


class TestJatsStripping:
    def test_title_block_is_dropped_entirely(self) -> None:
        raw = "<jats:title>Abstract</jats:title><jats:p>Real content.</jats:p>"
        stripped = strip_jats_tags(raw)
        assert "Abstract" not in stripped
        assert "Real content." in stripped

    def test_other_tags_are_removed_but_inner_text_survives(self) -> None:
        raw = "research<jats:sup>1,2</jats:sup>. Although the community..."
        stripped = strip_jats_tags(raw)
        assert "<jats:sup>" not in stripped
        assert "</jats:sup>" not in stripped
        assert "1,2" in stripped

    def test_plain_text_with_no_jats_markup_passes_through_unchanged(self) -> None:
        raw = "A perfectly ordinary abstract with no markup at all."
        assert strip_jats_tags(raw) == raw

    def test_is_pure_and_deterministic(self) -> None:
        raw = "<jats:title>Abstract</jats:title><jats:p>X <jats:italic>Y</jats:italic> Z</jats:p>"
        assert strip_jats_tags(raw) == strip_jats_tags(raw)

    def test_real_sakana_abstract_reference_markers_survive_as_plain_digits(self) -> None:
        """The exact shape task-packets/N2-T03a.yaml's own reviewer_resolution
        and docs/design/2026-07-25-n2-t03-derivation.md describe: JATS
        superscript reference markers become plain-text digit sequences
        after stripping — this is what makes the anchor-term requirement in
        N2-T03b's claim manifest necessary in the first place.
        """
        raw = (
            "<jats:title>Abstract</jats:title>\n"
            "<jats:p>research\n<jats:sup>1,2</jats:sup>\n. More text\n"
            "<jats:sup>3–5</jats:sup>\nand more\n<jats:sup>6,7</jats:sup>\n.</jats:p>"
        )
        stripped = strip_jats_tags(raw)
        normalized = _normalize_excerpt_whitespace(stripped)
        assert "1,2" in normalized
        assert "6,7" in normalized
        assert "Abstract" not in normalized


class TestWhitespaceNormalization:
    def test_collapses_newlines_and_indentation_to_single_spaces(self) -> None:
        raw = "Line one.\n                    Line two.\n   Line three."
        assert _normalize_excerpt_whitespace(raw) == "Line one. Line two. Line three."

    def test_collapses_nbsp_like_python_split(self) -> None:
        raw = "a\xa0top-tier conference"
        assert _normalize_excerpt_whitespace(raw) == "a top-tier conference"

    def test_strips_leading_and_trailing_whitespace(self) -> None:
        assert _normalize_excerpt_whitespace("   padded text   ") == "padded text"

    def test_empty_or_whitespace_only_normalises_to_empty_string(self) -> None:
        assert _normalize_excerpt_whitespace("   \n\t  ") == ""


# ---------------------------------------------------------------------------
# arXiv summary extraction.
# ---------------------------------------------------------------------------


class TestArxivSummaryExtraction:
    def test_present_entry_yields_normalised_excerpt(self) -> None:
        results = parse_arxiv_summaries(_ARXIV_ATOM_TWO_ENTRIES.encode(), ["2511.02824"])
        assert len(results) == 1
        result = results[0]
        assert result.excerpt_available is True
        assert result.unavailable_reason is None
        assert result.excerpt_text is not None
        assert "12 hours" in result.excerpt_text
        assert "\n" not in result.excerpt_text

    def test_requested_id_absent_from_response_is_unavailable_with_typed_reason(self) -> None:
        results = parse_arxiv_summaries(_ARXIV_ATOM_TWO_ENTRIES.encode(), ["9999.00000"])
        assert results == (_arxiv_unavailable("9999.00000", "arxiv_entry_not_found"),)

    def test_entry_with_blank_summary_is_unavailable_with_typed_reason(self) -> None:
        results = parse_arxiv_summaries(_ARXIV_ATOM_EMPTY_SUMMARY_ENTRY.encode(), ["2511.02824"])
        assert results == (_arxiv_unavailable("2511.02824", "arxiv_summary_absent"),)

    def test_entry_with_no_summary_element_at_all_is_unavailable_with_typed_reason(self) -> None:
        results = parse_arxiv_summaries(_ARXIV_ATOM_NO_SUMMARY_ELEMENT.encode(), ["2511.02824"])
        assert results == (_arxiv_unavailable("2511.02824", "arxiv_summary_absent"),)

    def test_versioned_request_matches_only_its_exact_version(self) -> None:
        results = parse_arxiv_summaries(_ARXIV_ATOM_TWO_ENTRIES.encode(), ["2502.14297v3"])
        assert results[0].excerpt_available is False
        assert results[0].unavailable_reason == "arxiv_entry_not_found"

    def test_unversioned_request_matches_whatever_version_is_returned(self) -> None:
        results = parse_arxiv_summaries(_ARXIV_ATOM_TWO_ENTRIES.encode(), ["2502.14297"])
        assert results[0].excerpt_available is True

    def test_output_covers_every_requested_id_in_the_given_order(self) -> None:
        requested = ["9999.00000", "2511.02824"]
        results = parse_arxiv_summaries(_ARXIV_ATOM_TWO_ENTRIES.encode(), requested)
        assert [result.requested_id for result in results] == requested

    def test_fetch_arxiv_batch_sends_exactly_one_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        call_count = 0
        captured_urls: list[str] = []

        class _FakeResponse:
            def __enter__(self) -> _FakeResponse:
                return self

            def __exit__(self, *_exc_info: object) -> None:
                return None

            def read(self) -> bytes:
                return _ARXIV_ATOM_TWO_ENTRIES.encode()

        def _fake_urlopen(request: urllib.request.Request, timeout: float) -> _FakeResponse:
            nonlocal call_count
            call_count += 1
            captured_urls.append(request.full_url)
            return _FakeResponse()

        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

        results = fetch_arxiv_batch(["2511.02824", "2502.14297v2", "9999.00000"])
        assert call_count == 1
        assert len(results) == 3
        (url,) = captured_urls
        assert "id_list=2511.02824%2C2502.14297v2%2C9999.00000" in url

    def test_fetch_arxiv_batch_with_no_ids_opens_no_connection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _fail_if_called(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("urlopen must not be called for an empty id batch")

        monkeypatch.setattr(urllib.request, "urlopen", _fail_if_called)

        assert fetch_arxiv_batch([]) == ()


def _arxiv_unavailable(requested_id: str, reason: str) -> ArxivExcerptResult:
    return ArxivExcerptResult(
        requested_id=requested_id,
        excerpt_available=False,
        excerpt_text=None,
        unavailable_reason=reason,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Crossref abstract extraction.
# ---------------------------------------------------------------------------


class TestCrossrefAbstractExtraction:
    DOI = "10.1038/s41586-026-10265-5"

    def test_jats_abstract_is_stripped_and_normalised(self) -> None:
        result = parse_crossref_abstract(_CROSSREF_WORK_WITH_JATS_ABSTRACT, self.DOI)
        assert result.excerpt_available is True
        assert result.unavailable_reason is None
        assert result.excerpt_text is not None
        assert "Abstract" not in result.excerpt_text
        assert "1,2" in result.excerpt_text
        assert "70%" in result.excerpt_text
        assert "\n" not in result.excerpt_text

    def test_missing_abstract_field_is_unavailable_with_typed_reason(self) -> None:
        result = parse_crossref_abstract(_CROSSREF_WORK_NO_ABSTRACT_FIELD, self.DOI)
        assert result.excerpt_available is False
        assert result.unavailable_reason == "crossref_abstract_absent"
        assert result.excerpt_text is None

    def test_blank_abstract_field_is_unavailable_with_typed_reason(self) -> None:
        result = parse_crossref_abstract(_CROSSREF_WORK_BLANK_ABSTRACT_FIELD, self.DOI)
        assert result.excerpt_available is False
        assert result.unavailable_reason == "crossref_abstract_absent"

    def test_refuses_non_json_body(self) -> None:
        with pytest.raises(UpstreamRefusedError):
            parse_crossref_abstract(b"not json at all", self.DOI)

    def test_refuses_body_with_no_message_object(self) -> None:
        with pytest.raises(UpstreamRefusedError):
            parse_crossref_abstract(b'{"status": "ok"}', self.DOI)

    def test_fetch_crossref_abstract_maps_404_to_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import email.message
        import io

        def _raise_404(request: urllib.request.Request, timeout: float) -> None:
            raise urllib.error.HTTPError(
                url="https://api.crossref.org/works/x",
                code=404,
                msg="not found",
                hdrs=email.message.Message(),
                fp=io.BytesIO(b""),
            )

        monkeypatch.setattr(urllib.request, "urlopen", _raise_404)

        result = fetch_crossref_abstract("10.9999/does-not-exist")
        assert result.excerpt_available is False
        assert result.unavailable_reason == "crossref_work_not_found"

    def test_fetch_crossref_abstract_maps_500_to_a_typed_refusal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import email.message
        import io

        def _raise_500(request: urllib.request.Request, timeout: float) -> None:
            raise urllib.error.HTTPError(
                url="https://api.crossref.org/works/x",
                code=500,
                msg="server error",
                hdrs=email.message.Message(),
                fp=io.BytesIO(b""),
            )

        monkeypatch.setattr(urllib.request, "urlopen", _raise_500)

        with pytest.raises(UpstreamRefusedError, match="500"):
            fetch_crossref_abstract(self.DOI)


# ---------------------------------------------------------------------------
# Manifest reading + resolver grouping (own, decoupled read).
# ---------------------------------------------------------------------------


class TestManifestReading:
    def test_unresolvable_entry_raises_typed_error(self) -> None:
        with pytest.raises(UnresolvableManifestEntryError):
            group_by_resolver([ManifestCitation(citation_id="mystery", arxiv_id=None, doi=None)])

    def test_missing_manifest_file_raises_typed_input_error(self, tmp_path: Path) -> None:
        with pytest.raises(ManifestInputError):
            read_manifest(tmp_path / "does-not-exist.json")

    def test_not_json_raises_typed_input_error(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("not json", encoding="utf-8")
        with pytest.raises(ManifestInputError):
            read_manifest(path)

    def test_reads_the_real_committed_manifest_with_21_citations(self) -> None:
        citations = read_manifest(REAL_MANIFEST_PATH)
        assert len(citations) == 21
        arxiv_group, doi_group = group_by_resolver(citations)
        assert len(arxiv_group) == 20
        assert len(doi_group) == 1
        assert doi_group[0] == ("sakana-nature", "10.1038/s41586-026-10265-5")


# ---------------------------------------------------------------------------
# Snapshot determinism.
# ---------------------------------------------------------------------------

_SAMPLE_RECORDS = (
    ExcerptRecord(
        citation_id="kosmos",
        resolver="arxiv",
        excerpt_kind=EXCERPT_KIND,
        excerpt_available=True,
        excerpt_text="Kosmos runs for up to 12 hours.",
        excerpt_sha256="sha256:" + sha256(b"Kosmos runs for up to 12 hours.").hexdigest(),
        unavailable_reason=None,
    ),
    ExcerptRecord(
        citation_id="sakana-nature",
        resolver="crossref",
        excerpt_kind=EXCERPT_KIND,
        excerpt_available=True,
        excerpt_text="The automation of science is a long-standing ambition.",
        excerpt_sha256=(
            "sha256:"
            + sha256(b"The automation of science is a long-standing ambition.").hexdigest()
        ),
        unavailable_reason=None,
    ),
    ExcerptRecord(
        citation_id="a-not-found-one",
        resolver="arxiv",
        excerpt_kind=EXCERPT_KIND,
        excerpt_available=False,
        excerpt_text=None,
        excerpt_sha256=None,
        unavailable_reason="arxiv_entry_not_found",
    ),
)


class TestSnapshotDeterminism:
    def test_two_renders_of_the_same_input_are_byte_identical(self) -> None:
        doc_1 = build_snapshot_document(
            manifest_relative_path="../citations.manifest.json",
            fetched_on="2026-07-25",
            records=_SAMPLE_RECORDS,
        )
        doc_2 = build_snapshot_document(
            manifest_relative_path="../citations.manifest.json",
            fetched_on="2026-07-25",
            records=_SAMPLE_RECORDS,
        )
        assert render_snapshot_json(doc_1) == render_snapshot_json(doc_2)

    def test_rendering_is_insensitive_to_caller_supplied_record_order(self) -> None:
        forward = build_snapshot_document(
            manifest_relative_path="../citations.manifest.json",
            fetched_on="2026-07-25",
            records=_SAMPLE_RECORDS,
        )
        backward = build_snapshot_document(
            manifest_relative_path="../citations.manifest.json",
            fetched_on="2026-07-25",
            records=tuple(reversed(_SAMPLE_RECORDS)),
        )
        assert render_snapshot_json(forward) == render_snapshot_json(backward)

    def test_excerpts_are_sorted_by_citation_id(self) -> None:
        document = build_snapshot_document(
            manifest_relative_path="../citations.manifest.json",
            fetched_on="2026-07-25",
            records=_SAMPLE_RECORDS,
        )
        ids = [entry["citation_id"] for entry in document["excerpts"]]
        assert ids == sorted(ids)

    def test_rendered_json_has_sorted_keys_and_a_trailing_newline(self) -> None:
        document = build_snapshot_document(
            manifest_relative_path="../citations.manifest.json",
            fetched_on="2026-07-25",
            records=_SAMPLE_RECORDS,
        )
        rendered = render_snapshot_json(document)
        assert rendered.endswith("\n")
        assert not rendered.endswith("\n\n")
        reparsed = json.loads(rendered)
        assert json.dumps(reparsed, indent=2, sort_keys=True) + "\n" == rendered

    def test_unavailable_excerpt_carries_null_text_and_a_typed_reason_never_a_placeholder(
        self,
    ) -> None:
        document = build_snapshot_document(
            manifest_relative_path="../citations.manifest.json",
            fetched_on="2026-07-25",
            records=_SAMPLE_RECORDS,
        )
        row = next(
            entry for entry in document["excerpts"] if entry["citation_id"] == "a-not-found-one"
        )
        assert row["excerpt_available"] is False
        assert row["excerpt_text"] is None
        assert row["excerpt_sha256"] is None
        assert row["unavailable_reason"] == "arxiv_entry_not_found"

    def test_document_shape_matches_the_declared_v1_schema(self) -> None:
        document = build_snapshot_document(
            manifest_relative_path="../citations.manifest.json",
            fetched_on="2026-07-25",
            records=_SAMPLE_RECORDS,
        )
        assert document["schema_version"] == "source-content-snapshot.v1"
        assert set(document) == {
            "schema_version",
            "manifest",
            "fetched_on",
            "excerpt_kind",
            "resolvers",
            "note",
            "excerpts",
        }
        assert document["excerpt_kind"] == "abstract"
        assert set(document["resolvers"]) == {"arxiv", "crossref"}
        assert "N2-T03b" in document["note"] or "support" in document["note"].lower()


# ---------------------------------------------------------------------------
# Manifest coverage: every one of the 21 real citations appears.
# ---------------------------------------------------------------------------


class TestManifestCoverage:
    def test_fetch_all_excerpts_covers_every_real_manifest_citation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exercises the real 21-citation manifest end to end with a
        monkeypatched transport (never touching the network), proving every
        citation_id is covered — including a deliberately-absent id — with
        no citation ever dropped or back-filled.
        """
        citations = read_manifest(REAL_MANIFEST_PATH)

        class _FakeResponse:
            def __init__(self, body: bytes) -> None:
                self._body = body

            def __enter__(self) -> _FakeResponse:
                return self

            def __exit__(self, *_exc_info: object) -> None:
                return None

            def read(self) -> bytes:
                return self._body

        def _fake_urlopen(request: urllib.request.Request, timeout: float) -> _FakeResponse:
            if "export.arxiv.org" in request.full_url:
                return _FakeResponse(b"<feed xmlns='http://www.w3.org/2005/Atom'></feed>")
            return _FakeResponse(_CROSSREF_WORK_WITH_JATS_ABSTRACT)

        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

        records = fetch_all_excerpts(citations)
        assert len(records) == 21
        covered_ids = {record.citation_id for record in records}
        assert covered_ids == {citation.citation_id for citation in citations}
        # The fake arXiv feed above is deliberately empty -> every one of the
        # 20 arXiv-resolved citations is unavailable, with a typed reason,
        # never omitted.
        for record in records:
            if record.resolver == "arxiv":
                assert record.excerpt_available is False
                assert record.unavailable_reason == "arxiv_entry_not_found"
            else:
                assert record.excerpt_available is True


def test_resolution_snapshot_json_is_untouched_by_this_module() -> None:
    """task-packets/N2-T03a.yaml acceptance_criteria: "resolution-
    snapshot.json is bit-identical before and after, asserted by its
    sha256". This module never opens that path at all (it reads
    citations.manifest.json only) — this is the tripwire that would catch a
    future accidental write.
    """
    actual = sha256(REAL_RESOLUTION_SNAPSHOT_PATH.read_bytes()).hexdigest()
    assert actual == "c6048147a2bf5a0fd990c0cb9869b5a37040cfdc274989527a923198cf2bb6c3"


# ---------------------------------------------------------------------------
# CLI exit semantics: 0 success, 2 input error, 3 refusal (mirrors N2-T01).
# ---------------------------------------------------------------------------


class TestCliExitSemantics:
    def test_missing_manifest_exits_2(self, tmp_path: Path) -> None:
        exit_code = main(
            [
                "--manifest",
                str(tmp_path / "does-not-exist.json"),
                "--out",
                str(tmp_path / "out.json"),
            ]
        )
        assert exit_code == 2

    def test_existing_output_path_exits_3(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / "citations.manifest.json"
        manifest_path.write_text(json.dumps({"citations": []}), encoding="utf-8")
        out_path = tmp_path / "content-snapshot.json"
        out_path.write_text("{}", encoding="utf-8")

        exit_code = main(["--manifest", str(manifest_path), "--out", str(out_path)])
        assert exit_code == 3

    def test_unresolvable_manifest_entry_exits_3(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / "citations.manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "citations": [
                        {"citation_id": "mystery", "identifiers": {}},
                    ]
                }
            ),
            encoding="utf-8",
        )
        out_path = tmp_path / "content-snapshot.json"

        exit_code = main(["--manifest", str(manifest_path), "--out", str(out_path)])
        assert exit_code == 3
        assert not out_path.exists()

    def test_successful_run_exits_0_and_writes_a_deterministic_snapshot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manifest_path = tmp_path / "citations.manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "citations": [
                        {"citation_id": "kosmos", "identifiers": {"arxiv": "2511.02824"}},
                        {
                            "citation_id": "sakana-nature",
                            "identifiers": {"doi": "10.1038/s41586-026-10265-5"},
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        out_path = tmp_path / "verification" / "content-snapshot.json"

        class _FakeResponse:
            def __init__(self, body: bytes) -> None:
                self._body = body

            def __enter__(self) -> _FakeResponse:
                return self

            def __exit__(self, *_exc_info: object) -> None:
                return None

            def read(self) -> bytes:
                return self._body

        def _fake_urlopen(request: urllib.request.Request, timeout: float) -> _FakeResponse:
            if "export.arxiv.org" in request.full_url:
                return _FakeResponse(_ARXIV_ATOM_TWO_ENTRIES.encode())
            return _FakeResponse(_CROSSREF_WORK_WITH_JATS_ABSTRACT)

        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

        exit_code = main(["--manifest", str(manifest_path), "--out", str(out_path)])
        assert exit_code == 0
        assert out_path.exists()

        document = json.loads(out_path.read_text(encoding="utf-8"))
        assert {entry["citation_id"] for entry in document["excerpts"]} == {
            "kosmos",
            "sakana-nature",
        }
        for entry in document["excerpts"]:
            assert entry["excerpt_available"] is True
            assert entry["excerpt_sha256"] is not None
