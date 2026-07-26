"""Unit tests for ``mrr.adapters.llm.transport`` (task-packets/E4-T08.yaml).

No test in this module makes a real network call, not even to loopback: the
seam every test drives is either :meth:`_NoRedirectHandler.redirect_request`
directly (a pure method call), or
``urllib.request.AbstractHTTPHandler.do_open`` -- the exact point where
``urllib.request``'s own request-composition/redirect-handling/error-
processing machinery hands off to actually opening a socket -- monkeypatched
per test with a canned fake response. Patching THIS seam (rather than
``urllib.request.urlopen`` itself, the seam
tests/unit/scripts/test_fetch_citation_resolutions_egress.py uses for a
DIFFERENT purpose -- proving a socket is never opened at all) is what lets
these tests prove the REAL opener built by :class:`UrllibHTTPTransport`,
with its REAL ``_NoRedirectHandler`` installed, refuses a redirect --
``urlopen`` IS the function that would already have followed the redirect by
the time it returned, so replacing it would replace the very mechanism under
test.
"""

from __future__ import annotations

import email.message
import urllib.error
import urllib.request
from collections.abc import Mapping
from http.client import HTTPMessage
from typing import IO

import pytest
from mrr.adapters.llm.transport import (
    DEFAULT_TIMEOUT_SECONDS,
    HTTPRequest,
    HTTPResponse,
    HTTPTransport,
    TransportError,
    TransportTimeoutError,
    UrllibHTTPTransport,
    _NoRedirectHandler,
)

# ---------------------------------------------------------------------------
# Shared fakes: a minimal stand-in for http.client.HTTPResponse, and a
# do_open replacement built from a fixed script of them.
# ---------------------------------------------------------------------------


def _fake_headers(pairs: list[tuple[str, str]]) -> HTTPMessage:
    """Builds a real ``http.client.HTTPMessage`` (the exact type
    ``_NoRedirectHandler.redirect_request`` and every other urllib.request
    handler declares for a response's headers) rather than a bare
    ``email.message.Message`` -- ``HTTPMessage`` adds nothing behavioural
    for this module's purposes, but matching the declared type keeps every
    fake response here a genuine, type-checked stand-in instead of a
    same-shape-but-different-class approximation.
    """
    message = HTTPMessage()
    for name, value in pairs:
        message[name] = value
    return message


class _FakeConnectionResponse:
    """Duck-types exactly the surface
    ``urllib.request.AbstractHTTPHandler.do_open`` returns from a real
    ``http.client.HTTPResponse`` after ``getresponse()``: ``.status``,
    ``.code``, ``.reason``, ``.msg``, ``.headers``, ``.read()``, and the
    context-manager protocol (do_open's caller wraps the result in ``with
    ... as response:``).
    """

    def __init__(self, status: int, reason: str, headers: HTTPMessage, body: bytes) -> None:
        self.status = status
        self.code = status
        self.reason = reason
        self.msg = reason
        self.headers = headers
        self._body = body

    def read(self, amt: int | None = None) -> bytes:
        return self._body

    def info(self) -> HTTPMessage:
        return self.headers

    def close(self) -> None:
        return None

    def __enter__(self) -> _FakeConnectionResponse:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


def _install_fake_do_open(
    monkeypatch: pytest.MonkeyPatch, responses_by_url: Mapping[str, _FakeConnectionResponse]
) -> list[str]:
    """Replace ``AbstractHTTPHandler.do_open`` (used by both
    ``HTTPHandler.http_open`` and ``HTTPSHandler.https_open``, so this
    covers both http/https requests transparently) with a fake that looks up
    a canned response by the request's full URL, and returns the list every
    call's URL is appended to -- so a test can assert exactly how many
    requests were actually made, and to which URLs.
    """
    calls: list[str] = []

    def fake_do_open(
        self: urllib.request.AbstractHTTPHandler,
        http_class: object,
        req: urllib.request.Request,
        **_http_conn_args: object,
    ) -> _FakeConnectionResponse:
        url = req.get_full_url()
        calls.append(url)
        assert url in responses_by_url, f"no fake response scripted for {url!r}"
        return responses_by_url[url]

    monkeypatch.setattr(urllib.request.AbstractHTTPHandler, "do_open", fake_do_open)
    return calls


# ---------------------------------------------------------------------------
# HTTPTransport Protocol conformance.
# ---------------------------------------------------------------------------


def test_urllib_transport_conforms_to_the_http_transport_protocol() -> None:
    assert isinstance(UrllibHTTPTransport(), HTTPTransport)


def test_a_minimal_fake_conforms_to_the_http_transport_protocol() -> None:
    class _Fake:
        def send(self, request: HTTPRequest) -> HTTPResponse:
            return HTTPResponse(status_code=200, body=b"", headers={})

    assert isinstance(_Fake(), HTTPTransport)


