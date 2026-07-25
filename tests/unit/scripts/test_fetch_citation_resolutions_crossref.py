"""AT4 (task-packets/N2-T02a.yaml), Crossref half: ``parse_crossref_work``
maps a FIXTURE ``works`` JSON body to a resolution correctly, and
``fetch_crossref_work`` maps HTTP 404 -> ``resolved=False`` while any other
non-200 (e.g. 500) -> :class:`UpstreamRefusedError` naming the status —
never a silent ``resolved=False`` for a server error. All fixtures/statuses
are supplied via a monkeypatched ``urllib.request.urlopen``; no test here
makes a real network call.
"""

from __future__ import annotations

import email.message
import io
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from scripts.fetch_citation_resolutions import (
    UpstreamRefusedError,
    fetch_crossref_work,
    parse_crossref_work,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SAKANA_DOI = "10.1038/s41586-026-10265-5"


def _read_fixture(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


def test_parse_crossref_work_extracts_title_container_and_detail() -> None:
    raw = _read_fixture("crossref_work_sakana_nature.json")
    resolution = parse_crossref_work(raw, SAKANA_DOI)

    assert resolution.doi == SAKANA_DOI
    assert resolution.resolved is True
    assert resolution.resolved_title == "Towards end-to-end automation of AI research"
    assert resolution.resolved_container == "Nature"
    assert resolution.resolved_detail == (
        "vol 651 (8107), pp. 914-919, 2026; first author Chris Lu; published online 2026-03-25"
    )


def test_parse_crossref_work_degrades_gracefully_with_sparse_message() -> None:
    """No volume/issue/page/author/published-online at all — the detail
    builder must degrade to ``None`` rather than raise or fabricate a
    placeholder value.
    """
    raw = b'{"message": {"title": ["A Bare Work"]}}'
    resolution = parse_crossref_work(raw, "10.1234/bare")

    assert resolution.resolved is True
    assert resolution.resolved_title == "A Bare Work"
    assert resolution.resolved_container is None
    assert resolution.resolved_detail is None


def test_parse_crossref_work_refuses_non_json_body() -> None:
    with pytest.raises(UpstreamRefusedError):
        parse_crossref_work(b"not json at all", SAKANA_DOI)


def test_parse_crossref_work_refuses_a_body_with_no_message_object() -> None:
    with pytest.raises(UpstreamRefusedError):
        parse_crossref_work(b'{"status": "ok"}', SAKANA_DOI)


def _http_error(status: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://api.crossref.org/works/x",
        code=status,
        msg="test",
        hdrs=email.message.Message(),
        fp=io.BytesIO(b""),
    )


def test_fetch_crossref_work_maps_404_to_resolved_false(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_404(request: urllib.request.Request, timeout: float) -> None:
        raise _http_error(404)

    monkeypatch.setattr(urllib.request, "urlopen", _raise_404)

    resolution = fetch_crossref_work("10.9999/does-not-exist")

    assert resolution.resolved is False
    assert resolution.resolved_title is None
    assert resolution.resolved_container is None
    assert resolution.resolved_detail is None


def test_fetch_crossref_work_maps_500_to_a_typed_refusal_naming_the_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_500(request: urllib.request.Request, timeout: float) -> None:
        raise _http_error(500)

    monkeypatch.setattr(urllib.request, "urlopen", _raise_500)

    with pytest.raises(UpstreamRefusedError, match="500"):
        fetch_crossref_work(SAKANA_DOI)


def test_fetch_crossref_work_success_path_uses_the_response_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _read_fixture("crossref_work_sakana_nature.json")

    class _FakeResponse:
        def __enter__(self) -> _FakeResponse:
            return self

        def __exit__(self, *_exc_info: object) -> None:
            return None

        def read(self) -> bytes:
            return raw

    def _fake_urlopen(request: urllib.request.Request, timeout: float) -> _FakeResponse:
        assert urllib.parse.quote(SAKANA_DOI, safe="") in request.full_url
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    resolution = fetch_crossref_work(SAKANA_DOI)
    assert resolution.resolved is True
    assert resolution.resolved_title == "Towards end-to-end automation of AI research"


def test_fetch_crossref_work_url_quotes_the_doi(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[str] = []

    class _FakeResponse:
        def __enter__(self) -> _FakeResponse:
            return self

        def __exit__(self, *_exc_info: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"message": {"title": ["X"]}}'

    def _fake_urlopen(request: urllib.request.Request, timeout: float) -> _FakeResponse:
        captured.append(request.full_url)
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    fetch_crossref_work(SAKANA_DOI)
    (url,) = captured
    assert url == f"https://api.crossref.org/works/{urllib.parse.quote(SAKANA_DOI, safe='')}"
