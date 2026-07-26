"""AT3 (task-packets/N2-T02a.yaml): the https + host allowlist guard in
``scripts/fetch_citation_resolutions.py`` refuses a non-allowlisted host, a
non-https scheme, and an unexpected (lookalike) URL, as a typed error
(:class:`EgressRefusedError`), with NO socket ever opened. Every such test
here monkeypatches ``urllib.request.urlopen`` with a function that fails the
test outright if it is ever called, then asserts the refusal happens before
that point — this is the module's own "no unrestricted network egress"
guarantee (AGENTS.md rule 11), proven, not merely asserted in prose.

task-packets/S1-T02.yaml extends this module's egress coverage with the
REDIRECT gap: the allowlist above gates only the REQUESTED url, and stdlib
``urllib`` follows HTTP redirects by default, so an allowlisted host could
silently hand a request to any other host — even a second allowlisted one.
The tests below (from ``TestNoRedirectHandler`` onward) prove that gap is
closed: :class:`RedirectRefusedError` fires for a redirect to ANOTHER
allowlisted host, not just to a foreign one (the sharp case named in
task-packets/S1-T02.yaml's own acceptance_criteria), the refused response's
body is never read, and the existing guarantees above (allowlist, timeout)
remain demonstrably intact. Unlike the allowlist tests above, which
monkeypatch ``urllib.request.urlopen`` itself, these redirect tests drive
the REAL, unpatched ``urlopen()`` and the REAL opener
``scripts.fetch_citation_resolutions`` installs at import time, with only
``urllib.request.AbstractHTTPHandler.do_open`` — the connection-layer seam —
monkeypatched (mirrors ``tests/unit/adapters/llm/test_transport.py``'s
identical technique for the E4-T08 precedent, ``_NoRedirectHandler`` in
``adapters/llm/mrr/adapters/llm/transport.py``) — never a mock of
``_open_url`` or ``_check_allowlisted`` themselves.

No test in this module makes a real network call.
"""

from __future__ import annotations

import email.message
import urllib.error
import urllib.request
from http.client import HTTPMessage

import pytest

