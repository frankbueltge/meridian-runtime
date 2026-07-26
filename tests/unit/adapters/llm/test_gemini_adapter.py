"""Unit tests for ``mrr.adapters.llm.gemini`` (task-packets/E4-T08.yaml).

Every test drives an in-test transport double implementing
``mrr.adapters.llm.transport.HTTPTransport`` -- never ``UrllibHTTPTransport``,
never a real socket of any kind (task-packets/E4-T08.yaml: "NO automated
test performs real network I/O -- every test drives an injected transport
double"). ``_clean_environment`` (autouse) deletes ``GEMINI_API_KEY`` from
the environment before every test, so no test result can ever depend on
whatever happens to be set on the machine running it; tests that need a key
present set one explicitly, always an obviously-fake value.

Covers the packet's own acceptance oracle in full:

- THE SHARP CASE -- each of the five ``TerminalStatus`` values produced from
  its own recorded provider response, never a generic ``"error"`` bucket
  (the ``# --- sharp case ---`` section).
- THE KEY NEVER LEAKS -- across all five paths, the API key never appears
  in the constructed URL, in ``MissingAPIKeyError``'s message, or anywhere
  in a returned ``ModelInvocationOutcome``, and nothing is ever written to
  stdout/stderr (the ``# --- the key never leaks ---`` section).
- the key is read from the environment only, and a missing/empty key is a
  typed refusal raised BEFORE the transport is touched (the
  ``# --- missing api key ---`` section).
- ``isinstance(adapter, ModelAdapter)`` holds against the unchanged port.
- ``response_hash`` present iff ``status == "completed"``.
- redaction policy passed through unchanged, never chosen by the adapter.
- token usage read from ``usageMetadata``, explicit (not silently
  zero-filled) when the provider reports none at all.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from mrr.adapters.llm.gemini import (
    API_KEY_HEADER_NAME,
    DEFAULT_API_BASE_URL,
    GEMINI_API_KEY_ENV_VAR,
    GeminiModelAdapter,
    MissingAPIKeyError,
)
from mrr.adapters.llm.transport import (
    HTTPRequest,
    HTTPResponse,
    TransportError,
    TransportTimeoutError,
)
from mrr.domain.model_adapter import (
    ModelAdapter,
    ModelInvocationRequest,
    RedactionPolicy,
    TerminalStatus,
    TokenUsage,
)

_VALID_PROFILE_URN = "urn:mrr:model-profile:01ARZ3NDEKTSV4RRFFQ69G5FAV"
_VALID_PROFILE_HASH = "sha256:" + "a" * 64
_FAKE_MODEL_NAME = "gemini-2.0-flash"
_SECRET_KEY_VALUE = "SECRET-GEMINI-KEY-MUST-NEVER-LEAK-9f3c2a"


# ---------------------------------------------------------------------------
# Fixtures: a clean environment by default, request/response builders, and
# the transport doubles this whole module drives instead of any real socket.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(GEMINI_API_KEY_ENV_VAR, raising=False)


def _request(**overrides: object) -> ModelInvocationRequest:
    defaults: dict[str, object] = {
        "model_profile_id": _VALID_PROFILE_URN,
        "model_profile_hash": _VALID_PROFILE_HASH,
        "prompt_text": "Say hello in one short sentence.",
        "operation_kind": "stochastic",
        "redaction_policy": "raw_permitted",
    }
    defaults.update(overrides)
    return ModelInvocationRequest(**defaults)  # type: ignore[arg-type]


def _json_response(payload: dict[str, Any], *, status_code: int = 200) -> HTTPResponse:
    return HTTPResponse(
        status_code=status_code,
        body=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )


class _ScriptedTransport:
    """Returns one canned ``HTTPResponse`` or raises one canned exception,
    recording every ``HTTPRequest`` it was called with -- exactly one of
    ``response``/``exception`` must be given.
    """

    def __init__(
        self, *, response: HTTPResponse | None = None, exception: Exception | None = None
    ) -> None:
        assert (response is None) != (exception is None), "give exactly one of response/exception"
        self._response = response
        self._exception = exception
        self.calls: list[HTTPRequest] = []

    def send(self, request: HTTPRequest) -> HTTPResponse:
        self.calls.append(request)
        if self._exception is not None:
            raise self._exception
        assert self._response is not None
        return self._response


class _FailIfCalledTransport:
    """A transport double that fails the test outright if ``send`` is ever
    invoked -- used to prove the missing-API-key refusal happens strictly
    before the transport is touched.
    """

    def send(self, request: HTTPRequest) -> HTTPResponse:
        raise AssertionError(
            "transport.send was called -- the missing API key was not refused first"
        )


# Canned generateContent response bodies, one per TerminalStatus this
# adapter can honestly produce from a successful HTTP exchange, plus the
# failure-shaped ones that arise before/around it. Each is the SOLE source
# for its scenario's outcome -- the sharp case.

_COMPLETED_PAYLOAD = {
    "candidates": [
        {
            "content": {"parts": [{"text": "Hello! Nice to meet you."}], "role": "model"},
            "finishReason": "STOP",
            "index": 0,
        }
    ],
    "usageMetadata": {"promptTokenCount": 8, "candidatesTokenCount": 6, "totalTokenCount": 14},
}

_CONTENT_FILTERED_BLOCKED_PROMPT_PAYLOAD = {
    "promptFeedback": {"blockReason": "SAFETY", "safetyRatings": []},
    "usageMetadata": {"promptTokenCount": 12, "totalTokenCount": 12},
}

_CONTENT_FILTERED_SAFETY_FINISH_PAYLOAD = {
    "candidates": [
        {
            "content": {"parts": [], "role": "model"},
            "finishReason": "SAFETY",
            "index": 0,
            "safetyRatings": [
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "probability": "HIGH"}
            ],
        }
    ],
    "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 0, "totalTokenCount": 9},
}

_REFUSED_EMPTY_CANDIDATE_PAYLOAD = {
    "candidates": [
        {"content": {"parts": [], "role": "model"}, "finishReason": "OTHER", "index": 0}
    ],
    "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 0, "totalTokenCount": 7},
}

_REFUSED_NO_CANDIDATES_PAYLOAD = {
    "usageMetadata": {"promptTokenCount": 5, "totalTokenCount": 5},
}


# ---------------------------------------------------------------------------
# THE SHARP CASE -- each of the five TerminalStatus values, its own recorded
# response, never a generic error bucket.
# ---------------------------------------------------------------------------


def test_completed_from_a_text_bearing_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GEMINI_API_KEY_ENV_VAR, _SECRET_KEY_VALUE)
    transport = _ScriptedTransport(response=_json_response(_COMPLETED_PAYLOAD))
    adapter = GeminiModelAdapter(transport=transport, model_name=_FAKE_MODEL_NAME)

    outcome = adapter.invoke(_request())

    assert outcome.status == "completed"
    assert outcome.response_hash is not None
    assert outcome.raw_response_text == "Hello! Nice to meet you."
    assert outcome.token_usage == TokenUsage(prompt_tokens=8, completion_tokens=6, total_tokens=14)
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "payload", [_CONTENT_FILTERED_BLOCKED_PROMPT_PAYLOAD, _CONTENT_FILTERED_SAFETY_FINISH_PAYLOAD]
)
def test_content_filtered_is_not_error(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]
) -> None:
    monkeypatch.setenv(GEMINI_API_KEY_ENV_VAR, _SECRET_KEY_VALUE)
    transport = _ScriptedTransport(response=_json_response(payload))
    adapter = GeminiModelAdapter(transport=transport, model_name=_FAKE_MODEL_NAME)

    outcome = adapter.invoke(_request())

    assert outcome.status == "content_filtered"
    assert outcome.response_hash is None


@pytest.mark.parametrize(
    "payload", [_REFUSED_EMPTY_CANDIDATE_PAYLOAD, _REFUSED_NO_CANDIDATES_PAYLOAD]
)
def test_refused_is_its_own_status(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]
) -> None:
    monkeypatch.setenv(GEMINI_API_KEY_ENV_VAR, _SECRET_KEY_VALUE)
    transport = _ScriptedTransport(response=_json_response(payload))
    adapter = GeminiModelAdapter(transport=transport, model_name=_FAKE_MODEL_NAME)

    outcome = adapter.invoke(_request())

    assert outcome.status == "refused"
    assert outcome.status not in ("error", "content_filtered")
    assert outcome.response_hash is None


def test_timed_out_is_not_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GEMINI_API_KEY_ENV_VAR, _SECRET_KEY_VALUE)
    transport = _ScriptedTransport(exception=TransportTimeoutError("request timed out after 60s"))
    adapter = GeminiModelAdapter(transport=transport, model_name=_FAKE_MODEL_NAME)

    outcome = adapter.invoke(_request())

    assert outcome.status == "timed_out"
    assert outcome.response_hash is None
    assert outcome.token_usage == TokenUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0)


@pytest.mark.parametrize(
    "transport",
    [
        _ScriptedTransport(exception=TransportError("connection refused")),
        _ScriptedTransport(
            response=HTTPResponse(status_code=429, body=b'{"error": {"code": 429}}', headers={})
        ),
        _ScriptedTransport(
            response=HTTPResponse(status_code=200, body=b"not valid json at all", headers={})
        ),
    ],
    ids=["transport-failure", "http-429", "malformed-json-body"],
)
def test_error_from_transport_http_and_parse_failures(
    monkeypatch: pytest.MonkeyPatch, transport: _ScriptedTransport
) -> None:
    monkeypatch.setenv(GEMINI_API_KEY_ENV_VAR, _SECRET_KEY_VALUE)
    adapter = GeminiModelAdapter(transport=transport, model_name=_FAKE_MODEL_NAME)

    outcome = adapter.invoke(_request())

    assert outcome.status == "error"
    assert outcome.response_hash is None


def test_error_when_a_completed_shaped_response_omits_usage_metadata_entirely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """task-packets/E4-T08.yaml acceptance_criteria: "when the provider
    reports none, the recorded usage is explicit rather than silently
    zero-filled." A response with real candidate text but NO usageMetadata
    object at all must not become a lying "completed, 0 tokens" outcome --
    it becomes an explicit "error" instead.
    """
    monkeypatch.setenv(GEMINI_API_KEY_ENV_VAR, _SECRET_KEY_VALUE)
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": "this looks like a normal answer"}],
                    "role": "model",
                },
                "finishReason": "STOP",
                "index": 0,
            }
        ]
        # usageMetadata deliberately absent.
    }
    transport = _ScriptedTransport(response=_json_response(payload))
    adapter = GeminiModelAdapter(transport=transport, model_name=_FAKE_MODEL_NAME)

    outcome = adapter.invoke(_request())

    assert outcome.status == "error"


def test_a_missing_individual_usage_count_field_defaults_to_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The narrower, documented convention: Gemini omits a zero-valued
    count field from an otherwise-PRESENT usageMetadata object -- NOT the
    same situation as usageMetadata being entirely absent (the previous
    test), and defaulting the omitted field to 0 is a faithful reading, not
    a fabrication.
    """
    monkeypatch.setenv(GEMINI_API_KEY_ENV_VAR, _SECRET_KEY_VALUE)
    payload = {
        "candidates": [
            {
                "content": {"parts": [{"text": "short"}], "role": "model"},
                "finishReason": "STOP",
                "index": 0,
            }
        ],
        "usageMetadata": {"promptTokenCount": 4, "totalTokenCount": 4},
        # candidatesTokenCount omitted -- Gemini's own zero-omission convention.
    }
    transport = _ScriptedTransport(response=_json_response(payload))
    adapter = GeminiModelAdapter(transport=transport, model_name=_FAKE_MODEL_NAME)

    outcome = adapter.invoke(_request())

    assert outcome.status == "completed"
    assert outcome.token_usage == TokenUsage(prompt_tokens=4, completion_tokens=0, total_tokens=4)


