"""Unit tests for mrr.adapters.llm.structured_generation (task-packets/
E4-T02.yaml): the bounded schema-repair loop over an injected
mrr.domain.model_adapter.ModelAdapter, driven entirely by an in-test
scripted fake adapter -- never a real provider, never a network call
(task-packets/E4-T02.yaml: "Tests drive it with an in-test scripted fake
adapter - never a real provider, never a network call").

Covers every packet acceptance scenario: valid-first, repair-then-succeed,
limit-exhausted, zero-budget, underlying-non-completed (each of the four
non-completed TerminalStatus values, surfaced verbatim and never relabeled),
and the redaction default (hashes_only never lets raw text leak into the
returned audit trail). The property test bounding the recorded-outcome count
and the proposal-iff-valid guarantee lives in
tests/property/test_structured_generation_properties.py.
"""

from __future__ import annotations

import json

import pytest
from mrr.adapters.llm.structured_generation import (
    StructuredGenerationResult,
    generate_structured,
)
from mrr.domain.model_adapter import (
    DEFAULT_REDACTION_POLICY,
    ModelInvocationOutcome,
    ModelInvocationRequest,
    RedactionPolicy,
    TerminalStatus,
    TokenUsage,
    apply_redaction,
)
from pydantic import BaseModel

_VALID_HASH = "sha256:" + "a" * 64
_VALID_PROFILE_URN = "urn:mrr:model-profile:01ARZ3NDEKTSV4RRFFQ69G5FAV"


class _Proposal(BaseModel):
    """A minimal target schema for tests -- two required fields, so a
    missing or malformed candidate always fails Pydantic validation.
    """

    claim_text: str
    confidence: float


_VALID_PAYLOAD = json.dumps({"claim_text": "the sky is blue", "confidence": 0.9})
_INVALID_PAYLOAD = "this is not valid JSON at all"


# ---------------------------------------------------------------------------
# Test fixtures: a scripted fake ModelAdapter and outcome builders.
# ---------------------------------------------------------------------------


class _ScriptedFakeAdapter:
    """An in-memory, deterministic fake ``ModelAdapter`` -- no network of any
    kind. Returns one pre-built ``ModelInvocationOutcome`` per call, in the
    order supplied at construction, and records every request it was called
    with for post-hoc inspection.
    """

    def __init__(self, script: list[ModelInvocationOutcome]) -> None:
        self._script = script
        self.calls: list[ModelInvocationRequest] = []

    def invoke(self, request: ModelInvocationRequest) -> ModelInvocationOutcome:
        self.calls.append(request)
        index = len(self.calls) - 1
        assert index < len(self._script), (
            f"scripted fake adapter called {index + 1} times but only "
            f"{len(self._script)} outcomes were scripted"
        )
        return self._script[index]


def _completed_outcome(
    response_text: str, *, redaction_policy: RedactionPolicy = "raw_permitted"
) -> ModelInvocationOutcome:
    response_hash, raw_response = apply_redaction(redaction_policy, response_text)
    return ModelInvocationOutcome(
        status="completed",
        prompt_config_hash=_VALID_HASH,
        token_usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        redaction_policy=redaction_policy,
        response_hash=response_hash,
        raw_response_text=raw_response,
    )


def _non_completed_outcome(status: TerminalStatus) -> ModelInvocationOutcome:
    assert status != "completed"
    return ModelInvocationOutcome(
        status=status,
        prompt_config_hash=_VALID_HASH,
        token_usage=TokenUsage(prompt_tokens=10, completion_tokens=0, total_tokens=10),
        redaction_policy="hashes_only",
        response_hash=None,
    )


