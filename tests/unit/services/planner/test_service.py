"""Unit tests for mrr.services.planner.service (task-packets/E4-T03.yaml):
the planner/proposer role, driven entirely by an in-test scripted fake
ModelAdapter -- never a real provider, never a network call (task-packets/
E4-T03.yaml: "Tests drive the planner with an in-test scripted fake adapter
... never a real provider, never a network call").

Covers every packet acceptance scenario: forest-across-roles with audit
trail, role-coverage waivers with no dropped branch, no-invalid-passes on
schema-invalid model output, determinism given a fixed clock/id sequence,
underlying non-completed statuses surfaced verbatim, and the structural
not-a-claim-of-result guarantee (status is always "proposed", never
settable by the model).
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from mrr.contracts.hypothesis import BRANCH_ROLES
from mrr.domain.model_adapter import (
    ModelInvocationOutcome,
    ModelInvocationRequest,
    TerminalStatus,
    TokenUsage,
    apply_redaction,
)
from mrr.services.planner.service import (
    HypothesisForestResult,
    propose_hypothesis_forest,
)

_VALID_HASH = "sha256:" + "a" * 64
_VALID_PROFILE_URN = "urn:mrr:model-profile:01ARZ3NDEKTSV4RRFFQ69G5FAV"
_PRACTICE_ID = "urn:mrr:practice:01ARZ3NDEKTSV4RRFFQ69G5FAV"
_CREATED_BY = "urn:mrr:agent:01ARZ3NDEKTSV4RRFFQ69G5FAV"
_FIXED_CLOCK_INSTANT = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Test fixtures: a scripted fake ModelAdapter, branch/forest payload builders,
# and a deterministic id/clock pair for the determinism test.
# ---------------------------------------------------------------------------


class _ScriptedFakeAdapter:
    """Same in-memory, deterministic fake ``ModelAdapter`` shape as
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


def _branch(role: str, **overrides: object) -> dict[str, object]:
    """One well-formed branch payload for ``role``, with sensible defaults
    for every field the target schema accepts -- overridable per test.
    """
    defaults: dict[str, object] = {
        "statement": f"Statement for the {role} branch.",
        "branch_role": role,
        "priority_rationale": f"Priority rationale for the {role} branch.",
    }
    if role == "insufficient_evidence":
        defaults["insufficiency_rationale"] = "Evidence available does not yet support a branch."
    else:
        defaults["predicted_observations"] = [f"Predicted observation for {role}."]
        defaults["disconfirming_observations"] = [f"Disconfirming observation for {role}."]
    defaults.update(overrides)
    return defaults


def _forest_payload(branches: list[dict[str, object]]) -> str:
    return json.dumps({"branches": branches})


def _sequential_id_factory() -> Callable[[], str]:
    """A fresh, deterministic id factory: a NEW counter starting at 1 each
    time this is called, so two SEPARATE ``propose_hypothesis_forest`` calls
    each built with their own ``_sequential_id_factory()`` mint the exact
    same sequence of ids -- the determinism test's own dependency-injected
    replacement for ``mrr.domain.identity.new_urn``'s randomness.
    """
    counter = itertools.count(1)

    def factory() -> str:
        return f"urn:mrr:hypothesis:{next(counter):026d}"

    return factory


def _fixed_clock() -> datetime:
    return _FIXED_CLOCK_INSTANT


