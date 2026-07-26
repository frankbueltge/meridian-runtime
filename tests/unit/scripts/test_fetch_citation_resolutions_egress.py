"""AT3 (task-packets/N2-T02a.yaml): the https + host allowlist guard in
``scripts/fetch_citation_resolutions.py`` refuses a non-allowlisted host, a
non-https scheme, and an unexpected (lookalike) URL, as a typed error
(:class:`EgressRefusedError`), with NO socket ever opened. Every test here
monkeypatches ``urllib.request.urlopen`` with a function that fails the test
outright if it is ever called, then asserts the refusal happens before that
point — this is the module's own "no unrestricted network egress" guarantee
(AGENTS.md rule 11), proven, not merely asserted in prose.

No test in this module makes a real network call.
"""

from __future__ import annotations

import urllib.request

import pytest

from scripts.fetch_citation_resolutions import (
    ALLOWED_HOSTS,
    USER_AGENT,
    EgressRefusedError,
    _check_allowlisted,
    _open_url,
)


def _fail_if_called(*_args: object, **_kwargs: object) -> object:
    raise AssertionError(
        "urllib.request.urlopen was called — the egress guard did not refuse first"
    )


@pytest.fixture(autouse=True)
def _poison_urlopen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Applied to EVERY test in this module: any call to
    ``urllib.request.urlopen`` fails the test immediately.
    """
    monkeypatch.setattr(urllib.request, "urlopen", _fail_if_called)


@pytest.mark.parametrize(
    "url",
    [
        "http://export.arxiv.org/api/query?id_list=2511.02824",
        "http://api.crossref.org/works/10.1038/s41586-026-10265-5",
    ],
)
def test_non_https_scheme_is_refused_before_any_socket_opens(url: str) -> None:
    with pytest.raises(EgressRefusedError, match="not https"):
        _open_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/api/query?id_list=2511.02824",
        "https://arxiv.org/api/query?id_list=2511.02824",  # real domain, wrong host
        "https://export.arxiv.org.attacker.example/api/query",  # lookalike suffix
    ],
)
def test_non_allowlisted_host_is_refused_before_any_socket_opens(url: str) -> None:
    with pytest.raises(EgressRefusedError, match="not in the allowlist"):
        _open_url(url)


def test_userinfo_trick_url_resolves_by_real_hostname_not_by_substring() -> None:
    """A URL that superficially CONTAINS an allowlisted host name in its
    userinfo component (``user@host``) must still be refused: the guard uses
    ``urlsplit(...).hostname`` (the REAL destination host), never a
    substring/prefix check on the raw string.
    """
    url = "https://export.arxiv.org@evil.example.com/api/query?id_list=2511.02824"
    with pytest.raises(EgressRefusedError, match="not in the allowlist"):
        _open_url(url)


def test_unexpected_scheme_relative_url_is_refused() -> None:
    with pytest.raises(EgressRefusedError):
        _open_url("export.arxiv.org/api/query?id_list=2511.02824")


@pytest.mark.parametrize("host", sorted(ALLOWED_HOSTS))
def test_check_allowlisted_accepts_https_and_the_two_allowlisted_hosts(host: str) -> None:
    """The positive case for :func:`_check_allowlisted` alone (not
    :func:`_open_url`, since this test intentionally does NOT want the
    poisoned ``urlopen`` to matter either way — it only checks the gate
    function raises nothing for a legitimate URL).
    """
    _check_allowlisted(f"https://{host}/some/path")


def test_allowed_hosts_is_exactly_the_two_documented_hosts() -> None:
    assert frozenset({"export.arxiv.org", "api.crossref.org"}) == ALLOWED_HOSTS


def test_user_agent_is_descriptive_and_carries_no_credential_marker() -> None:
    lowered = USER_AGENT.lower()
    assert "meridian" in lowered
    for forbidden_marker in ("token", "key=", "authorization", "bearer", "secret"):
        assert forbidden_marker not in lowered


def test_request_sends_no_authorization_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proves ``_open_url`` never attaches an ``Authorization`` header (or
    any credential) to the outgoing request — captured via a fake opener
    that records the ``Request`` object instead of ever touching the
    network.
    """
    captured: list[urllib.request.Request] = []

    class _FakeResponse:
        def __enter__(self) -> _FakeResponse:
            return self

        def __exit__(self, *_exc_info: object) -> None:
            return None

        def read(self, amt: int | None = None) -> bytes:
            return b"<feed xmlns='http://www.w3.org/2005/Atom'></feed>"

    def _fake_urlopen(request: urllib.request.Request, timeout: float) -> _FakeResponse:
        captured.append(request)
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    _open_url("https://export.arxiv.org/api/query?id_list=2511.02824")

    assert len(captured) == 1
    request = captured[0]
    header_names = {name.lower() for name in request.headers}
    assert "authorization" not in header_names
    assert request.get_header("User-agent") == USER_AGENT