@pytest.mark.parametrize("bad_value", ["not-a-number", -1, True])
def test_a_malformed_usage_count_field_is_an_error_not_a_coercion(
    monkeypatch: pytest.MonkeyPatch, bad_value: object
) -> None:
    monkeypatch.setenv(GEMINI_API_KEY_ENV_VAR, _SECRET_KEY_VALUE)
    payload = {
        "candidates": [
            {
                "content": {"parts": [{"text": "short"}], "role": "model"},
                "finishReason": "STOP",
                "index": 0,
            }
        ],
        "usageMetadata": {"promptTokenCount": bad_value, "totalTokenCount": 4},
    }
    transport = _ScriptedTransport(response=_json_response(payload))
    adapter = GeminiModelAdapter(transport=transport, model_name=_FAKE_MODEL_NAME)

    outcome = adapter.invoke(_request())

    assert outcome.status == "error"


# ---------------------------------------------------------------------------
# response_hash <-> status=="completed" biconditional, demonstrated per
# status (the port already enforces this structurally; this proves the
# adapter never tries to work around it).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("transport", "expected_status"),
    [
        (_ScriptedTransport(response=_json_response(_COMPLETED_PAYLOAD)), "completed"),
        (
            _ScriptedTransport(response=_json_response(_CONTENT_FILTERED_SAFETY_FINISH_PAYLOAD)),
            "content_filtered",
        ),
        (_ScriptedTransport(response=_json_response(_REFUSED_EMPTY_CANDIDATE_PAYLOAD)), "refused"),
        (_ScriptedTransport(exception=TransportTimeoutError("timeout")), "timed_out"),
        (_ScriptedTransport(exception=TransportError("boom")), "error"),
    ],
    ids=["completed", "content_filtered", "refused", "timed_out", "error"],
)
def test_response_hash_present_iff_completed(
    monkeypatch: pytest.MonkeyPatch, transport: _ScriptedTransport, expected_status: TerminalStatus
) -> None:
    monkeypatch.setenv(GEMINI_API_KEY_ENV_VAR, _SECRET_KEY_VALUE)
    adapter = GeminiModelAdapter(transport=transport, model_name=_FAKE_MODEL_NAME)

    outcome = adapter.invoke(_request())

    assert outcome.status == expected_status
    if expected_status == "completed":
        assert outcome.response_hash is not None
    else:
        assert outcome.response_hash is None
    # prompt_config_hash is ALWAYS present, regardless of outcome -- it is
    # computed before the transport is ever touched.
    assert outcome.prompt_config_hash.startswith("sha256:")


