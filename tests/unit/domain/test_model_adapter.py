"""Unit tests for mrr.domain.model_adapter (E4-T01): the ``TokenUsage``/
``ToolCallOutcome``/``ModelInvocationRequest``/``ModelInvocationOutcome``
value-object surface, the ``apply_redaction`` helper, and the ``ModelAdapter``
Protocol's structural conformance — including an in-test fake adapter that
produces a valid ``mrr.contracts.model_invocation.ModelInvocation`` end to
end, entirely without network (task-packets/E4-T01.yaml acceptance test:
"an in-test fake adapter implementing the port produces a valid
ModelInvocation without any network").

No concrete adapter is exercised or imported here (task-packets/E4-T01.yaml
forbidden_changes: "any concrete provider adapter ... and any adapters/llm/**
file"); ``_FakeModelAdapter`` below is a private, in-memory, deterministic
test double, matching this codebase's own "private module fake, not shared"
precedent (see e.g. mrr.contracts.source_family's own norm, and
tests/unit/domain/test_artifacts.py's ``_FakeArtifactStore``).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from mrr.contracts.model_invocation import ModelInvocation
from mrr.contracts.model_invocation import TokenUsage as ModelInvocationTokenUsage
from mrr.contracts.model_profile import ModelProfile, compute_config_hash
from mrr.domain.exceptions import InvalidContentHashError
from mrr.domain.identity import new_urn
from mrr.domain.model_adapter import (
    DEFAULT_REDACTION_POLICY,
    ModelAdapter,
    ModelInvocationOutcome,
    ModelInvocationRequest,
    TokenUsage,
    ToolCallOutcome,
    apply_redaction,
)

_VALID_HASH = "sha256:" + "a" * 64
_OTHER_VALID_HASH = "sha256:" + "b" * 64
_VALID_PROFILE_URN = "urn:mrr:model-profile:01ARZ3NDEKTSV4RRFFQ69G5FAV"
_PRACTICE_ID = "urn:mrr:practice:01ARZ3NDEKTSV4RRFFQ69G5FA0"
_AGENT_ID = "urn:mrr:agent:01ARZ3NDEKTSV4RRFFQ69G5FA1"

# ---------------------------------------------------------------------------
# TokenUsage
# ---------------------------------------------------------------------------


def test_token_usage_accepts_nonnegative_counts() -> None:
    usage = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    assert usage.total_tokens == 15


@pytest.mark.parametrize("field_name", ["prompt_tokens", "completion_tokens", "total_tokens"])
def test_token_usage_rejects_a_negative_count(field_name: str) -> None:
    kwargs = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
    kwargs[field_name] = -1
    with pytest.raises(ValueError, match=field_name):
        TokenUsage(**kwargs)


# ---------------------------------------------------------------------------
# ToolCallOutcome — name/hash validation and the result_hash <-> status biconditional.
# ---------------------------------------------------------------------------


def test_tool_call_outcome_completed_requires_a_result_hash() -> None:
    with pytest.raises(ValueError, match="result_hash"):
        ToolCallOutcome(
            name="web_search", arguments_hash=_VALID_HASH, result_hash=None, status="completed"
        )


@pytest.mark.parametrize("status", ["refused", "error", "timed_out"])
def test_tool_call_outcome_non_completed_forbids_a_result_hash(status: str) -> None:
    with pytest.raises(ValueError, match="result_hash"):
        ToolCallOutcome(
            name="web_search",
            arguments_hash=_VALID_HASH,
            result_hash=_OTHER_VALID_HASH,
            status=status,  # type: ignore[arg-type]
        )


def test_tool_call_outcome_completed_with_result_hash_succeeds() -> None:
    call = ToolCallOutcome(
        name="web_search",
        arguments_hash=_VALID_HASH,
        result_hash=_OTHER_VALID_HASH,
        status="completed",
    )
    assert call.status == "completed"


def test_tool_call_outcome_rejects_empty_name() -> None:
    with pytest.raises(ValueError, match="name"):
        ToolCallOutcome(name="", arguments_hash=_VALID_HASH, result_hash=None, status="error")


def test_tool_call_outcome_rejects_malformed_arguments_hash() -> None:
    with pytest.raises(InvalidContentHashError):
        ToolCallOutcome(
            name="web_search", arguments_hash="not-a-hash", result_hash=None, status="error"
        )


# ---------------------------------------------------------------------------
# ModelInvocationRequest
# ---------------------------------------------------------------------------


def _request(**overrides: object) -> ModelInvocationRequest:
    defaults: dict[str, object] = {
        "model_profile_id": _VALID_PROFILE_URN,
        "model_profile_hash": _VALID_HASH,
        "prompt_text": "What is the capital of France?",
        "operation_kind": "deterministic",
        "redaction_policy": DEFAULT_REDACTION_POLICY,
    }
    defaults.update(overrides)
    return ModelInvocationRequest(**defaults)  # type: ignore[arg-type]


def test_model_invocation_request_accepts_valid_fields() -> None:
    request = _request()
    assert request.operation_kind == "deterministic"


def test_model_invocation_request_rejects_a_malformed_profile_urn() -> None:
    with pytest.raises(ValueError, match="urn"):
        _request(model_profile_id="not-a-urn")


def test_model_invocation_request_rejects_a_malformed_profile_hash() -> None:
    with pytest.raises(InvalidContentHashError):
        _request(model_profile_hash="not-a-hash")


def test_model_invocation_request_rejects_empty_prompt_text() -> None:
    with pytest.raises(ValueError, match="prompt_text"):
        _request(prompt_text="")


# ---------------------------------------------------------------------------
# ModelInvocationOutcome — the response_hash <-> status biconditional, and
# the redaction-default enforcement (no raw text under hashes_only).
# ---------------------------------------------------------------------------


def _token_usage() -> TokenUsage:
    return TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)


def test_outcome_completed_requires_a_response_hash() -> None:
    with pytest.raises(ValueError, match="response_hash"):
        ModelInvocationOutcome(
            status="completed",
            prompt_config_hash=_VALID_HASH,
            token_usage=_token_usage(),
            redaction_policy="hashes_only",
            response_hash=None,
        )


@pytest.mark.parametrize("status", ["refused", "content_filtered", "error", "timed_out"])
def test_outcome_non_completed_forbids_a_response_hash(status: str) -> None:
    with pytest.raises(ValueError, match="response_hash"):
        ModelInvocationOutcome(
            status=status,  # type: ignore[arg-type]
            prompt_config_hash=_VALID_HASH,
            token_usage=_token_usage(),
            redaction_policy="hashes_only",
            response_hash=_OTHER_VALID_HASH,
        )


@pytest.mark.parametrize("status", ["refused", "content_filtered", "error", "timed_out"])
def test_outcome_non_completed_with_no_response_hash_succeeds(status: str) -> None:
    outcome = ModelInvocationOutcome(
        status=status,  # type: ignore[arg-type]
        prompt_config_hash=_VALID_HASH,
        token_usage=_token_usage(),
        redaction_policy="hashes_only",
        response_hash=None,
    )
    assert outcome.status == status
    assert outcome.response_hash is None


def test_outcome_under_hashes_only_forbids_raw_prompt_text() -> None:
    with pytest.raises(ValueError, match="hashes_only"):
        ModelInvocationOutcome(
            status="completed",
            prompt_config_hash=_VALID_HASH,
            token_usage=_token_usage(),
            redaction_policy="hashes_only",
            response_hash=_OTHER_VALID_HASH,
            raw_prompt_text="the actual prompt",
        )


def test_outcome_under_hashes_only_forbids_raw_response_text() -> None:
    with pytest.raises(ValueError, match="hashes_only"):
        ModelInvocationOutcome(
            status="completed",
            prompt_config_hash=_VALID_HASH,
            token_usage=_token_usage(),
            redaction_policy="hashes_only",
            response_hash=_OTHER_VALID_HASH,
            raw_response_text="the actual response",
        )


def test_outcome_under_raw_permitted_allows_raw_text() -> None:
    outcome = ModelInvocationOutcome(
        status="completed",
        prompt_config_hash=_VALID_HASH,
        token_usage=_token_usage(),
        redaction_policy="raw_permitted",
        response_hash=_OTHER_VALID_HASH,
        raw_prompt_text="the actual prompt",
        raw_response_text="the actual response",
    )
    assert outcome.raw_prompt_text == "the actual prompt"


# ---------------------------------------------------------------------------
# apply_redaction — the redaction default helper.
# ---------------------------------------------------------------------------


def test_apply_redaction_hashes_only_never_returns_raw_text() -> None:
    hashed, raw = apply_redaction("hashes_only", "a secret-looking prompt")
    assert raw is None
    assert hashed.startswith("sha256:")


def test_apply_redaction_raw_permitted_returns_both() -> None:
    hashed, raw = apply_redaction("raw_permitted", "a permitted prompt")
    assert raw == "a permitted prompt"
    assert hashed.startswith("sha256:")


def test_apply_redaction_hash_is_stable_and_content_derived() -> None:
    hashed_a, _ = apply_redaction("hashes_only", "identical text")
    hashed_b, _ = apply_redaction("raw_permitted", "identical text")
    assert hashed_a == hashed_b

    hashed_c, _ = apply_redaction("hashes_only", "different text")
    assert hashed_a != hashed_c


# ---------------------------------------------------------------------------
# ModelAdapter Protocol — structural conformance.
# ---------------------------------------------------------------------------


class _FakeModelAdapter:
    """An in-memory, deterministic fake — no network of any kind."""

    def invoke(self, request: ModelInvocationRequest) -> ModelInvocationOutcome:
        prompt_hash, raw_prompt = apply_redaction(request.redaction_policy, request.prompt_text)
        response_text = f"fake deterministic response to: {request.prompt_text}"
        response_hash, raw_response = apply_redaction(request.redaction_policy, response_text)
        return ModelInvocationOutcome(
            status="completed",
            prompt_config_hash=prompt_hash,
            token_usage=TokenUsage(prompt_tokens=12, completion_tokens=6, total_tokens=18),
            redaction_policy=request.redaction_policy,
            response_hash=response_hash,
            raw_prompt_text=raw_prompt,
            raw_response_text=raw_response,
        )


class _IncompleteModelAdapter:
    """Missing ``invoke`` entirely — must not satisfy the Protocol."""


def test_model_adapter_protocol_accepts_a_conforming_fake() -> None:
    assert isinstance(_FakeModelAdapter(), ModelAdapter)


def test_model_adapter_protocol_rejects_an_incomplete_implementation() -> None:
    assert not isinstance(_IncompleteModelAdapter(), ModelAdapter)


def _sample_profile() -> ModelProfile:
    decoding_parameters = {"temperature": 0, "max_output_tokens": 2048}
    config_hash = compute_config_hash(
        provider="anthropic",
        model_family="claude-3",
        model_identifier="claude-3-5-sonnet-20241022",
        decoding_parameters=decoding_parameters,
        determinism="deterministic",
        seed=7,
        prompt_family="test-fixture-v1",
        tool_permissions=[],
    )
    return ModelProfile(
        id=new_urn("model-profile"),
        api_version="mrr/v1alpha1",
        kind="ModelProfile",
        practice_id=_PRACTICE_ID,
        revision=1,
        created_at=datetime.now(UTC),
        created_by=_AGENT_ID,
        content_hash=_VALID_HASH,
        provider="anthropic",
        model_family="claude-3",
        model_identifier="claude-3-5-sonnet-20241022",
        decoding_parameters=decoding_parameters,
        determinism="deterministic",
        seed=7,
        prompt_family="test-fixture-v1",
        tool_permissions=[],
        config_hash=config_hash,
    )


def test_fake_adapter_produces_a_valid_model_invocation_without_network() -> None:
    """The full pipeline — request built against a real ModelProfile, a fake
    adapter's outcome, and a persisted ModelInvocation built from that
    outcome — works end to end with no network call anywhere in it.
    """
    profile = _sample_profile()
    request = ModelInvocationRequest(
        model_profile_id=profile.id,
        model_profile_hash=profile.content_hash,
        prompt_text="What is the capital of France?",
        operation_kind="deterministic",
        redaction_policy=DEFAULT_REDACTION_POLICY,
    )

    outcome = _FakeModelAdapter().invoke(request)

    invocation = ModelInvocation(
        id=new_urn("model-invocation"),
        api_version="mrr/v1alpha1",
        kind="ModelInvocation",
        practice_id=_PRACTICE_ID,
        revision=1,
        created_at=datetime.now(UTC),
        created_by=_AGENT_ID,
        content_hash=_VALID_HASH,
        model_profile_id=request.model_profile_id,
        model_profile_hash=request.model_profile_hash,
        operation_kind=request.operation_kind,
        prompt_config_hash=outcome.prompt_config_hash,
        token_usage=ModelInvocationTokenUsage(
            prompt_tokens=outcome.token_usage.prompt_tokens,
            completion_tokens=outcome.token_usage.completion_tokens,
            total_tokens=outcome.token_usage.total_tokens,
        ),
        response_hash=outcome.response_hash,
        status=outcome.status,
        redaction_policy=outcome.redaction_policy,
    )

    assert invocation.status == "completed"
    assert invocation.response_hash == outcome.response_hash
    # Default redaction policy: no raw text anywhere on the persisted record.
    assert invocation.raw_prompt_text is None
    assert invocation.raw_response_text is None
    # Proposal-only shape: nothing on this object could express acceptance.
    assert "claim_status" not in ModelInvocation.model_fields
    assert "verification_verdict" not in ModelInvocation.model_fields
    assert "accepted" not in ModelInvocation.model_fields
