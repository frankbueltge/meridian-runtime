"""Property tests for mrr.adapters.llm.structured_generation.generate_structured
(task-packets/E4-T02.yaml acceptance test: "property - for arbitrary scripted
sequences of valid/invalid/non-completed outcomes and arbitrary
max_repair_attempts, the recorded-outcome count never exceeds 1 +
max_repair_attempts and a proposal is returned IFF some attempt produced
schema-valid output within the budget").

The scripted fake adapter here is driven by an explicit, hand-written model
of ``generate_structured``'s own halting rule (mirrored in
``_expected_outcome`` below): the loop executes at most
``max_repair_attempts + 1`` scripted entries in order, stopping immediately
at the first entry that is either schema-valid or a non-completed
``TerminalStatus`` -- entries after that point are never invoked. This
matches ``generate_structured``'s bounded-repair invariant (each
non-completed status is a distinct, immediate failure; only a completed-but-
invalid response consumes a repair attempt) rather than assuming every
scripted entry is always executed.
"""

from __future__ import annotations

import json

from hypothesis import given
from hypothesis import strategies as st
from mrr.adapters.llm.structured_generation import generate_structured
from mrr.domain.model_adapter import (
    ModelInvocationOutcome,
    ModelInvocationRequest,
    TokenUsage,
    apply_redaction,
)
from pydantic import BaseModel

_VALID_HASH = "sha256:" + "a" * 64
_VALID_PROFILE_URN = "urn:mrr:model-profile:01ARZ3NDEKTSV4RRFFQ69G5FAV"
_VALID_PAYLOAD = json.dumps({"claim_text": "the sky is blue", "confidence": 0.9})
_INVALID_PAYLOAD = "not valid JSON"

#: Every kind a scripted entry can take: schema-valid, completed-but-invalid,
#: or one of the four non-completed TerminalStatus values.
_KINDS = ("valid", "invalid", "refused", "content_filtered", "error", "timed_out")

#: Bounded small so example generation and shrinking stay fast -- the
#: property being tested does not depend on the budget's magnitude.
_MAX_REPAIR_ATTEMPTS = st.integers(min_value=0, max_value=4)


class _Proposal(BaseModel):
    claim_text: str
    confidence: float


class _ScriptedFakeAdapter:
    """Same fake shape as tests/unit/adapters/llm/test_structured_generation.py."""

    def __init__(self, script: list[ModelInvocationOutcome]) -> None:
        self._script = script
        self.calls: list[ModelInvocationRequest] = []

    def invoke(self, request: ModelInvocationRequest) -> ModelInvocationOutcome:
        self.calls.append(request)
        index = len(self.calls) - 1
        assert index < len(self._script), (
            "generate_structured invoked the adapter past the point its own "
            "halting rule should have stopped it"
        )
        return self._script[index]


def _outcome_for_kind(kind: str) -> ModelInvocationOutcome:
    if kind == "valid":
        response_hash, raw_response = apply_redaction("raw_permitted", _VALID_PAYLOAD)
        return ModelInvocationOutcome(
            status="completed",
            prompt_config_hash=_VALID_HASH,
            token_usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            redaction_policy="raw_permitted",
            response_hash=response_hash,
            raw_response_text=raw_response,
        )
    if kind == "invalid":
        response_hash, raw_response = apply_redaction("raw_permitted", _INVALID_PAYLOAD)
        return ModelInvocationOutcome(
            status="completed",
            prompt_config_hash=_VALID_HASH,
            token_usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            redaction_policy="raw_permitted",
            response_hash=response_hash,
            raw_response_text=raw_response,
        )
    # One of the four non-completed TerminalStatus values.
    return ModelInvocationOutcome(
        status=kind,  # type: ignore[arg-type]
        prompt_config_hash=_VALID_HASH,
        token_usage=TokenUsage(prompt_tokens=1, completion_tokens=0, total_tokens=1),
        redaction_policy="hashes_only",
        response_hash=None,
    )


def _expected_outcome(kinds: list[str]) -> tuple[int, str]:
    """Hand-written model of generate_structured's halting rule: returns
    ``(executed_count, expected_status)`` for a scripted ``kinds`` sequence.
    """
    for i, kind in enumerate(kinds):
        if kind == "valid":
            return i + 1, "proposal"
        if kind != "invalid":
            return i + 1, kind
    return len(kinds), "schema_invalid"


@st.composite
def _budget_and_script(draw: st.DrawFn) -> tuple[int, list[str]]:
    max_repair_attempts = draw(_MAX_REPAIR_ATTEMPTS)
    kinds = draw(
        st.lists(
            st.sampled_from(_KINDS),
            min_size=max_repair_attempts + 1,
            max_size=max_repair_attempts + 1,
        )
    )
    return max_repair_attempts, kinds


@given(_budget_and_script())
def test_recorded_outcome_count_never_exceeds_the_bound_and_proposal_iff_valid(
    budget_and_script: tuple[int, list[str]],
) -> None:
    max_repair_attempts, kinds = budget_and_script
    script = [_outcome_for_kind(kind) for kind in kinds]
    fake = _ScriptedFakeAdapter(script)
    request = ModelInvocationRequest(
        model_profile_id=_VALID_PROFILE_URN,
        model_profile_hash=_VALID_HASH,
        prompt_text="Extract the claim as JSON matching the schema.",
        operation_kind="stochastic",
        redaction_policy="raw_permitted",
    )

    result = generate_structured(fake, request, _Proposal, max_repair_attempts=max_repair_attempts)

    expected_count, expected_status = _expected_outcome(kinds)

    # Bounded repair: never more than 1 + max_repair_attempts recorded outcomes.
    assert len(result.attempts) <= 1 + max_repair_attempts
    assert len(result.attempts) == expected_count
    assert len(fake.calls) == expected_count

    # Proposal-iff-valid: a proposal is returned exactly when the halting
    # model says the executed prefix ended on a schema-valid attempt.
    assert (result.status == "proposal") == (expected_status == "proposal")
    assert result.status == expected_status
    if expected_status == "proposal":
        assert result.proposal is not None
    else:
        assert result.proposal is None
