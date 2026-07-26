"""``GeminiModelAdapter`` (task-packets/E4-T08.yaml): the FIRST concrete
implementation of ``mrr.domain.model_adapter.ModelAdapter`` anywhere in this
repository. Five E4 packages plus the prompt registry and the benchmark
runner were built and tested entirely against an in-test fake
(docs/design/2026-07-26-fact-lock-provider-adapter.md, Befund 1) -- this
module is the edge that reaches a real provider, Google Gemini, over its
``generateContent`` REST endpoint, using nothing but the standard library
and this package's own ``transport`` module.

This module does not wire itself into anything (task-packets/E4-T08.yaml
explicitly_not: "NO WIRING INTO ANY ORCHESTRATION. No run performs a model
call as a result of this packet."). It builds the edge; a later, separate
packet performs the act, and per that same packet's derivation is REQUIRED
to first make the recorded network policy honest (both orchestration paths
today hardcode ``network_policy: deny_all`` -- wiring THIS adapter into a
run before that is fixed would make a run's own ``RunManifest`` lie about
what it permitted).

--- The three tragende Regeln (see also ``mrr.adapters.llm.transport``) ----

1. **The key goes in the header, never the URL.** Google's Generative
   Language API also accepts the key as the ``?key=...`` query parameter --
   the convenient and the wrong form, since a URL is exactly what ends up in
   exception text, timeout messages, and any request log (AGENTS.md rule
   11: "no ... secrets in prompts" -- the same reasoning extends to any
   place a secret could surface unintentionally). :meth:`GeminiModelAdapter.
   _build_request` assembles ``url`` from ONLY the adapter's own
   ``api_base_url``/``model_name`` -- the expression that builds it never
   references ``api_key`` at all, so the URL cannot carry the key even by
   accident. The key travels solely in the :data:`API_KEY_HEADER_NAME`
   (``"x-goog-api-key"``) header.
2. **The key comes from the environment, never a parameter.**
   :class:`GeminiModelAdapter` exposes no constructor argument, no method
   argument, no config field for the key anywhere. :meth:`GeminiModelAdapter.
   invoke` reads :data:`GEMINI_API_KEY_ENV_VAR` itself, fresh, at the start
   of every call, and raises :class:`MissingAPIKeyError` -- a typed refusal
   -- before ``self._transport.send`` is touched at all, if the variable is
   absent or empty.
3. **All five ``TerminalStatus`` values are mapped honestly, never
   collapsed.** See :func:`_parse_generate_content_response` for the mapping
   from a successful HTTP exchange onto ``"completed"``/``"refused"``/
   ``"content_filtered"``, and :meth:`GeminiModelAdapter.invoke` for how a
   failed exchange becomes ``"timed_out"`` or ``"error"`` -- never a single
   generic bucket (AGENTS.md: "collapsing ... into one generic error" is a
   named prohibited shortcut).

--- What this adapter deliberately does NOT do -----------------------------

- It does not send ``request.tool_names_available`` as Gemini ``tools``
  configuration, and it never populates ``ModelInvocationOutcome.
  tool_calls`` (always ``()``) -- mapping a model's ``functionCall`` parts
  onto ``mrr.domain.model_adapter.ToolCallOutcome`` needs its own design
  (in particular, deciding what ``result_hash`` means when MRR itself never
  actually executes the tool) and is not asked for by this task's
  acceptance criteria; inventing one here would be exactly the kind of
  unscoped domain behaviour AGENTS.md rule 3 forbids.
- It does not map ``request.operation_kind`` onto any Gemini
  ``generationConfig`` field (e.g. ``temperature``) -- not required by this
  task, and a later task can make that mapping deliberately rather than
  inheriting an undiscussed default from this one.
- It does not attempt to detect an in-content refusal (ordinary "I can't
  help with that" text, finish reason ``STOP``, no safety flag) as
  ``"refused"`` -- see :func:`_parse_generate_content_response`'s own
  docstring for why that is a genuine, honestly-documented limitation of
  what Gemini's response shape can structurally distinguish, not an
  oversight.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from mrr.adapters.llm.transport import (
    HTTPRequest,
    HTTPTransport,
    TransportError,
    TransportTimeoutError,
)
from mrr.crypto.hashing import content_hash
from mrr.domain.model_adapter import (
    ModelInvocationOutcome,
    ModelInvocationRequest,
    TokenUsage,
    apply_redaction,
)

#: The ONLY place this adapter ever looks for the Gemini API key
#: (task-packets/E4-T08.yaml: "Der API-Schlüssel wird AUSSCHLIESSLICH aus
#: der Umgebung gelesen"). Never a constructor parameter, never a file, never
#: a config entry.
GEMINI_API_KEY_ENV_VAR = "GEMINI_API_KEY"

#: Google's own documented header for the Generative Language API key --
#: the ALTERNATIVE to this, the ``?key=...`` query parameter, is never used
#: anywhere in this module (see this module's docstring, rule 1).
API_KEY_HEADER_NAME = "x-goog-api-key"

#: The real Gemini REST base (v1beta, the version the documented
#: ``generateContent`` response shape this module parses is written
#: against). Overridable per instance -- see :class:`GeminiModelAdapter` --
#: but never environment- or request-sourced.
DEFAULT_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

#: Used for BOTH genuine no-response outcomes: a transport-level failure
#: (``"error"``/``"timed_out"``, where no HTTP response was ever obtained at
#: all) and a non-200 HTTP response (an error body, never Gemini's
#: usageMetadata shape). Deliberately NOT reused for "a 200 response came
#: back but omitted usageMetadata entirely" -- that case is a shape error
#: that turns the WHOLE outcome into "error" instead (see
#: :func:`_extract_token_usage`), precisely so this constant is never
#: confused with "the provider reported zero and we discarded that report".
_NO_RESPONSE_TOKEN_USAGE = TokenUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0)


class GeminiAdapterError(Exception):
    """Base class for every typed error :class:`GeminiModelAdapter` raises
    DIRECTLY to its caller -- as opposed to reporting a failure inside a
    returned ``ModelInvocationOutcome.status``, which is where a genuine
    call outcome belongs (see :class:`MissingAPIKeyError`'s own docstring
    for why a missing key is not a call outcome).
    """


class MissingAPIKeyError(GeminiAdapterError):
    """Raised by :meth:`GeminiModelAdapter.invoke`, BEFORE the injected
    transport is touched at all, when :data:`GEMINI_API_KEY_ENV_VAR` is
    absent or empty in the environment (task-packets/E4-T08.yaml: "die
    Prüfung auf sein Fehlen ist eine typisierte Verweigerung, BEVOR der
    Transport überhaupt aufgerufen wird").

    A missing key is a MISCONFIGURATION, never a call outcome: none of
    ``mrr.domain.model_adapter.TerminalStatus``'s five values means "this
    adapter instance could not even attempt a call" -- returning e.g.
    ``status="error"`` here would silently conflate "the call was attempted
    and failed" with "no call was ever attempted", exactly the kind of
    collapsed distinction AGENTS.md forbids. Raising instead means a caller
    that does not handle this exception finds out immediately, at the first
    call, rather than the provider quietly explaining it 401 calls later.
    """

    def __init__(self) -> None:
        super().__init__(
            f"environment variable {GEMINI_API_KEY_ENV_VAR} is not set (or is empty) -- "
            "refusing to invoke the transport without an API key"
        )


class _GeminiResponseShapeError(GeminiAdapterError):
    """Internal control-flow signal ONLY -- raised by this module's own
    parsing helpers (:func:`_parse_generate_content_response`,
    :func:`_extract_token_usage`) when an HTTP-200 response body does not
    match the documented ``generateContent`` response shape closely enough
    to classify honestly (no ``usageMetadata`` object at all, a
    ``candidates[0]`` that is not a JSON object, a non-object top level,
    ...). ALWAYS caught inside :meth:`GeminiModelAdapter.invoke`, which maps
    it onto ``TerminalStatus`` ``"error"`` -- it never escapes this module,
    and is deliberately not exported via ``__all__``.
    """


@dataclass(frozen=True, slots=True)
class _ParsedResponse:
    """The result of classifying one HTTP-200 ``generateContent`` response
    body -- see :func:`_parse_generate_content_response`. ``text`` is
    populated if and only if ``status == "completed"``; a caller narrows
    this the same way it is constructed (``status == "completed" and text is
    not None``), never by an unchecked cast.
    """

    status: Literal["completed", "refused", "content_filtered"]
    text: str | None
    token_usage: TokenUsage


def _require_api_key() -> str:
    """Read :data:`GEMINI_API_KEY_ENV_VAR`. Raises :class:`MissingAPIKeyError`
    -- carrying no fragment of any key value, since none was found -- if the
    variable is unset or empty. The ONLY function in this module that reads
    the key from anywhere.
    """
    api_key = os.environ.get(GEMINI_API_KEY_ENV_VAR)
    if not api_key:
        raise MissingAPIKeyError()
    return api_key


def _compute_prompt_config_hash(request: ModelInvocationRequest, *, model_name: str) -> str:
    """MRR-FR-045's "prompt/configuration hash": a content hash over the
    prompt text together with every piece of configuration that governs
    what was sent (which profile and exact revision, which concrete
    provider model, the declared operation kind, which tools were
    available) -- computed BEFORE any network call, so it is present and
    identical in shape on ALL FIVE possible outcomes, never only on a
    successful one. ``json.dumps(..., sort_keys=True)`` gives a
    deterministic byte sequence for two calls built from an equal request
    and model name.
    """
    canonical = json.dumps(
        {
            "model_profile_id": request.model_profile_id,
            "model_profile_hash": request.model_profile_hash,
            "provider_model_name": model_name,
            "operation_kind": request.operation_kind,
            "tool_names_available": list(request.tool_names_available),
            "prompt_text": request.prompt_text,
        },
        sort_keys=True,
    ).encode("utf-8")
    return content_hash(canonical)


def _extract_candidate_text(candidate: Mapping[str, Any]) -> str:
    """Concatenate every text part of one ``candidates[]`` entry's
    ``content.parts``, in order. Returns ``""`` (never ``None``) if
    ``content``/``parts`` is absent or the wrong shape, or if no part
    carries a string ``text`` field -- an empty string and "no text" are the
    same fact to this adapter's classification (see
    :func:`_parse_generate_content_response`).
    """
    content = candidate.get("content")
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts")
    if not isinstance(parts, list):
        return ""
    fragments = [
        part["text"]
        for part in parts
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    ]
    return "".join(fragments)


def _extract_usage_count(usage: Mapping[str, Any], field_name: str) -> int:
    """Read one ``usageMetadata`` count field. ABSENT (the field simply is
    not a key of ``usage``) is read as ``0`` -- every real ``usageMetadata``
    payload observed at this task's derivation omits a zero-valued count
    rather than writing it out explicitly, so treating absence-of-this-one-
    field as zero is a faithful reading of what the provider reported, not
    a fabrication (contrast :func:`_extract_token_usage`, which treats the
    ``usageMetadata`` OBJECT itself being entirely absent very differently).
    A PRESENT field of the wrong type, or a negative number, is a shape
    error -- never silently coerced to 0 or truncated.
    """
    value = usage.get(field_name, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _GeminiResponseShapeError(
            f"usageMetadata.{field_name} is not a non-negative integer: {value!r}"
        )
    return value


def _extract_token_usage(payload: Mapping[str, Any]) -> TokenUsage:
    """Read ``usageMetadata`` from an already-JSON-decoded, HTTP-200
    response body. If the ``usageMetadata`` object is ENTIRELY absent --
    task-packets/E4-T08.yaml acceptance_criteria: "when the provider reports
    none, the recorded usage is explicit rather than silently zero-filled"
    -- this raises :class:`_GeminiResponseShapeError` rather than returning
    a ``TokenUsage`` of all zeros: a zero-filled ``TokenUsage`` sitting next
    to an otherwise-normal-looking ``"completed"``/``"refused"``/
    ``"content_filtered"`` outcome would be indistinguishable from "the
    provider genuinely reported zero tokens", silently hiding the real fact
    that the provider's response did not honour the documented contract at
    all. Called by :func:`_parse_generate_content_response` BEFORE it
    inspects anything else in the payload, so this same, single check
    covers all three successful-exchange statuses uniformly.
    """
    usage = payload.get("usageMetadata")
    if not isinstance(usage, dict):
        raise _GeminiResponseShapeError(
            "response carries no usageMetadata object at all -- refusing to record a "
            "fabricated zero token count"
        )
    return TokenUsage(
        prompt_tokens=_extract_usage_count(usage, "promptTokenCount"),
        completion_tokens=_extract_usage_count(usage, "candidatesTokenCount"),
        total_tokens=_extract_usage_count(usage, "totalTokenCount"),
    )


def _parse_generate_content_response(payload: Mapping[str, Any]) -> _ParsedResponse:
    """Classify one already-JSON-decoded, HTTP-200 ``generateContent``
    response body onto exactly one of the three ``TerminalStatus`` values a
    SUCCESSFUL HTTP exchange can produce -- ``"completed"``, ``"refused"``,
    ``"content_filtered"``. The other two (``"error"``, ``"timed_out"``) can
    only arise from a FAILED exchange and are decided entirely in
    :meth:`GeminiModelAdapter.invoke`, never here.

    The mapping (docs/design/2026-07-26-e4-t08-ableitung-gemini-adapter.md,
    section 5 -- "Die fünf Endzustände ehrlich abbilden"), in the order
    checked:

    1. ``promptFeedback.blockReason`` present (any truthy value) ->
       ``"content_filtered"`` -- the PROMPT itself was blocked before any
       generation happened; there is no candidate to look at at all in this
       case.
    2. ``candidates`` absent, not a list, or empty, with no ``blockReason``
       either -> ``"refused"`` -- the model's turn ended with nothing to
       show and no filter was ever recorded for it.
    3. ``candidates[0].finishReason == "SAFETY"`` -> ``"content_filtered"``
       -- checked BEFORE looking at any text the candidate might still
       carry, so a partially-filtered fragment is never mistaken for a
       normal completion.
    4. ``candidates[0]`` carries non-empty text (any other
       ``finishReason`` -- ``"STOP"``, ``"MAX_TOKENS"``, or anything else
       non-SAFETY) -> ``"completed"``. Truncation by ``MAX_TOKENS`` is still
       real text that was produced, not a refusal.
    5. Otherwise (a candidate exists, was not safety-filtered, but carries
       no text) -> ``"refused"``.

    Token usage (:func:`_extract_token_usage`) is read FIRST, before any of
    the above -- see that function's own docstring for why a wholly-missing
    ``usageMetadata`` escalates the ENTIRE outcome to ``"error"`` rather than
    silently zero-filling it under any of the three statuses above.

    --- An honest limitation, not smoothed over ----------------------------

    A model's own in-CONTENT refusal (ordinary text reading e.g. "I can't
    help with that", finish reason ``"STOP"``, no safety flag anywhere) is,
    at the wire level, INDISTINGUISHABLE from any other completed response
    carrying text: Gemini's REST shape gives no structural signal for "this
    text IS a refusal" the way it gives one for "a filter intervened"
    (``SAFETY``) or "nothing was produced" (case 2/5 above). This function
    does not attempt semantic judgement of response text to guess at a
    refusal hiding inside otherwise-normal-shaped output -- doing so would
    be exactly the kind of unverified interpretive leap AGENTS.md rules 3
    and 7 forbid. The ``"refused"`` status this function DOES produce
    captures the real, structural Gemini signal available for "the model
    produced nothing and no filter fired" -- a deliberately narrower reading
    of "refused" than the design derivation's prose alone might suggest,
    flagged here for reviewer attention (task-packets/E4-T08.yaml: "solche
    Korrekturen sind ausdrücklich erwünscht").
    """
    token_usage = _extract_token_usage(payload)

    prompt_feedback = payload.get("promptFeedback")
    if isinstance(prompt_feedback, dict) and prompt_feedback.get("blockReason"):
        return _ParsedResponse(status="content_filtered", text=None, token_usage=token_usage)

    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return _ParsedResponse(status="refused", text=None, token_usage=token_usage)

    candidate = candidates[0]
    if not isinstance(candidate, dict):
        raise _GeminiResponseShapeError("candidates[0] is not a JSON object")

    if candidate.get("finishReason") == "SAFETY":
        return _ParsedResponse(status="content_filtered", text=None, token_usage=token_usage)

    text = _extract_candidate_text(candidate)
    if text:
        return _ParsedResponse(status="completed", text=text, token_usage=token_usage)

    return _ParsedResponse(status="refused", text=None, token_usage=token_usage)


class GeminiModelAdapter:
    """Concrete ``mrr.domain.model_adapter.ModelAdapter`` for Google
    Gemini's ``generateContent`` REST endpoint -- see this module's
    docstring for the three tragende Regeln this class exists to uphold and
    the "what this adapter deliberately does NOT do" section.

    ``isinstance(adapter, ModelAdapter)`` holds against the UNCHANGED,
    ``runtime_checkable`` Protocol (tests/unit/adapters/llm/
    test_gemini_adapter.py) -- this class implements exactly the one method
    the Protocol declares, ``invoke``, and nothing on
    ``mrr.domain.model_adapter`` is modified by this task.
    """

    def __init__(
        self,
        *,
        transport: HTTPTransport,
        model_name: str,
        api_base_url: str = DEFAULT_API_BASE_URL,
    ) -> None:
        """
        Args:
            transport: the injected HTTP transport (task-packets/E4-T08.yaml:
                "der Transport wird im Konstruktor injiziert") -- every test
                passes a double implementing
                ``mrr.adapters.llm.transport.HTTPTransport``, never
                ``UrllibHTTPTransport``.
            model_name: the concrete Gemini model id (e.g.
                ``"gemini-2.0-flash"``) this instance calls.
                ``ModelInvocationRequest`` pins a
                ``model_profile_id``/``model_profile_hash``, not a literal
                provider model string -- see this module's docstring for why
                resolving one from the other is deliberately left to this
                adapter's caller rather than done here.
            api_base_url: overridable for testing/alternate deployments;
                defaults to the real Gemini v1beta REST base.

        Raises:
            ValueError: ``model_name`` or ``api_base_url`` is empty.
        """
        if not model_name:
            raise ValueError("model_name must not be empty")
        if not api_base_url:
            raise ValueError("api_base_url must not be empty")
        self._transport = transport
        self._model_name = model_name
        self._api_base_url = api_base_url

    def invoke(self, request: ModelInvocationRequest) -> ModelInvocationOutcome:
        """See ``mrr.domain.model_adapter.ModelAdapter.invoke``.

        Reads the API key FIRST (:func:`_require_api_key`), before building
        any request or touching ``self._transport`` at all -- a missing key
        raises :class:`MissingAPIKeyError` and returns no
        ``ModelInvocationOutcome`` (see that class's own docstring for why).

        ``request.redaction_policy`` is carried UNCHANGED into every
        returned outcome and into ``apply_redaction`` -- this adapter never
        overrides or infers it (task-packets/E4-T08.yaml: "der Adapter
        entscheidet sie NIE selbst"). Under ``"raw_permitted"``,
        ``raw_prompt_text`` is populated on EVERY returned outcome
        (including ``"error"``/``"timed_out"``/``"refused"``/
        ``"content_filtered"``) since the prompt itself is known before any
        network call is even attempted, regardless of how the call turns
        out -- exactly the data an experiment measuring model FAILURE modes
        needs (docs/design/2026-07-26-fact-lock-provider-adapter.md, Befund
        5). ``raw_response_text`` is populated ONLY alongside a
        ``"completed"`` outcome's ``response_hash``, via
        ``apply_redaction`` -- never for a filtered/refused response, since
        exposing a safety-filtered fragment would work against the filter
        that produced it.
        """
        api_key = _require_api_key()
        prompt_config_hash = _compute_prompt_config_hash(request, model_name=self._model_name)
        raw_prompt_text = (
            request.prompt_text if request.redaction_policy == "raw_permitted" else None
        )

        http_request = self._build_request(request, api_key)

        try:
            http_response = self._transport.send(http_request)
        except TransportTimeoutError:
            return ModelInvocationOutcome(
                status="timed_out",
                prompt_config_hash=prompt_config_hash,
                token_usage=_NO_RESPONSE_TOKEN_USAGE,
                redaction_policy=request.redaction_policy,
                raw_prompt_text=raw_prompt_text,
            )
        except TransportError:
            return ModelInvocationOutcome(
                status="error",
                prompt_config_hash=prompt_config_hash,
                token_usage=_NO_RESPONSE_TOKEN_USAGE,
                redaction_policy=request.redaction_policy,
                raw_prompt_text=raw_prompt_text,
            )

        if http_response.status_code != 200:
            # Any non-200 status -- including a refused 301-308 redirect,
            # which mrr.adapters.llm.transport.UrllibHTTPTransport surfaces
            # as an ordinary HTTPResponse rather than raising -- is an HTTP
            # failure. Gemini's own error body ({"error": {...}}) carries no
            # usageMetadata, so there is genuinely nothing to record beyond
            # _NO_RESPONSE_TOKEN_USAGE.
            return ModelInvocationOutcome(
                status="error",
                prompt_config_hash=prompt_config_hash,
                token_usage=_NO_RESPONSE_TOKEN_USAGE,
                redaction_policy=request.redaction_policy,
                raw_prompt_text=raw_prompt_text,
            )

        try:
            payload = json.loads(http_response.body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise _GeminiResponseShapeError("top-level response body is not a JSON object")
            parsed = _parse_generate_content_response(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, _GeminiResponseShapeError):
            return ModelInvocationOutcome(
                status="error",
                prompt_config_hash=prompt_config_hash,
                token_usage=_NO_RESPONSE_TOKEN_USAGE,
                redaction_policy=request.redaction_policy,
                raw_prompt_text=raw_prompt_text,
            )

        if parsed.status == "completed" and parsed.text is not None:
            response_hash, raw_response_text = apply_redaction(
                request.redaction_policy, parsed.text
            )
            return ModelInvocationOutcome(
                status="completed",
                prompt_config_hash=prompt_config_hash,
                token_usage=parsed.token_usage,
                redaction_policy=request.redaction_policy,
                response_hash=response_hash,
                raw_response_text=raw_response_text,
                raw_prompt_text=raw_prompt_text,
            )

        return ModelInvocationOutcome(
            status=parsed.status,
            prompt_config_hash=prompt_config_hash,
            token_usage=parsed.token_usage,
            redaction_policy=request.redaction_policy,
            raw_prompt_text=raw_prompt_text,
        )

    def _build_request(self, request: ModelInvocationRequest, api_key: str) -> HTTPRequest:
        """Build the outbound ``HTTPRequest``. ``url`` is assembled from
        ONLY ``self._api_base_url``/``self._model_name`` -- ``api_key``
        never appears in the expression that builds it (this module's
        docstring, rule 1); it travels solely in the
        :data:`API_KEY_HEADER_NAME` header.
        """
        url = f"{self._api_base_url}/models/{self._model_name}:generateContent"
        headers = {
            "Content-Type": "application/json",
            API_KEY_HEADER_NAME: api_key,
        }
        body = json.dumps(
            {"contents": [{"parts": [{"text": request.prompt_text}]}]}, sort_keys=True
        ).encode("utf-8")
        return HTTPRequest(method="POST", url=url, headers=headers, body=body)


__all__ = [
    "API_KEY_HEADER_NAME",
    "DEFAULT_API_BASE_URL",
    "GEMINI_API_KEY_ENV_VAR",
    "GeminiAdapterError",
    "GeminiModelAdapter",
    "MissingAPIKeyError",
]
