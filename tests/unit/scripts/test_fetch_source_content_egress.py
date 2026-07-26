"""task-packets/S1-T02.yaml: closes the redirect gap in
``scripts/fetch_source_content.py``. ``_check_allowlisted`` (exercised by
``TestEgressAllowlist`` in ``tests/unit/scripts/test_fetch_source_content.py``
— untouched by this task, still passing independently) gates only the
REQUESTED url; stdlib ``urllib`` follows HTTP redirects by default, so an
allowlisted host could silently hand a request to any other host — even a
SECOND allowlisted one. This module lives separately from that existing
file (task-packets/S1-T02.yaml ``allowed_paths`` names this file, not that
one) and covers exactly the redirect-refusal surface: :class:`RedirectRefusedError`
fires for a redirect between two allowlisted hosts (the sharp case in
task-packets/S1-T02.yaml's own acceptance_criteria, not merely a redirect
off the allowlist), the refused response's body is never read, the new
error stays distinct from :class:`EgressRefusedError`, and the pre-existing
timeout/size-limit guarantees remain demonstrably intact.

Declared independently from
``tests/unit/scripts/test_fetch_citation_resolutions_egress.py`` — no
import between the two test modules, exactly mirroring
``scripts/fetch_source_content.py`` and
``scripts/fetch_citation_resolutions.py``'s own "decoupled read, declared
independently" precedent (task-packets/S1-T02.yaml explicitly forbids a
shared module for ``scripts/`` and, by the same reasoning, for these tests).

Every test here drives the REAL, unpatched ``urlopen()`` and the REAL
opener ``scripts.fetch_source_content`` installs at import time
(:class:`_NoRedirectHandler`), with only
``urllib.request.AbstractHTTPHandler.do_open`` — the connection-layer seam
— monkeypatched (mirrors ``tests/unit/adapters/llm/test_transport.py``'s
identical technique for the E4-T08 precedent,
``adapters/llm/mrr/adapters/llm/transport.py::_NoRedirectHandler``) — never
a mock of ``_open_url`` or ``_check_allowlisted`` themselves.

No test in this module makes a real network call.
"""

from __future__ import annotations

import email.message
import urllib.error
import urllib.request
from http.client import HTTPMessage

import pytest

from scripts.fetch_source_content import (
    REQUEST_TIMEOUT_SECONDS,
    EgressRefusedError,
    FetchScriptError,
    RedirectRefusedError,
    ResponseTooLargeError,
    _NoRedirectHandler,
    _open_url,
)


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
    how many requests were made, and to which URLs. A request for a URL
    with no scripted response fails the test immediately: this is how the
    redirect-target-never-requested guarantee is proven below, exactly as
    in ``tests/unit/adapters/llm/test_transport.py`` for the E4-T08
    precedent.
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
    def test_redirect_between_two_allowlisted_hosts_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """THE SHARP CASE (task-packets/S1-T02.yaml acceptance_criteria): a
        302 from one allowlisted host to ANOTHER allowlisted host is
        refused all the same — the guarantee is "no redirect", not "no
        redirect off the allowlist" (see :class:`RedirectRefusedError`'s
        own docstring). The target host below (``api.crossref.org``) IS in
        :data:`scripts.fetch_source_content.ALLOWED_HOSTS`; a
        follow-and-recheck strategy would let this one through, which is
        exactly the strategy this packet rejects.
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
        case ever consults ``ALLOWED_HOSTS`` at all (no redirect is ever
        followed, allowlisted target or not).
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
        """A redirect response with no ``Location`` header at all is a
        real, if unusual, possibility (AGENTS.md discipline: no
        placeholders). ``None`` records that honestly rather than inventing
        a host name.
        """
        error = RedirectRefusedError("https://export.arxiv.org/x", 302, None)
        assert error.target_host is None


# ---------------------------------------------------------------------------
# Existing guarantees, demonstrably intact after the redirect-refusal change
# (task-packets/S1-T02.yaml acceptance_criteria) — proven here through the
# REAL opener/urlopen, complementing the untouched allowlist coverage in
# tests/unit/scripts/test_fetch_source_content.py and the untouched
# size-limit coverage in tests/unit/scripts/test_script_edge_hardening.py.
# ---------------------------------------------------------------------------


class TestExistingGuaranteesStillHoldThroughTheRealOpener:
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