# ---------------------------------------------------------------------------
# Constructor validation.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("timeout_seconds", [0, -1, -0.5])
def test_non_positive_timeout_is_rejected(timeout_seconds: float) -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        UrllibHTTPTransport(timeout_seconds=timeout_seconds)


def test_default_timeout_is_positive() -> None:
    assert DEFAULT_TIMEOUT_SECONDS > 0


# ---------------------------------------------------------------------------
# _NoRedirectHandler.redirect_request, tested directly -- the pure method
# call that IS the disabling mechanism (see this module's docstring).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_no_redirect_handler_returns_none_for_every_redirect_status(code: int) -> None:
    handler = _NoRedirectHandler()
    request = urllib.request.Request("https://example.invalid/original")
    fp: IO[bytes] = _FakeConnectionResponse(  # type: ignore[assignment]
        code, "redirected", _fake_headers([]), b""
    )

    result = handler.redirect_request(
        request,
        fp,
        code,
        "redirected",
        _fake_headers([("Location", "https://example.invalid/target")]),
        "https://example.invalid/target",
    )

    assert result is None


# ---------------------------------------------------------------------------
# End-to-end: the built opener actually refuses to follow a redirect.
# ---------------------------------------------------------------------------


def test_redirect_is_not_followed_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hands the REAL opener (built by UrllibHTTPTransport, with its REAL
    _NoRedirectHandler installed) a 302 pointing at a second URL that would
    return 200 if fetched, through the monkeypatched do_open seam. Asserts
    the redirect is not followed on TWO independent grounds: the returned
    HTTPResponse still carries the ORIGINAL 302 status/body, and do_open was
    called exactly ONCE -- the target URL was never requested at all.
    """
    redirect_response = _FakeConnectionResponse(
        302,
        "Found",
        _fake_headers([("Location", "https://example.invalid/target")]),
        b"this is the redirect response body, never the target's",
    )
    target_response = _FakeConnectionResponse(
        200, "OK", _fake_headers([]), b"the target response body -- must never be reached"
    )
    calls = _install_fake_do_open(
        monkeypatch,
        {
            "https://example.invalid/original": redirect_response,
            "https://example.invalid/target": target_response,
        },
    )

    transport = UrllibHTTPTransport()
    response = transport.send(
        HTTPRequest(method="GET", url="https://example.invalid/original", headers={})
    )

    assert response.status_code == 302
    assert response.body == b"this is the redirect response body, never the target's"
    assert calls == ["https://example.invalid/original"], (
        f"expected exactly one request (the target must never be fetched), got {calls}"
    )


@pytest.mark.parametrize("code", [301, 303, 307, 308])
def test_every_redirect_status_is_refused_not_only_302(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    redirect_response = _FakeConnectionResponse(
        code,
        "Redirected",
        _fake_headers([("Location", "https://example.invalid/target")]),
        b"redirect body",
    )
    calls = _install_fake_do_open(
        monkeypatch, {"https://example.invalid/original": redirect_response}
    )

    transport = UrllibHTTPTransport()
    response = transport.send(
        HTTPRequest(method="GET", url="https://example.invalid/original", headers={})
    )

    assert response.status_code == code
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Ordinary responses: 2xx returned normally, non-2xx returned as data too.
# ---------------------------------------------------------------------------


def test_a_200_response_is_returned_with_status_body_and_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_do_open(
        monkeypatch,
        {
            "https://example.invalid/ok": _FakeConnectionResponse(
                200, "OK", _fake_headers([("Content-Type", "application/json")]), b'{"ok": true}'
            )
        },
    )

    transport = UrllibHTTPTransport()
    response = transport.send(
        HTTPRequest(
            method="POST",
            url="https://example.invalid/ok",
            headers={"Content-Type": "application/json"},
            body=b"{}",
        )
    )

    assert response.status_code == 200
    assert response.body == b'{"ok": true}'
    assert response.headers.get("Content-Type") == "application/json"


def test_a_non_2xx_response_is_returned_as_data_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 404/429/500 is exactly as much "a response" as a 200 -- interpreting
    it is the caller's job (mrr.adapters.llm.gemini), never this transport's
    (see this module's own docstring, "What is, and is not, a transport
    failure").
    """
    _install_fake_do_open(
        monkeypatch,
        {
            "https://example.invalid/broken": _FakeConnectionResponse(
                429, "Too Many Requests", _fake_headers([]), b'{"error": {"code": 429}}'
            )
        },
    )

    transport = UrllibHTTPTransport()
    response = transport.send(
        HTTPRequest(method="POST", url="https://example.invalid/broken", headers={})
    )

    assert response.status_code == 429
    assert response.body == b'{"error": {"code": 429}}'


# ---------------------------------------------------------------------------
# Timeout wiring: the configured timeout reaches OpenerDirector.open.
# ---------------------------------------------------------------------------


