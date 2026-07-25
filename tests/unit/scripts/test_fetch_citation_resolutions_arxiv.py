"""AT4 (task-packets/N2-T02a.yaml), arXiv half: ``parse_arxiv_atom`` maps a
FIXTURE Atom response to resolutions correctly — (a) a requested id ABSENT
from the response becomes ``resolved=False``, and (b) a versioned id
matches ONLY its exact versioned response id (never a different version,
never normalised to the base id). ``fetch_arxiv_batch`` is exercised
separately, with ``urllib.request.urlopen`` monkeypatched to return fixture
bytes, to prove the batching itself (one request for every id) without ever
touching the network.

Every fixture referenced here lives under tests/unit/scripts/fixtures/ and
is a real (if trimmed) arXiv Atom response shape, not invented XML.
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

import pytest

from scripts.fetch_citation_resolutions import (
    ArxivResolution,
    fetch_arxiv_batch,
    parse_arxiv_atom,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _read_fixture(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


def test_present_ids_resolve_with_title_and_first_author() -> None:
    raw = _read_fixture("arxiv_atom_present_and_absent.xml")
    resolutions = parse_arxiv_atom(raw, ["2511.02824", "2502.14297v3"])

    assert resolutions == (
        ArxivResolution(
            requested_id="2511.02824",
            resolved=True,
            resolved_title="Kosmos: An AI Scientist for Autonomous Discovery",
            resolved_detail="first author Ludovico Mitchener",
        ),
        ArxivResolution(
            requested_id="2502.14297v3",
            resolved=True,
            resolved_title=(
                "Evaluating Sakana's AI Scientist: Bold Claims, Mixed Results, and a "
                "Promising Future?"
            ),
            resolved_detail="first author Joeran Beel",
        ),
    )


def test_requested_id_absent_from_response_resolves_false() -> None:
    """AT4(a): a requested id with no matching entry in the response is
    ``resolved=False`` — never dropped from the output, never back-filled.
    """
    raw = _read_fixture("arxiv_atom_present_and_absent.xml")
    resolutions = parse_arxiv_atom(raw, ["2511.02824", "9999.00000"])

    by_id = {resolution.requested_id: resolution for resolution in resolutions}
    assert by_id["2511.02824"].resolved is True
    assert by_id["9999.00000"] == ArxivResolution(
        requested_id="9999.00000", resolved=False, resolved_title=None, resolved_detail=None
    )


def test_output_covers_every_requested_id_in_the_given_order() -> None:
    raw = _read_fixture("arxiv_atom_present_and_absent.xml")
    requested = ["9999.00000", "2511.02824", "2502.14297v3"]
    resolutions = parse_arxiv_atom(raw, requested)
    assert [resolution.requested_id for resolution in resolutions] == requested


def test_versioned_id_matches_only_its_exact_versioned_response_id() -> None:
    """AT4(b): a versioned requested id (``2502.14297v3``) matches ONLY an
    entry carrying that exact version. The fixture here has ONLY
    ``2502.14297v2`` — a different version of the same paper — so the
    versioned request must resolve False, never match the wrong version and
    never be silently normalised to the base id.
    """
    raw = _read_fixture("arxiv_atom_version_mismatch.xml")
    resolutions = parse_arxiv_atom(raw, ["2502.14297v3"])

    assert resolutions == (
        ArxivResolution(
            requested_id="2502.14297v3", resolved=False, resolved_title=None, resolved_detail=None
        ),
    )


def test_versioned_id_present_in_exactly_that_version_resolves_true() -> None:
    raw = _read_fixture("arxiv_atom_present_and_absent.xml")
    resolutions = parse_arxiv_atom(raw, ["2502.14297v3"])
    assert resolutions[0].resolved is True
    assert resolutions[0].requested_id == "2502.14297v3"


def test_unversioned_request_matches_whatever_version_the_response_carries() -> None:
    """An UNVERSIONED request (``2511.02824``) matches the entry by its base
    id regardless of which concrete version arXiv's API returns (it always
    returns one) — this is base-id matching, not "normalising a version
    away", since the REQUEST never carried a version to begin with.
    """
    raw = _read_fixture("arxiv_atom_present_and_absent.xml")
    resolutions = parse_arxiv_atom(raw, ["2511.02824"])
    assert resolutions[0].resolved is True
    assert resolutions[0].resolved_title == "Kosmos: An AI Scientist for Autonomous Discovery"


def test_an_arxiv_error_entry_never_matches_any_requested_id() -> None:
    """A response whose only entry is arXiv's own ``errors#...`` id (its
    documented behavior for a malformed ``id_list`` element) matches no
    requested id at all — it degrades to the same ``resolved=False`` as a
    genuinely absent id, never raises and never gets treated as a match.
    """
    raw = _read_fixture("arxiv_atom_error_entry.xml")
    resolutions = parse_arxiv_atom(raw, ["2511.02824"])
    assert resolutions == (
        ArxivResolution(
            requested_id="2511.02824", resolved=False, resolved_title=None, resolved_detail=None
        ),
    )


def test_empty_requested_ids_yields_empty_tuple() -> None:
    raw = _read_fixture("arxiv_atom_present_and_absent.xml")
    assert parse_arxiv_atom(raw, []) == ()


def test_fetch_arxiv_batch_sends_exactly_one_request_for_all_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """task-packets/N2-T02a.yaml R2: "ONE batched request ... one round trip
    for all of them, not one per id" — proven here by counting calls to the
    monkeypatched opener while resolving three ids at once.
    """
    raw = _read_fixture("arxiv_atom_present_and_absent.xml")
    call_count = 0
    captured_urls: list[str] = []

    class _FakeResponse:
        def __enter__(self) -> _FakeResponse:
            return self

        def __exit__(self, *_exc_info: object) -> None:
            return None

        def read(self) -> bytes:
            return raw

    def _fake_urlopen(request: urllib.request.Request, timeout: float) -> _FakeResponse:
        nonlocal call_count
        call_count += 1
        captured_urls.append(request.full_url)
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    resolutions = fetch_arxiv_batch(["2511.02824", "2502.14297v3", "9999.00000"])

    assert call_count == 1
    assert len(resolutions) == 3
    (url,) = captured_urls
    assert "id_list=2511.02824%2C2502.14297v3%2C9999.00000" in url
    assert "max_results=3" in url


def test_fetch_arxiv_batch_with_no_ids_opens_no_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail_if_called(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("urlopen must not be called for an empty id batch")

    monkeypatch.setattr(urllib.request, "urlopen", _fail_if_called)
    assert fetch_arxiv_batch([]) == ()