# ---------------------------------------------------------------------------
# THE KEY NEVER LEAKS -- across all five paths: not in the URL, not in any
# exception message, not in any returned field, not on stdout/stderr.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "transport",
    [
        _ScriptedTransport(response=_json_response(_COMPLETED_PAYLOAD)),
        _ScriptedTransport(response=_json_response(_CONTENT_FILTERED_SAFETY_FINISH_PAYLOAD)),
        _ScriptedTransport(response=_json_response(_REFUSED_EMPTY_CANDIDATE_PAYLOAD)),
        _ScriptedTransport(exception=TransportTimeoutError("timeout after 60s")),
        _ScriptedTransport(exception=TransportError("connection refused")),
    ],
    ids=["completed", "content_filtered", "refused", "timed_out", "error"],
)
def test_the_key_never_appears_in_the_constructed_url_on_any_of_the_five_paths(
    monkeypatch: pytest.MonkeyPatch,
    transport: _ScriptedTransport,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(GEMINI_API_KEY_ENV_VAR, _SECRET_KEY_VALUE)
    adapter = GeminiModelAdapter(transport=transport, model_name=_FAKE_MODEL_NAME)

    outcome = adapter.invoke(_request(redaction_policy="raw_permitted"))

    assert len(transport.calls) == 1
    sent_request = transport.calls[0]
    assert _SECRET_KEY_VALUE not in sent_request.url
    assert sent_request.url.startswith(DEFAULT_API_BASE_URL)
    # The key IS expected in the header -- that is the whole point.
    assert sent_request.headers[API_KEY_HEADER_NAME] == _SECRET_KEY_VALUE

    # Scan the actual returned outcome's every string-valued field for the
    # key, rather than trusting that no field happens to carry it.
    for field_value in (
        outcome.status,
        outcome.prompt_config_hash,
        outcome.response_hash,
        outcome.raw_prompt_text,
        outcome.raw_response_text,
    ):
        if field_value is not None:
            assert _SECRET_KEY_VALUE not in field_value

    captured = capsys.readouterr()
    assert _SECRET_KEY_VALUE not in captured.out
    assert _SECRET_KEY_VALUE not in captured.err
    assert captured.out == ""
    assert captured.err == ""


def test_the_key_never_appears_in_a_raised_transport_error_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even though this adapter never constructs a TransportError message
    itself (it only catches the type -- see
    tests/unit/adapters/llm/test_transport.py for the real
    UrllibHTTPTransport's own guarantee that its messages never interpolate
    a header value), a caller-visible propagated message from elsewhere in
    the chain must still not carry the key -- proven by inspecting the
    actually-raised transport exception's string form, not by reading the
    code. The sent request's ``.url`` -- the ONE field this adapter's own
    exception-handling ever reasons about, per this module's docstring rule
    1 -- is checked separately; ``.headers`` legitimately carries the key
    (that is the whole point of rule 1), so it is deliberately NOT scanned
    here.
    """
    monkeypatch.setenv(GEMINI_API_KEY_ENV_VAR, _SECRET_KEY_VALUE)
    transport_error = TransportError("boom, no key referenced here")
    transport = _ScriptedTransport(exception=transport_error)
    adapter = GeminiModelAdapter(transport=transport, model_name=_FAKE_MODEL_NAME)

    outcome = adapter.invoke(_request())

    assert outcome.status == "error"
    assert _SECRET_KEY_VALUE not in str(transport_error)
    assert _SECRET_KEY_VALUE not in transport.calls[0].url


# ---------------------------------------------------------------------------
# Missing API key: a typed refusal, raised before the transport is touched.
# ---------------------------------------------------------------------------


def test_missing_api_key_raises_before_the_transport_is_touched() -> None:
    adapter = GeminiModelAdapter(transport=_FailIfCalledTransport(), model_name=_FAKE_MODEL_NAME)

    with pytest.raises(MissingAPIKeyError):
        adapter.invoke(_request())


def test_empty_api_key_is_treated_the_same_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GEMINI_API_KEY_ENV_VAR, "")
    adapter = GeminiModelAdapter(transport=_FailIfCalledTransport(), model_name=_FAKE_MODEL_NAME)

    with pytest.raises(MissingAPIKeyError):
        adapter.invoke(_request())


def test_missing_api_key_error_message_carries_no_key_fragment() -> None:
    error = MissingAPIKeyError()
    assert GEMINI_API_KEY_ENV_VAR in str(error)
    # There is no key value to leak in this scenario by construction (none
    # was found) -- this asserts the message stays about the ENV VAR NAME,
    # never about a value.
    assert "key=" not in str(error).lower()


def test_missing_api_key_has_no_constructor_parameter_for_a_key() -> None:
    """The adapter exposes no parameter, file path, or config entry for the
    key anywhere (task-packets/E4-T08.yaml acceptance_criteria) -- proven by
    the constructor signature itself accepting only transport/model_name/
    api_base_url.
    """
    import inspect

    signature = inspect.signature(GeminiModelAdapter.__init__)
    parameter_names = set(signature.parameters) - {"self"}
    assert parameter_names == {"transport", "model_name", "api_base_url"}


# ---------------------------------------------------------------------------
# Protocol conformance against the UNCHANGED port.
# ---------------------------------------------------------------------------


def test_gemini_adapter_conforms_to_the_unchanged_model_adapter_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(GEMINI_API_KEY_ENV_VAR, _SECRET_KEY_VALUE)
    adapter = GeminiModelAdapter(
        transport=_ScriptedTransport(response=_json_response(_COMPLETED_PAYLOAD)),
        model_name=_FAKE_MODEL_NAME,
    )
    assert isinstance(adapter, ModelAdapter)


# ---------------------------------------------------------------------------
# Redaction policy: passed through unchanged, never decided by the adapter.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("policy", ["hashes_only", "raw_permitted"])
def test_redaction_policy_is_echoed_back_unchanged(
    monkeypatch: pytest.MonkeyPatch, policy: RedactionPolicy
) -> None:
    monkeypatch.setenv(GEMINI_API_KEY_ENV_VAR, _SECRET_KEY_VALUE)
    transport = _ScriptedTransport(response=_json_response(_COMPLETED_PAYLOAD))
    adapter = GeminiModelAdapter(transport=transport, model_name=_FAKE_MODEL_NAME)

    outcome = adapter.invoke(_request(redaction_policy=policy))

    assert outcome.redaction_policy == policy


def test_hashes_only_carries_no_raw_text_on_any_of_the_five_statuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(GEMINI_API_KEY_ENV_VAR, _SECRET_KEY_VALUE)
    scenarios: list[_ScriptedTransport] = [
        _ScriptedTransport(response=_json_response(_COMPLETED_PAYLOAD)),
        _ScriptedTransport(response=_json_response(_CONTENT_FILTERED_SAFETY_FINISH_PAYLOAD)),
        _ScriptedTransport(response=_json_response(_REFUSED_EMPTY_CANDIDATE_PAYLOAD)),
        _ScriptedTransport(exception=TransportTimeoutError("timeout")),
        _ScriptedTransport(exception=TransportError("boom")),
    ]
    for transport in scenarios:
        adapter = GeminiModelAdapter(transport=transport, model_name=_FAKE_MODEL_NAME)
        outcome = adapter.invoke(_request(redaction_policy="hashes_only"))
        assert outcome.raw_prompt_text is None
        assert outcome.raw_response_text is None


def test_raw_permitted_carries_raw_prompt_text_on_every_status_including_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(GEMINI_API_KEY_ENV_VAR, _SECRET_KEY_VALUE)
    prompt_text = "a distinctive prompt used only in this test"
    scenarios: list[_ScriptedTransport] = [
        _ScriptedTransport(response=_json_response(_COMPLETED_PAYLOAD)),
        _ScriptedTransport(response=_json_response(_CONTENT_FILTERED_SAFETY_FINISH_PAYLOAD)),
        _ScriptedTransport(response=_json_response(_REFUSED_EMPTY_CANDIDATE_PAYLOAD)),
        _ScriptedTransport(exception=TransportTimeoutError("timeout")),
        _ScriptedTransport(exception=TransportError("boom")),
    ]
    for transport in scenarios:
        adapter = GeminiModelAdapter(transport=transport, model_name=_FAKE_MODEL_NAME)
        outcome = adapter.invoke(
            _request(redaction_policy="raw_permitted", prompt_text=prompt_text)
        )
        assert outcome.raw_prompt_text == prompt_text


def test_raw_permitted_carries_raw_response_text_only_on_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(GEMINI_API_KEY_ENV_VAR, _SECRET_KEY_VALUE)
    transport = _ScriptedTransport(response=_json_response(_CONTENT_FILTERED_SAFETY_FINISH_PAYLOAD))
    adapter = GeminiModelAdapter(transport=transport, model_name=_FAKE_MODEL_NAME)

    outcome = adapter.invoke(_request(redaction_policy="raw_permitted"))

    assert outcome.status == "content_filtered"
    assert outcome.raw_response_text is None


def test_the_adapter_never_overrides_the_callers_redaction_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sends the SAME underlying provider response under both policies and
    checks the adapter's OWN behaviour changes only as apply_redaction
    dictates -- never a hardcoded choice inside the adapter itself.
    """
    monkeypatch.setenv(GEMINI_API_KEY_ENV_VAR, _SECRET_KEY_VALUE)

    hashes_only_transport = _ScriptedTransport(response=_json_response(_COMPLETED_PAYLOAD))
    hashes_only_adapter = GeminiModelAdapter(
        transport=hashes_only_transport, model_name=_FAKE_MODEL_NAME
    )
    hashes_only_outcome = hashes_only_adapter.invoke(_request(redaction_policy="hashes_only"))

    raw_permitted_transport = _ScriptedTransport(response=_json_response(_COMPLETED_PAYLOAD))
    raw_permitted_adapter = GeminiModelAdapter(
        transport=raw_permitted_transport, model_name=_FAKE_MODEL_NAME
    )
    raw_permitted_outcome = raw_permitted_adapter.invoke(_request(redaction_policy="raw_permitted"))

    assert hashes_only_outcome.raw_response_text is None
    assert raw_permitted_outcome.raw_response_text == "Hello! Nice to meet you."
    # Both still complete and record the SAME response_hash -- redaction
    # controls raw-text retention only, never whether/how a response is hashed.
    assert hashes_only_outcome.response_hash == raw_permitted_outcome.response_hash


# ---------------------------------------------------------------------------
# Constructor validation and configuration.
# ---------------------------------------------------------------------------


def test_empty_model_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="model_name"):
        GeminiModelAdapter(transport=_FailIfCalledTransport(), model_name="")