from scripts.fetch_citation_resolutions import (
    ALLOWED_HOSTS,
    REQUEST_TIMEOUT_SECONDS,
    USER_AGENT,
    EgressRefusedError,
    FetchScriptError,
    RedirectRefusedError,
    ResponseTooLargeError,
    _check_allowlisted,
    _NoRedirectHandler,
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


# ---------------------------------------------------------------------------
# task-packets/S1-T02.yaml: redirects are refused, unconditionally — even to
# ANOTHER allowlisted host. Everything below drives the REAL urlopen() and
# the REAL process opener installed by scripts.fetch_citation_resolutions at
# import time, with only urllib.request.AbstractHTTPHandler.do_open
# monkeypatched (see the module docstring above for why this seam, not
# urlopen itself, is what proves the real wiring rather than a mock of it).
# ---------------------------------------------------------------------------


def _fake_headers(pairs: list[tuple[str, str]]) -> HTTPMessage:
    """Builds a real ``http.client.HTTPMessage`` — the exact type a genuine
    urllib response carries — rather than a bare ``email.message.Message``,
    mirroring ``tests/unit/adapters/llm/test_transport.py``'s identical
    helper for the E4-T08 precedent.
    """
    message = HTTPMessage()
    for name, value in pairs:
        message[name] = value
    return message


class _FakeRedirectConnection:
    """Duck-types the surface ``urllib.request.AbstractHTTPHandler.do_open``
    returns from a real ``http.client.HTTPResponse``: ``.status``, ``.code``,
    ``.reason``, ``.msg``, ``.headers``, ``.read()``, and the context-manager
    protocol. ``.read()`` RAISES — task-packets/S1-T02.yaml
    acceptance_criteria: "No body is read on a refused redirect - asserted
    by a connection double that fails the test if a body read is attempted
    after the 30x." Every test below that expects a refusal uses this fake,
    so any of them would fail loudly if ``_open_url`` ever read the body of
    a redirect it is supposed to refuse outright.
    """

    def __init__(self, status: int, headers: HTTPMessage) -> None:
        self.status = status
        self.code = status
        self.reason = "redirected"
        self.msg = self.reason
        self.headers = headers

    def read(self, amt: int | None = None) -> bytes:
        raise AssertionError(
            "response body was read on a refused redirect — a refusal must never "
            "read the body (task-packets/S1-T02.yaml acceptance_criteria)"
        )

    def info(self) -> HTTPMessage:
        return self.headers

    def close(self) -> None:
        return None

    def __enter__(self) -> _FakeRedirectConnection:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        return None


class _FakeOkConnection:
    """An ordinary 200 response — used both as the (never-reached) redirect
    target in the refusal tests, and standalone to prove a non-redirect
    response still round-trips normally after the redirect-refusing opener
    is installed.
    """

    def __init__(self, body: bytes) -> None:
        self.status = 200
        self.code = 200
        self.reason = "OK"
        self.msg = self.reason
        self.headers = _fake_headers([])
        self._body = body

    def read(self, amt: int | None = None) -> bytes:
        return self._body

    def info(self) -> HTTPMessage:
        return self.headers

    def close(self) -> None:
        return None

    def __enter__(self) -> _FakeOkConnection:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        return None


def _install_fake_do_open(
    monkeypatch: pytest.MonkeyPatch, responses_by_url: dict[str, object]
) -> list[str]:
    """Replaces ``AbstractHTTPHandler.do_open`` (used by both
    ``HTTPHandler.http_open`` and ``HTTPSHandler.https_open``) with a fake
    that looks up a canned response by the request's full URL, and returns
    the list every call's URL is appended to — so a test can assert exactly
    how many requests were made, and to which URLs. A request for a URL with
    no scripted response fails the test immediately: this is how the
    redirect-target-never-requested guarantee is proven below, exactly as in
    ``tests/unit/adapters/llm/test_transport.py`` for the E4-T08 precedent.
    """
    calls: list[str] = []

    def fake_do_open(
        self: urllib.request.AbstractHTTPHandler,
        http_class: object,
        req: urllib.request.Request,
        **_http_conn_args: object,
    ) -> object:
        url = req.get_full_url()
        calls.append(url)
        assert url in responses_by_url, (
            f"no fake response scripted for {url!r} — a refused redirect target "
            "must never actually be requested"
        )
        return responses_by_url[url]

    monkeypatch.setattr(urllib.request.AbstractHTTPHandler, "do_open", fake_do_open)
    return calls


class TestNoRedirectHandler:
    """Direct, pure test of the disabling mechanism itself — mirrors
    ``tests/unit/adapters/llm/test_transport.py``'s identical test for the
    E4-T08 precedent's own ``_NoRedirectHandler``.
    """

    @pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
    def test_returns_none_for_every_redirect_status(self, code: int) -> None:
        handler = _NoRedirectHandler()
        request = urllib.request.Request("https://export.arxiv.org/original")
        fp = _FakeRedirectConnection(code, _fake_headers([]))

        result = handler.redirect_request(
            request,
            fp,  # type: ignore[arg-type]
            code,
            "redirected",
            _fake_headers([("Location", "https://api.crossref.org/target")]),
            "https://api.crossref.org/target",
        )

        assert result is None


class TestRedirectIsRefusedEndToEnd:
    @pytest.fixture(autouse=True)
    def _poison_urlopen(self) -> None:
        """Overrides the module-level autouse fixture of the identical name
        for every test in this class (pytest fixture shadowing: a fixture
        defined closer to the test wins over one of the same name defined
        further out, autouse or not). Every test below drives the REAL
        ``urllib.request.urlopen()`` — routed through the real opener
        installed at import time — deliberately, per this module's own
        docstring; a poisoned stand-in would defeat the entire point of
        proving the redirect refusal end-to-end.
        """
        return None

    def test_redirect_between_two_allowlisted_hosts_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """THE SHARP CASE (task-packets/S1-T02.yaml acceptance_criteria): a
        302 from one allowlisted host to ANOTHER allowlisted host is refused
        all the same — the guarantee is "no redirect", not "no redirect off
        the allowlist" (see :class:`RedirectRefusedError`'s own docstring).
        The target host below (``api.crossref.org``) IS in
        :data:`ALLOWED_HOSTS`; a follow-and-recheck strategy would let this
        one through, which is exactly the strategy this packet rejects.
        """
        original_url = "https://export.arxiv.org/api/query?id_list=2511.02824"
        target_url = "https://api.crossref.org/works/10.1038/never-reached"
        redirect_response = _FakeRedirectConnection(302, _fake_headers([("Location", target_url)]))
        calls = _install_fake_do_open(monkeypatch, {original_url: redirect_response})

        with pytest.raises(RedirectRefusedError) as excinfo:
            _open_url(original_url)

        assert excinfo.value.status_code == 302
        assert excinfo.value.target_host == "api.crossref.org"
        assert calls == [original_url], f"the redirect target must never be requested, got {calls}"

    def test_redirect_to_a_non_allowlisted_host_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The obvious case: a redirect OFF the allowlist is refused too —
        by the identical mechanism as the sharp case above, since neither
        case ever consults :data:`ALLOWED_HOSTS` at all (no redirect is
        ever followed, allowlisted target or not).
        """
        original_url = "https://export.arxiv.org/api/query?id_list=2511.02824"
        target_url = "https://evil.example.com/steal-the-bytes"
        redirect_response = _FakeRedirectConnection(302, _fake_headers([("Location", target_url)]))
        calls = _install_fake_do_open(monkeypatch, {original_url: redirect_response})

        with pytest.raises(RedirectRefusedError) as excinfo:
            _open_url(original_url)

        assert excinfo.value.status_code == 302
        assert excinfo.value.target_host == "evil.example.com"
        assert calls == [original_url]

    @pytest.mark.parametrize("code", [301, 303, 307, 308])
    def test_every_redirect_status_is_refused_not_only_302(
        self, monkeypatch: pytest.MonkeyPatch, code: int
    ) -> None:
        original_url = "https://export.arxiv.org/api/query?id_list=2511.02824"
        redirect_response = _FakeRedirectConnection(
            code, _fake_headers([("Location", "https://api.crossref.org/target")])
        )
        calls = _install_fake_do_open(monkeypatch, {original_url: redirect_response})

        with pytest.raises(RedirectRefusedError) as excinfo:
            _open_url(original_url)

        assert excinfo.value.status_code == code
        assert len(calls) == 1

    def test_no_body_is_read_on_a_refused_redirect(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Names the guarantee explicitly (task-packets/S1-T02.yaml
        acceptance_criteria) even though every test above already exercises
        the same body-read-raises fake and would fail identically if
        ``_open_url`` ever read a refused redirect's body.
        """
        original_url = "https://export.arxiv.org/api/query?id_list=2511.02824"
        redirect_response = _FakeRedirectConnection(
            302, _fake_headers([("Location", "https://api.crossref.org/target")])
        )
        _install_fake_do_open(monkeypatch, {original_url: redirect_response})

        with pytest.raises(RedirectRefusedError):
            _open_url(original_url)

    def test_a_normal_200_response_still_round_trips_through_the_real_opener(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sanity: installing the redirect-refusing opener changes nothing
        about an ORDINARY (non-redirect) response.
        """
        url = "https://export.arxiv.org/api/query?id_list=2511.02824"
        body = b"<feed xmlns='http://www.w3.org/2005/Atom'></feed>"
        _install_fake_do_open(monkeypatch, {url: _FakeOkConnection(body)})

        assert _open_url(url) == body


class TestRedirectRefusedErrorIsDistinctFromEgressRefusedError:
    """task-packets/S1-T02.yaml acceptance_criteria: "distinct from
    EgressRefusedError (which means 'we never opened a socket') - the two
    failure modes stay separate, never collapsed."
    """

    def test_neither_is_a_subclass_of_the_other(self) -> None:
        assert not issubclass(RedirectRefusedError, EgressRefusedError)
        assert not issubclass(EgressRefusedError, RedirectRefusedError)

    def test_both_share_only_the_common_fetch_script_error_base(self) -> None:
        assert issubclass(RedirectRefusedError, FetchScriptError)
        assert issubclass(EgressRefusedError, FetchScriptError)

    def test_redirect_refused_error_names_status_code_and_target_host(self) -> None:
        error = RedirectRefusedError(
            "https://export.arxiv.org/api/query?id_list=1", 302, "evil.example.com"
        )
        assert error.status_code == 302
        assert error.target_host == "evil.example.com"
        assert "302" in str(error)
        assert "evil.example.com" in str(error)

    def test_target_host_is_none_not_a_fabricated_placeholder_when_location_is_absent(
        self,
    ) -> None:
        """A redirect response with no ``Location`` header at all is a real,
        if unusual, possibility (task-packets/S1-T02.yaml's own AGENTS.md
        discipline: no placeholders). ``None`` records that honestly rather
        than inventing a host name.
        """
        error = RedirectRefusedError("https://export.arxiv.org/x", 302, None)
        assert error.target_host is None


# ---------------------------------------------------------------------------
# Existing guarantees, demonstrably intact after the redirect-refusal change
# (task-packets/S1-T02.yaml acceptance_criteria) — proven here through the
# REAL opener/urlopen, complementing the untouched allowlist/size-limit
# coverage above and in tests/unit/scripts/test_script_edge_hardening.py.
# ---------------------------------------------------------------------------


class TestExistingGuaranteesStillHoldThroughTheRealOpener:
    @pytest.fixture(autouse=True)
    def _poison_urlopen(self) -> None:
        """Overrides the module-level autouse fixture of the identical name
        — see ``TestRedirectIsRefusedEndToEnd``'s identical override for
        why: both tests below drive the REAL ``urllib.request.urlopen()``
        deliberately.
        """
        return None

    def test_configured_timeout_is_still_forwarded_to_the_real_opener(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ "the timeout is still passed" (task-packets/S1-T02.yaml
        acceptance_criteria) — proven against the REAL
        ``urllib.request.urlopen()`` call in ``_open_url``, patching
        ``urllib.request.OpenerDirector.open`` (the exact seam
        ``tests/unit/adapters/llm/test_transport.py``'s own
        ``test_configured_timeout_is_forwarded_to_the_opener`` uses for the
        E4-T08 precedent), never a mock of ``_open_url`` itself.
        """
        captured: dict[str, object] = {}

        def fake_open(
            self: urllib.request.OpenerDirector,
            fullurl: object,
            data: object = None,
            timeout: object = None,
        ) -> object:
            captured["timeout"] = timeout
            raise urllib.error.HTTPError(
                "https://export.arxiv.org/x", 599, "stop here", email.message.Message(), None
            )

        monkeypatch.setattr(urllib.request.OpenerDirector, "open", fake_open)

        with pytest.raises(urllib.error.HTTPError):
            _open_url("https://export.arxiv.org/api/query?id_list=2511.02824")

        assert captured["timeout"] == REQUEST_TIMEOUT_SECONDS

    def test_oversized_response_is_still_refused_through_the_real_opener(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ "the MAX_RESPONSE_BYTES bound still caps a bounded read"
        (task-packets/S1-T02.yaml acceptance_criteria) — mirrors
        ``tests/unit/scripts/test_script_edge_hardening.py``'s
        ``_BoundedFakeResponse`` (untouched by this task, still passing
        independently), but driven here through the REAL opener/do_open
        seam rather than a monkeypatched ``urlopen``.
        """

        class _FakeOversizedConnection:
            def __init__(self) -> None:
                self.status = 200
                self.code = 200
                self.reason = "OK"
                self.msg = self.reason
                self.headers = _fake_headers([])

            def read(self, amt: int | None = None) -> bytes:
                if amt is None:
                    raise AssertionError("response.read() called with no size limit")
                return b"x" * amt

            def info(self) -> HTTPMessage:
                return self.headers

            def close(self) -> None:
                return None

            def __enter__(self) -> _FakeOversizedConnection:
                return self

            def __exit__(self, *_exc_info: object) -> None:
                return None

        url = "https://export.arxiv.org/api/query?id_list=2511.02824"
        _install_fake_do_open(monkeypatch, {url: _FakeOversizedConnection()})

        with pytest.raises(ResponseTooLargeError):
            _open_url(url)