def test_configured_timeout_is_forwarded_to_the_opener(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_open(
        self: urllib.request.OpenerDirector,
        fullurl: object,
        data: object = None,
        timeout: object = None,
    ) -> object:
        captured["timeout"] = timeout
        raise urllib.error.HTTPError(
            "https://example.invalid/x", 200, "OK", email.message.Message(), None
        )

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", fake_open)

    transport = UrllibHTTPTransport(timeout_seconds=12.5)
    transport.send(HTTPRequest(method="GET", url="https://example.invalid/x", headers={}))

    assert captured["timeout"] == 12.5


# ---------------------------------------------------------------------------
# Transport-level failures: timeout (bare and URLError-wrapped) and a plain
# connection failure -- distinct exception types, never collapsed.
# ---------------------------------------------------------------------------


def test_bare_timeout_error_raises_transport_timeout_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_do_open(
        self: urllib.request.AbstractHTTPHandler,
        http_class: object,
        req: urllib.request.Request,
        **_: object,
    ) -> _FakeConnectionResponse:
        raise TimeoutError("the read timed out")

    monkeypatch.setattr(urllib.request.AbstractHTTPHandler, "do_open", fake_do_open)

    transport = UrllibHTTPTransport()
    with pytest.raises(TransportTimeoutError):
        transport.send(HTTPRequest(method="GET", url="https://example.invalid/y", headers={}))


def test_urlerror_wrapped_timeout_raises_transport_timeout_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_do_open(
        self: urllib.request.AbstractHTTPHandler,
        http_class: object,
        req: urllib.request.Request,
        **_: object,
    ) -> _FakeConnectionResponse:
        raise urllib.error.URLError(TimeoutError("connect timed out"))

    monkeypatch.setattr(urllib.request.AbstractHTTPHandler, "do_open", fake_do_open)

    transport = UrllibHTTPTransport()
    with pytest.raises(TransportTimeoutError):
        transport.send(HTTPRequest(method="GET", url="https://example.invalid/y", headers={}))


def test_connection_failure_raises_transport_error_not_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_do_open(
        self: urllib.request.AbstractHTTPHandler,
        http_class: object,
        req: urllib.request.Request,
        **_: object,
    ) -> _FakeConnectionResponse:
        raise urllib.error.URLError(ConnectionRefusedError("connection refused"))

    monkeypatch.setattr(urllib.request.AbstractHTTPHandler, "do_open", fake_do_open)

    transport = UrllibHTTPTransport()
    with pytest.raises(TransportError) as excinfo:
        transport.send(HTTPRequest(method="GET", url="https://example.invalid/y", headers={}))

    assert not isinstance(excinfo.value, TransportTimeoutError)


# ---------------------------------------------------------------------------
# A raised TransportError/TransportTimeoutError message never carries a
# secret, even when the failed request's own headers did (mrr.adapters.llm.
# gemini puts the Gemini API key in exactly such a header) -- this module's
# own exception messages interpolate only ``request.url``, never
# ``request.headers``, so this holds structurally; proven here by actually
# raising with a secret-bearing header present and inspecting the message.
# ---------------------------------------------------------------------------


def test_a_transport_error_message_never_carries_a_header_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "SECRET-VALUE-THAT-MUST-NEVER-APPEAR-IN-AN-EXCEPTION-MESSAGE"

    def fake_do_open(
        self: urllib.request.AbstractHTTPHandler,
        http_class: object,
        req: urllib.request.Request,
        **_: object,
    ) -> _FakeConnectionResponse:
        raise urllib.error.URLError(ConnectionRefusedError("connection refused"))

    monkeypatch.setattr(urllib.request.AbstractHTTPHandler, "do_open", fake_do_open)

    transport = UrllibHTTPTransport()
    with pytest.raises(TransportError) as excinfo:
        transport.send(
            HTTPRequest(
                method="GET",
                url="https://example.invalid/y",
                headers={"x-goog-api-key": secret},
            )
        )

    assert secret not in str(excinfo.value)


def test_a_transport_timeout_error_message_never_carries_a_header_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "SECRET-VALUE-THAT-MUST-NEVER-APPEAR-IN-A-TIMEOUT-MESSAGE"

    def fake_do_open(
        self: urllib.request.AbstractHTTPHandler,
        http_class: object,
        req: urllib.request.Request,
        **_: object,
    ) -> _FakeConnectionResponse:
        raise TimeoutError("read timed out")

    monkeypatch.setattr(urllib.request.AbstractHTTPHandler, "do_open", fake_do_open)

    transport = UrllibHTTPTransport()
    with pytest.raises(TransportTimeoutError) as excinfo:
        transport.send(
            HTTPRequest(
                method="GET",
                url="https://example.invalid/y",
                headers={"x-goog-api-key": secret},
            )
        )

    assert secret not in str(excinfo.value)