def test_empty_api_base_url_is_rejected() -> None:
    with pytest.raises(ValueError, match="api_base_url"):
        GeminiModelAdapter(
            transport=_FailIfCalledTransport(), model_name=_FAKE_MODEL_NAME, api_base_url=""
        )


def test_default_api_base_url_is_the_real_gemini_endpoint() -> None:
    assert DEFAULT_API_BASE_URL == "https://generativelanguage.googleapis.com/v1beta"


def test_request_url_and_method_are_built_from_model_name_and_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(GEMINI_API_KEY_ENV_VAR, _SECRET_KEY_VALUE)
    transport = _ScriptedTransport(response=_json_response(_COMPLETED_PAYLOAD))
    adapter = GeminiModelAdapter(
        transport=transport,
        model_name=_FAKE_MODEL_NAME,
        api_base_url="https://example.invalid/v1beta",
    )

    adapter.invoke(_request())

    sent_request = transport.calls[0]
    assert (
        sent_request.url == "https://example.invalid/v1beta/models/gemini-2.0-flash:generateContent"
    )
    assert sent_request.method == "POST"
    assert sent_request.headers["Content-Type"] == "application/json"


# ---------------------------------------------------------------------------
# prompt_config_hash: deterministic, present unconditionally, sensitive to
# the actual prompt content.
# ---------------------------------------------------------------------------


