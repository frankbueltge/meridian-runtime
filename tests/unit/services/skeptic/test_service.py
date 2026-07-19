"""Unit tests for mrr.services.skeptic.service (task-packets/E4-T04.yaml):
the skeptic role, driven entirely by an in-test scripted fake ModelAdapter --
never a real provider, never a network call (task-packets/E4-T04.yaml:
"Tests drive the skeptic with an in-test scripted fake adapter ... never a
real provider, never a network call").

Covers every packet acceptance scenario: challenges-across-types with audit
trail, type-coverage waivers with no dropped challenge, an entirely empty
review as a valid coverage outcome, no-invalid-passes on schema-invalid
model output, determinism given a fixed clock/id sequence, underlying
non-completed statuses surfaced verbatim, the structural
not-a-verdict guarantee, and that every produced challenge pins the
CALLER-supplied target claim and producing ModelProfile references (never
anything the model itself proposed).
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from mrr.contracts.common import Scope
from mrr.contracts.skeptical_challenge import CHALLENGE_TYPES
from mrr.domain.model_adapter import (
    ModelInvocationOutcome,
    ModelInvocationRequest,
    TerminalStatus,
    TokenUsage,
    apply_redaction,
)
from mrr.services.skeptic.service import (
    SkepticalChallengeResult,
    propose_skeptical_challenges,
)

_VALID_HASH = "sha256:" + "a" * 64
_VALID_PROFILE_URN = "urn:mrr:model-profile:01ARZ3NDEKTSV4RRFFQ69G5FAV"
_PRACTICE_ID = "urn:mrr:practice:01ARZ3NDEKTSV4RRFFQ69G5FAV"
_CREATED_BY = "urn:mrr:agent:01ARZ3NDEKTSV4RRFFQ69G5FAV"
_TARGET_CLAIM_ID = "urn:mrr:claim:01ARZ3NDEKTSV4RRFFQ69G5FAV"
_TARGET_CLAIM_HASH = "sha256:" + "b" * 64
_TARGET_CLAIM_ASSERTION = (
    "The recomputed value is 42 percent for the supplied numerator and denominator."
)
_TARGET_CLAIM_SCOPE = Scope(population="Benchmark fixture", conditions=["Numerator 42"])
_FIXED_CLOCK_INSTANT = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Test fixtures: a scripted fake ModelAdapter, challenge/review payload
# builders, and a deterministic id/clock pair for the determinism test.
# ---------------------------------------------------------------------------


class _ScriptedFakeAdapter:
    """Same in-memory, deterministic fake ``ModelAdapter`` shape as
    tests/unit/services/planner/test_service.py and
    tests/unit/adapters/llm/test_structured_generation.py -- no network of
    any kind.
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