def _request(**overrides: object) -> ModelInvocationRequest:
    defaults: dict[str, object] = {
        "model_profile_id": _VALID_PROFILE_URN,
        "model_profile_hash": _VALID_HASH,
        "prompt_text": "Extract the claim as JSON matching the schema.",
        "operation_kind": "stochastic",
        "redaction_policy": "raw_permitted",
    }
    defaults.update(overrides)
    return ModelInvocationRequest(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# valid-first
# ---------------------------------------------------------------------------


def test_valid_first_returns_the_proposal_with_one_recorded_outcome_and_zero_repairs() -> None:
    fake = _ScriptedFakeAdapter([_completed_outcome(_VALID_PAYLOAD)])

    result = generate_structured(fake, _request(), _Proposal, max_repair_attempts=3)

    assert result.status == "proposal"
    assert result.proposal == _Proposal(claim_text="the sky is blue", confidence=0.9)
    assert len(result.attempts) == 1
    assert result.repair_attempts_used == 0
    assert result.validation_errors == ()
    assert len(fake.calls) == 1


# ---------------------------------------------------------------------------
# repair-then-succeed
# ---------------------------------------------------------------------------


def test_repair_then_succeed_returns_the_proposal_after_one_repair() -> None:
    fake = _ScriptedFakeAdapter(
        [_completed_outcome(_INVALID_PAYLOAD), _completed_outcome(_VALID_PAYLOAD)]
    )

    result = generate_structured(fake, _request(), _Proposal, max_repair_attempts=3)

    assert result.status == "proposal"
    assert result.proposal == _Proposal(claim_text="the sky is blue", confidence=0.9)
    assert result.repair_attempts_used == 1
    # Audit trail order: the invalid attempt, then the successful one.
    assert len(result.attempts) == 2
    assert result.attempts[0].status == "completed"
    assert result.attempts[1].status == "completed"
    assert len(fake.calls) == 2

    # The repair call's prompt restates the original task and includes the
    # validation error and prior response -- never a bare retry of the same
    # prompt text.
    first_prompt = fake.calls[0].prompt_text
    second_prompt = fake.calls[1].prompt_text
    assert second_prompt != first_prompt
    assert first_prompt in second_prompt
    assert _INVALID_PAYLOAD in second_prompt

    # Everything except prompt_text is identical across calls.
    assert fake.calls[0].model_profile_id == fake.calls[1].model_profile_id
    assert fake.calls[0].model_profile_hash == fake.calls[1].model_profile_hash
    assert fake.calls[0].operation_kind == fake.calls[1].operation_kind
    assert fake.calls[0].redaction_policy == fake.calls[1].redaction_policy
    assert fake.calls[0].tool_names_available == fake.calls[1].tool_names_available


# ---------------------------------------------------------------------------
# limit-exhausted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("max_repair_attempts", [0, 1, 3])
def test_limit_exhausted_returns_an_explicit_schema_invalid_failure(
    max_repair_attempts: int,
) -> None:
    script = [_completed_outcome(_INVALID_PAYLOAD) for _ in range(max_repair_attempts + 1)]
    fake = _ScriptedFakeAdapter(script)

    result = generate_structured(
        fake, _request(), _Proposal, max_repair_attempts=max_repair_attempts
    )

    assert result.status == "schema_invalid"
    assert result.proposal is None
    assert len(result.attempts) == max_repair_attempts + 1
    assert result.repair_attempts_used == max_repair_attempts
    assert len(result.validation_errors) == max_repair_attempts + 1
    assert len(fake.calls) == max_repair_attempts + 1


# ---------------------------------------------------------------------------
# zero-budget
# ---------------------------------------------------------------------------


def test_zero_budget_fails_explicitly_with_exactly_one_call_and_no_repair() -> None:
    fake = _ScriptedFakeAdapter([_completed_outcome(_INVALID_PAYLOAD)])

    result = generate_structured(fake, _request(), _Proposal, max_repair_attempts=0)

    assert result.status == "schema_invalid"
    assert result.proposal is None
    assert len(result.attempts) == 1
    assert result.repair_attempts_used == 0
    assert len(fake.calls) == 1


# ---------------------------------------------------------------------------
# underlying-non-completed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["refused", "content_filtered", "error", "timed_out"])
def test_underlying_non_completed_is_surfaced_verbatim_never_relabeled(status: str) -> None:
    fake = _ScriptedFakeAdapter([_non_completed_outcome(status)])  # type: ignore[arg-type]

    result = generate_structured(fake, _request(), _Proposal, max_repair_attempts=5)

    assert result.status == status
    assert result.status != "schema_invalid"
    assert result.proposal is None
    assert len(result.attempts) == 1
    assert result.repair_attempts_used == 0
    # No repair attempt was made even though budget remained -- a
    # non-completed outcome halts the loop immediately.
    assert len(fake.calls) == 1


# ---------------------------------------------------------------------------
# redaction default
# ---------------------------------------------------------------------------


def test_redaction_default_never_lets_raw_text_into_the_recorded_outcome() -> None:
    fake = _ScriptedFakeAdapter(
        [_completed_outcome(_VALID_PAYLOAD, redaction_policy=DEFAULT_REDACTION_POLICY)]
    )
    request = _request(redaction_policy=DEFAULT_REDACTION_POLICY)
    assert DEFAULT_REDACTION_POLICY == "hashes_only"

    result = generate_structured(fake, request, _Proposal, max_repair_attempts=0)

    # Under hashes_only this layer has no observable candidate text and
    # therefore never infers validity from a hash alone.
    assert result.status == "schema_invalid"
    assert result.proposal is None
    assert len(result.attempts) == 1
    recorded = result.attempts[0]
    assert recorded.redaction_policy == "hashes_only"
    assert recorded.raw_prompt_text is None
    assert recorded.raw_response_text is None


# ---------------------------------------------------------------------------
# max_repair_attempts input validation
# ---------------------------------------------------------------------------


def test_negative_max_repair_attempts_is_rejected() -> None:
    fake = _ScriptedFakeAdapter([_completed_outcome(_VALID_PAYLOAD)])

    with pytest.raises(ValueError, match="max_repair_attempts"):
        generate_structured(fake, _request(), _Proposal, max_repair_attempts=-1)

    # Rejected before any call is made.
    assert fake.calls == []


# ---------------------------------------------------------------------------
# StructuredGenerationResult's own structural invariants.
# ---------------------------------------------------------------------------


def test_result_rejects_proposal_status_without_a_proposal() -> None:
    with pytest.raises(ValueError, match="proposal"):
        StructuredGenerationResult(
            status="proposal",
            proposal=None,
            attempts=(_completed_outcome(_VALID_PAYLOAD),),
            repair_attempts_used=0,
        )


def test_result_rejects_a_failure_status_carrying_a_proposal() -> None:
    with pytest.raises(ValueError, match="schema_invalid"):
        StructuredGenerationResult(
            status="schema_invalid",
            proposal=_Proposal(claim_text="x", confidence=0.1),
            attempts=(_completed_outcome(_INVALID_PAYLOAD),),
            repair_attempts_used=0,
        )


def test_result_rejects_an_attempts_count_mismatch() -> None:
    with pytest.raises(ValueError, match="repair_attempts_used"):
        StructuredGenerationResult(
            status="schema_invalid",
            proposal=None,
            attempts=(_completed_outcome(_INVALID_PAYLOAD),),
            repair_attempts_used=1,
        )


def test_result_rejects_empty_attempts() -> None:
    with pytest.raises(ValueError, match="attempts"):
        StructuredGenerationResult(
            status="schema_invalid",
            proposal=None,
            attempts=(),
            repair_attempts_used=0,
        )