def test_prompt_config_hash_is_deterministic_for_an_equal_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(GEMINI_API_KEY_ENV_VAR, _SECRET_KEY_VALUE)
    adapter_one = GeminiModelAdapter(
        transport=_ScriptedTransport(response=_json_response(_COMPLETED_PAYLOAD)),
        model_name=_FAKE_MODEL_NAME,
    )
    adapter_two = GeminiModelAdapter(
        transport=_ScriptedTransport(response=_json_response(_COMPLETED_PAYLOAD)),
        model_name=_FAKE_MODEL_NAME,
    )

    outcome_one = adapter_one.invoke(_request())
    outcome_two = adapter_two.invoke(_request())

    assert outcome_one.prompt_config_hash == outcome_two.prompt_config_hash


def test_prompt_config_hash_differs_for_a_different_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GEMINI_API_KEY_ENV_VAR, _SECRET_KEY_VALUE)
    adapter = GeminiModelAdapter(
        transport=_ScriptedTransport(response=_json_response(_COMPLETED_PAYLOAD)),
        model_name=_FAKE_MODEL_NAME,
    )
    other_adapter = GeminiModelAdapter(
        transport=_ScriptedTransport(response=_json_response(_COMPLETED_PAYLOAD)),
        model_name=_FAKE_MODEL_NAME,
    )

    outcome = adapter.invoke(_request(prompt_text="prompt A"))
    other_outcome = other_adapter.invoke(_request(prompt_text="prompt B"))

    assert outcome.prompt_config_hash != other_outcome.prompt_config_hash