def _completed_outcome(response_text: str) -> ModelInvocationOutcome:
    response_hash, raw_response = apply_redaction("raw_permitted", response_text)
    return ModelInvocationOutcome(
        status="completed",
        prompt_config_hash=_VALID_HASH,
        token_usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        redaction_policy="raw_permitted",
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


def _challenge(challenge_type: str, **overrides: object) -> dict[str, object]:
    """One well-formed challenge payload for ``challenge_type``, with
    sensible defaults for every field the target schema accepts --
    overridable per test.
    """
    defaults: dict[str, object] = {
        "challenge_type": challenge_type,
        "statement": f"Statement for the {challenge_type} challenge.",
        "rationale": f"Rationale for the {challenge_type} challenge.",
    }
    defaults.update(overrides)
    return defaults


def _review_payload(challenges: list[dict[str, object]]) -> str:
    return json.dumps({"challenges": challenges})


def _sequential_id_factory() -> Callable[[], str]:
    """A fresh, deterministic id factory: a NEW counter starting at 1 each
    time this is called, so two SEPARATE ``propose_skeptical_challenges``
    calls each built with their own ``_sequential_id_factory()`` mint the
    exact same sequence of ids -- the determinism test's own
    dependency-injected replacement for ``mrr.domain.identity.new_urn``'s
    randomness.
    """
    counter = itertools.count(1)

    def factory() -> str:
        return f"urn:mrr:skeptical-challenge:{next(counter):026d}"

    return factory


def _fixed_clock() -> datetime:
    return _FIXED_CLOCK_INSTANT


def _propose(
    adapter: _ScriptedFakeAdapter,
    *,
    max_repair_attempts: int = 0,
    id_factory: Callable[[], str] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> SkepticalChallengeResult:
    return propose_skeptical_challenges(
        adapter,
        target_claim_id=_TARGET_CLAIM_ID,
        target_claim_hash=_TARGET_CLAIM_HASH,
        target_claim_assertion=_TARGET_CLAIM_ASSERTION,
        target_claim_scope=_TARGET_CLAIM_SCOPE,
        model_profile_id=_VALID_PROFILE_URN,
        model_profile_hash=_VALID_HASH,
        operation_kind="stochastic",
        redaction_policy="raw_permitted",
        max_repair_attempts=max_repair_attempts,
        practice_id=_PRACTICE_ID,
        created_by=_CREATED_BY,
        id_factory=id_factory or _sequential_id_factory(),
        clock=clock or _fixed_clock,
    )


# ---------------------------------------------------------------------------
# challenges-across-types, audit trail, structural not-a-verdict
# ---------------------------------------------------------------------------


def test_produces_challenges_across_types_with_audit_trail() -> None:
    challenges = [_challenge(challenge_type) for challenge_type in CHALLENGE_TYPES]
    fake = _ScriptedFakeAdapter([_completed_outcome(_review_payload(challenges))])

    result = _propose(fake)

    assert result.status == "proposal"
    assert len(result.challenges) == len(CHALLENGE_TYPES)
    assert {c.challenge_type for c in result.challenges} == set(CHALLENGE_TYPES)
    assert result.type_waivers == ()
    # Every produced SkepticalChallenge is proposal-only, never a verdict.
    for challenge in result.challenges:
        assert not hasattr(challenge, "verdict")
        assert not hasattr(challenge, "decision")
        assert not hasattr(challenge, "verified")
        assert not hasattr(challenge, "resolved")
        assert not hasattr(challenge, "claim_status")
    # The audit trail records exactly the one underlying model call.
    assert len(result.attempts) == 1
    assert result.attempts[0].status == "completed"
    assert len(fake.calls) == 1
    assert _TARGET_CLAIM_ASSERTION in fake.calls[0].prompt_text


def test_produced_challenges_pin_the_caller_supplied_target_and_producing_profile() -> None:
    """The target claim and producing ModelProfile references on every
    produced challenge come from the CALLER's own inputs -- never anything
    the model itself proposed (the model's own target model has no field
    for either; see the service module's docstring).
    """
    challenges = [_challenge("counterevidence")]
    fake = _ScriptedFakeAdapter([_completed_outcome(_review_payload(challenges))])

    result = _propose(fake)

    assert len(result.challenges) == 1
    produced = result.challenges[0]
    assert produced.target_claim_id == _TARGET_CLAIM_ID
    assert produced.target_claim_hash == _TARGET_CLAIM_HASH
    assert produced.producing_model_profile_id == _VALID_PROFILE_URN
    assert produced.producing_model_profile_hash == _VALID_HASH


def test_supporting_source_ids_pass_through_from_the_model() -> None:
    source_id = "urn:mrr:source-record:01ARZ3NDEKTSV4RRFFQ69G5FAV"
    challenges = [_challenge("counterevidence", supporting_source_ids=[source_id])]
    fake = _ScriptedFakeAdapter([_completed_outcome(_review_payload(challenges))])

    result = _propose(fake)

    assert result.challenges[0].supporting_source_ids == [source_id]


# ---------------------------------------------------------------------------
# coverage: waivers recorded, no produced challenge dropped
# ---------------------------------------------------------------------------


def test_missing_types_get_an_explicit_waiver_and_no_challenge_is_dropped() -> None:
    produced_types = ["counterevidence", "counterevidence", "scope_leakage"]
    challenges = [_challenge(challenge_type) for challenge_type in produced_types]
    fake = _ScriptedFakeAdapter([_completed_outcome(_review_payload(challenges))])

    result = _propose(fake)

    assert result.status == "proposal"
    # Every produced challenge is preserved -- including the two under the
    # same type -- never deduplicated or dropped.
    assert len(result.challenges) == 3
    assert [c.challenge_type for c in result.challenges] == produced_types

    missing_types = set(CHALLENGE_TYPES) - {"counterevidence", "scope_leakage"}
    waived_types = {waiver.challenge_type for waiver in result.type_waivers}
    assert waived_types == missing_types
    for waiver in result.type_waivers:
        assert waiver.challenge_type in waiver.reason
        assert "searched, none found" in waiver.reason


def test_full_type_coverage_yields_no_waivers() -> None:
    challenges = [_challenge(challenge_type) for challenge_type in CHALLENGE_TYPES]
    fake = _ScriptedFakeAdapter([_completed_outcome(_review_payload(challenges))])

    result = _propose(fake)

    assert result.type_waivers == ()


def test_empty_review_is_a_valid_proposal_with_all_types_waived() -> None:
    """A skeptic that genuinely finds nothing across all four types is a
    valid, recorded outcome (task-packets/E4-T04.yaml: "a skeptic that
    searched and found no counterevidence is a valid, recorded outcome") --
    NOT a failure, and not schema_invalid: an empty ``{"challenges": []}``
    document is itself schema-valid.
    """
    fake = _ScriptedFakeAdapter([_completed_outcome(_review_payload([]))])

    result = _propose(fake)

    assert result.status == "proposal"
    assert result.challenges == ()
    assert {waiver.challenge_type for waiver in result.type_waivers} == set(CHALLENGE_TYPES)


# ---------------------------------------------------------------------------
# no-invalid-passes: schema-invalid model output never becomes a proposal
# ---------------------------------------------------------------------------


def test_schema_invalid_output_is_not_emitted_as_a_proposal() -> None:
    fake = _ScriptedFakeAdapter([_completed_outcome("this is not valid JSON at all")])

    result = _propose(fake, max_repair_attempts=0)

    assert result.status == "schema_invalid"
    assert result.challenges == ()
    assert result.type_waivers == ()
    assert len(result.validation_errors) == 1
    assert len(result.attempts) == 1


def test_one_invalid_challenge_fails_the_whole_review_not_just_that_challenge() -> None:
    """Whole-review atomicity (see mrr.services.skeptic.service's module
    docstring): a challenge with an empty statement fails the ENTIRE
    structured-generation attempt, not just that one challenge.
    """
    challenges = [
        _challenge("counterevidence"),
        _challenge("scope_leakage", statement=""),
    ]
    fake = _ScriptedFakeAdapter([_completed_outcome(_review_payload(challenges))])

    result = _propose(fake, max_repair_attempts=0)

    assert result.status == "schema_invalid"
    assert result.challenges == ()


def test_repair_then_succeed_records_both_attempts_in_the_audit_trail() -> None:
    valid_challenges = [_challenge("counterevidence")]
    fake = _ScriptedFakeAdapter(
        [
            _completed_outcome("not valid JSON"),
            _completed_outcome(_review_payload(valid_challenges)),
        ]
    )

    result = _propose(fake, max_repair_attempts=1)

    assert result.status == "proposal"
    assert len(result.challenges) == 1
    assert len(result.attempts) == 2
    assert len(fake.calls) == 2
    # The repair prompt restates the original claim assertion plus the failure.
    assert _TARGET_CLAIM_ASSERTION in fake.calls[1].prompt_text


@pytest.mark.parametrize("status", ["refused", "content_filtered", "error", "timed_out"])
def test_underlying_non_completed_status_is_surfaced_verbatim(status: str) -> None:
    fake = _ScriptedFakeAdapter([_non_completed_outcome(status)])  # type: ignore[arg-type]

    result = _propose(fake, max_repair_attempts=5)

    assert result.status == status
    assert result.challenges == ()
    assert result.type_waivers == ()
    assert len(result.attempts) == 1
    # No repair attempt is made -- a non-completed outcome halts immediately.
    assert len(fake.calls) == 1


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


def test_same_scripted_adapter_and_inputs_yield_an_identical_result() -> None:
    challenges = [_challenge(challenge_type) for challenge_type in CHALLENGE_TYPES]

    fake_1 = _ScriptedFakeAdapter([_completed_outcome(_review_payload(challenges))])
    result_1 = _propose(fake_1, id_factory=_sequential_id_factory(), clock=_fixed_clock)

    fake_2 = _ScriptedFakeAdapter([_completed_outcome(_review_payload(challenges))])
    result_2 = _propose(fake_2, id_factory=_sequential_id_factory(), clock=_fixed_clock)

    assert result_1.status == result_2.status == "proposal"
    assert result_1.challenges == result_2.challenges
    assert result_1.type_waivers == result_2.type_waivers
    assert len(result_1.attempts) == len(result_2.attempts)


# ---------------------------------------------------------------------------
# provider neutrality -- no provider SDK import anywhere in this module
# ---------------------------------------------------------------------------


def test_skeptic_module_imports_no_provider_sdk_or_network_library() -> None:
    import ast
    from pathlib import Path

    module_path = (
        Path(__file__).resolve().parents[4]
        / "services"
        / "control_plane"
        / "mrr"
        / "services"
        / "skeptic"
        / "service.py"
    )
    tree = ast.parse(module_path.read_text())
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_roots.add(node.module.split(".")[0])

    forbidden = {
        "openai",
        "anthropic",
        "boto3",
        "botocore",
        "requests",
        "httpx",
        "urllib3",
        "fastapi",
        "starlette",
        "temporalio",
    }
    assert imported_roots.isdisjoint(forbidden)
