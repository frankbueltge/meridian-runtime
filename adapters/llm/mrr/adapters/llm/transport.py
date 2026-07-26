"""A narrow HTTP transport abstraction for provider adapters under this
package (task-packets/E4-T08.yaml), plus its one standard-library
implementation.

This module is deliberately provider-free: :class:`HTTPRequest` and
:class:`HTTPResponse` know nothing about Gemini, an API key header, or any
other provider-specific shape -- ``mrr.adapters.llm.gemini`` builds those,
this module only moves opaque bytes and headers over HTTP. The concrete
:class:`GeminiModelAdapter` (``mrr.adapters.llm.gemini``) takes an
:class:`HTTPTransport` as a constructor-injected dependency rather than
opening a connection itself, so every adapter test drives an in-test double
implementing this Protocol -- never :class:`UrllibHTTPTransport`, never a
real socket (task-packets/E4-T08.yaml: "NO automated test performs real
network I/O -- every test drives an injected transport double").
:class:`UrllibHTTPTransport` itself is exercised directly, but only ever
against a monkeypatched connection layer
(tests/unit/adapters/llm/test_transport.py) -- see that module's own
docstring for why swapping ``urllib.request.AbstractHTTPHandler.do_open``
(rather than ``urllib.request.urlopen`` itself) is the seam that lets a test
prove the REAL redirect-refusal wiring works, without opening a socket of
any kind, not even to loopback.

--- Why stdlib ``urllib``, not ``httpx``/``requests`` -----------------------

task-packets/E4-T01.yaml already excluded provider SDKs by name
(``openai, anthropic, google-generativeai, boto3, litellm``); a generic HTTP
client library would still be a NEW dependency this task's own packet
forbids ("No new dependency - pyproject.toml is untouched and only the
standard library plus existing packages are imported"). Gemini's
``generateContent`` REST endpoint is fully reachable with nothing but
``urllib.request`` and ``json`` (docs/design/2026-07-26-fact-lock-provider-
adapter.md, Befund 3), so there is nothing to weigh here -- the standard
library is not merely acceptable, it is sufficient.

--- Redirects are refused STRUCTURALLY, not by omission ---------------------

docs/design/2026-07-26-fact-lock-provider-adapter.md, Befund 3, names a real,
open defect elsewhere in this repository: ``scripts/`` fetch scripts use
plain ``urllib.request.urlopen``, which follows redirects by default, and
their own allowlist gate checks only the REQUESTED url, not any URL a
redirect might later point at. This module does not inherit that gap.
:class:`UrllibHTTPTransport` builds its opener with :class:`_NoRedirectHandler`
installed in place of the stock ``urllib.request.HTTPRedirectHandler`` --
overriding ONLY ``redirect_request`` to return ``None`` (the exact seam
``HTTPRedirectHandler`` itself documents for "refuse this redirect"), which
makes every 301/302/303/307/308 response surface as an ordinary
:class:`HTTPResponse` carrying that 30x status, never transparently followed
to wherever ``Location`` points. This is a property of the CONNECTION
mechanism, verified in tests/unit/adapters/llm/test_transport.py by handing
the opener an actual redirect through a monkeypatched connection layer and
counting how many requests were made -- not a comment asserting the outcome.

Because this transport speaks to exactly ONE fixed provider address per call
(built by the caller, e.g. ``mrr.adapters.llm.gemini``, never taken from
untrusted input), it has no occasion to build the kind of allowlist the
``scripts/`` gate needs; refusing every redirect unconditionally is strictly
stronger and needs no allowlist to stay correct (task-packets/E4-T08.yaml
explicitly scopes closing the ``scripts/`` gap itself out: "this adapter
follows no redirects and does not inherit it; that gap remains a separate,
open defect").

--- What is, and is not, a transport failure --------------------------------

:meth:`HTTPTransport.send` returns an :class:`HTTPResponse` for EVERY
response actually received from the far end -- 200, 404, 429, 500, or a
refused 301-308 alike. Interpreting a non-2xx status is the CALLER's job
(``mrr.adapters.llm.gemini.GeminiModelAdapter`` maps any non-200
:class:`HTTPResponse` onto the ``"error"`` ``TerminalStatus``); this module
does not privilege 2xx over any other status code, since doing so would bury
a refused redirect's 30x status inside a raised exception instead of letting
the caller observe it as data, exactly like any other non-success response.

:meth:`HTTPTransport.send` raises :class:`TransportError` (or its
:class:`TransportTimeoutError` subclass) ONLY when no HTTP response was ever
obtained at all -- DNS failure, connection refused, a TLS failure, or the
request exceeding its timeout. :class:`TransportTimeoutError` is kept a
distinct, never-collapsed subclass of :class:`TransportError` specifically
so ``GeminiModelAdapter`` can map it onto the ``"timed_out"``
``TerminalStatus`` rather than the generic ``"error"`` one (AGENTS.md:
"collapsing ... into one generic error" is a named prohibited shortcut;
task-packets/E4-T08.yaml acceptance_criteria: "A timeout yields timed_out,
NOT error").
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from http.client import HTTPMessage
from typing import IO, Protocol, runtime_checkable

#: Gemini's documented request/response cycle for a single ``generateContent``
#: call comfortably completes in single-digit seconds; this is a generous
#: single-digit-minute ceiling -- long enough that a real, working call is
#: never cut off, short enough that a stalled connection surfaces as a
#: distinct ``TransportTimeoutError`` (task-packets/E4-T08.yaml: "the default
#: is a urllib transport with redirects disabled and a set timeout") instead
#: of hanging the caller indefinitely. A named module constant, not a literal
#: buried in a call, mirroring ``scripts/fetch_source_content.py``'s own
#: ``REQUEST_TIMEOUT_SECONDS`` precedent.
DEFAULT_TIMEOUT_SECONDS: float = 60.0


@dataclass(frozen=True, slots=True, kw_only=True)
class HTTPRequest:
    """One outbound HTTP request, provider-agnostic. ``headers`` carries
    everything a caller needs sent, including any credential -- this module
    has no opinion about what a header means; it is
    ``mrr.adapters.llm.gemini`` that ensures the Gemini API key lands ONLY in
    a header here, never folded into ``url``.
    """

    method: str
    url: str
    headers: Mapping[str, str]
    body: bytes | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class HTTPResponse:
    """One received HTTP response, whatever its status code -- see this
    module's docstring section "What is, and is not, a transport failure"
    for why a non-2xx status is represented here rather than raised.
    """

    status_code: int
    body: bytes
    headers: Mapping[str, str]


class TransportError(Exception):
    """Base class for every typed failure :meth:`HTTPTransport.send` may
    raise. Reserved for the case where no HTTP response was ever obtained at
    all (connection refused, DNS failure, TLS failure, an HTTP-level
    exchange that never completed) -- see this module's docstring section
    "What is, and is not, a transport failure". Never raised for a response
    that WAS received, however unwelcome its status code.
    """


class TransportTimeoutError(TransportError):
    """Raised when the request does not complete within the configured
    timeout. Kept a DISTINCT subclass of :class:`TransportError` -- never
    folded into it -- because ``mrr.adapters.llm.gemini.GeminiModelAdapter``
    must be able to map exactly this failure onto the
    ``mrr.domain.model_adapter.TerminalStatus`` value ``"timed_out"``, kept
    apart from the generic ``"error"`` value the same way AGENTS.md forbids
    collapsing any other pair of distinct outcomes.
    """


@runtime_checkable
class HTTPTransport(Protocol):
    """The Protocol every concrete transport implements, and the ONLY
    dependency a provider adapter under this package needs injected to reach
    a real network (task-packets/E4-T08.yaml: "the transport is injected in
    the constructor"). Mirrors ``mrr.domain.model_adapter.ModelAdapter``'s
    own shape one layer down: one method, no provider-specific argument.
    """

    def send(self, request: HTTPRequest) -> HTTPResponse:
        """Perform exactly one HTTP exchange and return its response.

        Raises:
            TransportError: no HTTP response was ever obtained (connection
                failure, DNS failure, TLS failure, or any other failure
                before/while receiving a response).
            TransportTimeoutError: the request did not complete within the
                configured timeout -- a distinct subclass of
                :class:`TransportError`, never raised as the bare base
                class, so a caller can tell the two apart with a single
                ``except TransportTimeoutError`` clause ahead of a broader
                ``except TransportError``.
        """
        ...


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Installed by :class:`UrllibHTTPTransport` in place of the stock
    ``urllib.request.HTTPRedirectHandler`` (``urllib.request.build_opener``:
    "If any of the handlers passed as arguments are subclasses of the
    default handlers, the default handlers will not be used" -- this class
    IS such a subclass, so building an opener with it present replaces, not
    adds to, the default redirect handler).

    Overrides ONLY :meth:`redirect_request`, returning ``None``
    unconditionally -- exactly the seam ``HTTPRedirectHandler.
    redirect_request``'s own docstring names for this purpose ("Return None
    if you can't [redirect] but another handler might"). Since no other
    handler in this opener registers a 301-308 handler either,
    ``http_error_301/302/303/307/308`` (all four aliased to the same
    ``http_error_302`` method on the base class, which this class does not
    override) see ``redirect_request`` return ``None`` and themselves return
    ``None`` in turn; ``urllib.request.OpenerDirector.error`` then falls
    through to ``HTTPDefaultErrorHandler.http_error_default``, which raises
    ``urllib.error.HTTPError`` carrying the ORIGINAL 30x response -- never a
    followed one. This is not a heuristic guess at urllib's behaviour: it is
    read directly from ``inspect.getsource`` of the installed interpreter's
    own ``urllib.request`` module at the time this class was written, and
    proven end-to-end (opener + this handler together, not this class in
    isolation) in tests/unit/adapters/llm/test_transport.py.
    """

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        return None


class UrllibHTTPTransport:
    """The default :class:`HTTPTransport`: standard-library ``urllib``,
    redirects disabled (:class:`_NoRedirectHandler`), a fixed timeout on
    every call. No provider SDK, no third-party HTTP client -- see this
    module's docstring section on why stdlib suffices.

    A NEW opener is built once per instance (not once per call, and not the
    process-global default opener ``urllib.request`` otherwise offers via
    ``urlopen``) so that redirect-refusal is a property of every request
    this instance ever sends, never dependent on -- or able to be
    overwritten by -- global interpreter state (``urllib.request.
    install_opener``).
    """

    def __init__(self, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        if timeout_seconds <= 0:
            raise ValueError(f"timeout_seconds must be > 0, got {timeout_seconds}")
        self._timeout_seconds = timeout_seconds
        self._opener = urllib.request.build_opener(_NoRedirectHandler)

    def send(self, request: HTTPRequest) -> HTTPResponse:
        """See :meth:`HTTPTransport.send`.

        A received non-2xx response (including a refused 301-308 redirect,
        which :class:`_NoRedirectHandler` turns into an
        ``urllib.error.HTTPError`` carrying the ORIGINAL redirect response --
        see that class's docstring) is still an :class:`HTTPResponse`, read
        from ``urllib.error.HTTPError`` the same way a 2xx response is read
        from the opener's normal return value -- never raised to the caller
        as an exception. Only ``TimeoutError`` (raised directly, e.g. from a
        stalled read) or ``urllib.error.URLError`` (connection-level
        failure, which also wraps a connect-phase timeout) become
        :class:`TransportError`/:class:`TransportTimeoutError`; both forms
        of timeout are handled because ``urllib.request.
        AbstractHTTPHandler.do_open`` only wraps a request-phase ``OSError``
        into ``URLError`` and lets a response-phase timeout propagate
        unwrapped (read directly from the installed interpreter's own
        ``do_open`` source; ``TimeoutError`` is ``socket.timeout`` since
        Python 3.10, itself an ``OSError`` subclass, so
        ``isinstance(reason, TimeoutError)`` catches both the bare and the
        ``URLError``-wrapped case).
        """
        urllib_request = urllib.request.Request(
            request.url,
            data=request.body,
            headers=dict(request.headers),
            method=request.method,
        )
        try:
            with self._opener.open(urllib_request, timeout=self._timeout_seconds) as response:
                status_code: int = response.status
                body: bytes = response.read()
                headers: dict[str, str] = dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            error_body: bytes = exc.read()
            error_headers: dict[str, str] = dict(exc.headers.items())
            return HTTPResponse(status_code=exc.code, body=error_body, headers=error_headers)
        except TimeoutError as exc:
            raise TransportTimeoutError(
                f"request to {request.url} timed out after {self._timeout_seconds}s"
            ) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise TransportTimeoutError(
                    f"request to {request.url} timed out after {self._timeout_seconds}s"
                ) from exc
            raise TransportError(f"request to {request.url} failed: {exc.reason}") from exc
        return HTTPResponse(status_code=status_code, body=body, headers=headers)


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "HTTPRequest",
    "HTTPResponse",
    "HTTPTransport",
    "TransportError",
    "TransportTimeoutError",
    "UrllibHTTPTransport",
]