def _propose(
    adapter: _ScriptedFakeAdapter,
    *,
    max_repair_attempts: int = 0,
    id_factory: Callable[[], str] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> HypothesisForestResult:
    return propose_hypothesis_forest(
        adapter,
        research_question="Does the recomputed benchmark value converge to 42 percent?",
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
# forest-across-roles, audit trail, structural not-a-claim-of-result
# ---------------------------------------------------------------------------


def test_produces_forest_of_schema_valid_proposals_across_roles_with_audit_trail() -> None:
    branches = [_branch(role) for role in BRANCH_ROLES]
    fake = _ScriptedFakeAdapter([_completed_outcome(_forest_payload(branches))])

    result = _propose(fake)

    assert result.status == "proposal"
    assert len(result.hypotheses) == len(BRANCH_ROLES)
    assert {h.branch_role for h in result.hypotheses} == set(BRANCH_ROLES)
    assert result.role_waivers == ()
    # Every produced Hypothesis is proposal-only, never a claim of result.
    for hypothesis in result.hypotheses:
        assert hypothesis.status == "proposed"
        assert not hasattr(hypothesis, "verified")
        assert not hasattr(hypothesis, "result")
        assert not hasattr(hypothesis, "authoritative")
    # The audit trail records exactly the one underlying model call.
    assert len(result.attempts) == 1
    assert result.attempts[0].status == "completed"
    assert len(fake.calls) == 1
    assert (
        "Does the recomputed benchmark value converge to 42 percent?" in fake.calls[0].prompt_text
    )


def test_insufficient_evidence_branch_is_accepted_with_empty_observations() -> None:
    branches = [_branch("insufficient_evidence")]
    fake = _ScriptedFakeAdapter([_completed_outcome(_forest_payload(branches))])

    result = _propose(fake)

    assert result.status == "proposal"
    assert len(result.hypotheses) == 1
    hypothesis = result.hypotheses[0]
    assert hypothesis.branch_role == "insufficient_evidence"
    assert hypothesis.predicted_observations == []
    assert hypothesis.disconfirming_observations == []
    assert hypothesis.insufficiency_rationale is not None


# ---------------------------------------------------------------------------
# role coverage: waivers recorded, no produced branch dropped
# ---------------------------------------------------------------------------


def test_missing_roles_get_an_explicit_waiver_and_no_branch_is_dropped() -> None:
    produced_roles = ["confirmatory", "confirmatory", "falsification"]
    branches = [_branch(role) for role in produced_roles]
    fake = _ScriptedFakeAdapter([_completed_outcome(_forest_payload(branches))])

    result = _propose(fake)

    assert result.status == "proposal"
    # Every produced branch is preserved -- including the two under the same
    # role -- never deduplicated or dropped.
    assert len(result.hypotheses) == 3
    assert [h.branch_role for h in result.hypotheses] == produced_roles

    missing_roles = set(BRANCH_ROLES) - {"confirmatory", "falsification"}
    waived_roles = {waiver.branch_role for waiver in result.role_waivers}
    assert waived_roles == missing_roles
    for waiver in result.role_waivers:
        assert waiver.branch_role in waiver.reason
        assert "no branch" in waiver.reason


def test_full_role_coverage_yields_no_waivers() -> None:
    branches = [_branch(role) for role in BRANCH_ROLES]
    fake = _ScriptedFakeAdapter([_completed_outcome(_forest_payload(branches))])

    result = _propose(fake)

    assert result.role_waivers == ()


# ---------------------------------------------------------------------------
# no-invalid-passes: schema-invalid model output never becomes a proposal
# ---------------------------------------------------------------------------


def test_schema_invalid_output_is_not_emitted_as_a_proposal() -> None:
    fake = _ScriptedFakeAdapter([_completed_outcome("this is not valid JSON at all")])

    result = _propose(fake, max_repair_attempts=0)

    assert result.status == "schema_invalid"
    assert result.hypotheses == ()
    assert result.role_waivers == ()
    assert len(result.validation_errors) == 1
    assert len(result.attempts) == 1


def test_one_invalid_branch_fails_the_whole_forest_not_just_that_branch() -> None:
    """Whole-forest atomicity (see mrr.services.planner.service's module
    docstring): a branch missing its falsifiable observations fails the
    ENTIRE structured-generation attempt, not just that one branch.
    """
    branches = [
        _branch("confirmatory"),
        _branch("falsification", predicted_observations=[], disconfirming_observations=[]),
    ]
    fake = _ScriptedFakeAdapter([_completed_outcome(_forest_payload(branches))])

    result = _propose(fake, max_repair_attempts=0)

    assert result.status == "schema_invalid"
    assert result.hypotheses == ()


def test_repair_then_succeed_records_both_attempts_in_the_audit_trail() -> None:
    valid_branches = [_branch("confirmatory")]
    fake = _ScriptedFakeAdapter(
        [
            _completed_outcome("not valid JSON"),
            _completed_outcome(_forest_payload(valid_branches)),
        ]
    )

    result = _propose(fake, max_repair_attempts=1)

    assert result.status == "proposal"
    assert len(result.hypotheses) == 1
    assert len(result.attempts) == 2
    assert len(fake.calls) == 2
    # The repair prompt restates the original question plus the failure.
    assert (
        "Does the recomputed benchmark value converge to 42 percent?" in fake.calls[1].prompt_text
    )


@pytest.mark.parametrize("status", ["refused", "content_filtered", "error", "timed_out"])
def test_underlying_non_completed_status_is_surfaced_verbatim(status: str) -> None:
    fake = _ScriptedFakeAdapter([_non_completed_outcome(status)])  # type: ignore[arg-type]

    result = _propose(fake, max_repair_attempts=5)

    assert result.status == status
    assert result.hypotheses == ()
    assert result.role_waivers == ()
    assert len(result.attempts) == 1
    # No repair attempt is made -- a non-completed outcome halts immediately.
    assert len(fake.calls) == 1


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


def test_same_scripted_adapter_and_inputs_yield_an_identical_forest() -> None:
    branches = [_branch(role) for role in BRANCH_ROLES]

    fake_1 = _ScriptedFakeAdapter([_completed_outcome(_forest_payload(branches))])
    result_1 = _propose(fake_1, id_factory=_sequential_id_factory(), clock=_fixed_clock)

    fake_2 = _ScriptedFakeAdapter([_completed_outcome(_forest_payload(branches))])
    result_2 = _propose(fake_2, id_factory=_sequential_id_factory(), clock=_fixed_clock)

    assert result_1.status == result_2.status == "proposal"
    assert result_1.hypotheses == result_2.hypotheses
    assert result_1.role_waivers == result_2.role_waivers
    assert len(result_1.attempts) == len(result_2.attempts)


# ---------------------------------------------------------------------------
# provider neutrality -- no provider SDK import anywhere in this module
# ---------------------------------------------------------------------------


def test_planner_module_imports_no_provider_sdk_or_network_library() -> None:
    import ast
    from pathlib import Path

    module_path = (
        Path(__file__).resolve().parents[4]
        / "services"
        / "control_plane"
        / "mrr"
        / "services"
        / "planner"
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
