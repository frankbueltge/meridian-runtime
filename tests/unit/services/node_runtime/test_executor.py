"""Unit tests for ``mrr.services.node_runtime.executor`` (task-packets/
E2-T04.yaml) — entirely DB-free, no PostgreSQL, no ``sqlalchemy.Engine``:
``ReferenceTaskExecutor`` is a pure in-process object with no persistence
dependency at all.

Acceptance-test mapping (task-packets/E2-T04.yaml):

- "the reference task runs and returns completed with a deterministic,
  replayable output hash (same across repeated runs)" ->
  ``test_completed_run_has_deterministic_replayable_output_hash``.
- "a task that raises returns failed (not completed, not a generic error)" ->
  ``test_raising_transform_returns_failed_not_an_exception``.
- "a task exceeding its wall-clock bound returns timed_out" ->
  ``test_transform_exceeding_wall_clock_bound_returns_timed_out`` (a real,
  ~1.2s wall-clock wait against the schema-minimum ``timeout_seconds=1`` — a
  deliberately slow injected transform, so the timeout path is genuinely
  exercised, not just theoretically present).
- "a cancelled and a policy_denied path each return their explicit terminal
  outcome" -> ``test_cancellation_signal_returns_cancelled_without_running``,
  ``test_policy_gate_denial_returns_policy_denied_without_running``.
- "re-executing the same (task_id, revision, execution_attempt) is
  idempotent (same outcome, no double side effect)" ->
  ``test_same_attempt_tuple_is_idempotent_and_does_not_rerun_transform``,
  ``test_different_execution_attempt_is_a_fresh_run``.
- "the result distinguishes deterministic from stochastic (reference task is
  deterministic)" -> ``test_every_outcome_is_marked_deterministic``.
- honesty boundary ("does NOT claim isolation... refuses to pretend
  otherwise") -> ``test_reference_executor_does_not_claim_isolation``,
  ``test_requesting_isolation_raises_instead_of_pretending``.

Plus this task's own addition (not in the packet's acceptance list, flagged
as an open specification question in the PR): a natural trigger for the
``partial`` terminal outcome — ``test_missing_declared_input_returns_partial``.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from mrr.contracts import ArtifactRef, TaskBundle
from mrr.crypto.hashing import content_hash
from mrr.domain.exceptions import UntrustedIsolationNotAvailableError
from mrr.domain.identity import new_urn
from mrr.services.node_runtime.executor import (
    Executor,
    ReferenceTaskExecutor,
    default_reference_transform,
)

# ---------------------------------------------------------------------------
# TaskBundle fixture factory — same shape and convention as
# tests/unit/services/task_bundle/test_service.py's own ``_bundle()``. The
# executor never verifies ``signature``/``nonce``/lifecycle ``status`` (out
# of this task's scope — E2-T03 already owns negotiation), so these fields
# are present only because ``TaskBundle`` requires them to be schema-valid.
# ---------------------------------------------------------------------------


def _bundle(
    *,
    revision: int = 1,
    timeout_seconds: int = 5,
    declared_inputs: list[ArtifactRef] | None = None,
    **overrides: Any,
) -> TaskBundle:
    now = datetime.now(UTC)
    data: dict[str, Any] = {
        "id": new_urn("task-bundle"),
        "api_version": "mrr/v1alpha1",
        "kind": "TaskBundle",
        "practice_id": new_urn("practice"),
        "revision": revision,
        "created_at": now,
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "origin_practice_id": new_urn("practice"),
        "target_node_id": new_urn("node"),
        "research_score_id": new_urn("research-score"),
        "research_score_revision": 1,
        "branch_id": new_urn("branch"),
        "capability": {"name": "reference.deterministic-transform", "version": "1.0.0"},
        "purpose": "Run the bounded, deterministic reference computation.",
        "instructions": {},
        "inputs": [ref.model_dump(mode="json") for ref in (declared_inputs or [])],
        "data_access_mode": "none",
        "execution": {
            "image_digest": "sha256:" + "c" * 64,
            "entrypoint": ["run.sh"],
        },
        "resource_limits": {
            "cpu": 1.0,
            "memory_mb": 512,
            "disk_mb": 100,
            "timeout_seconds": timeout_seconds,
        },
        "network_policy": {"mode": "deny_all", "allowlist": []},
        "output_schema": "urn:mrr:schema:evidence-crate:1",
        "classification": "PUBLIC",
        "approval_requirement": "automatic",
        "expires_at": now + timedelta(days=1),
        "nonce": "n" * 16,
        "signature": {
            "signer_practice_id": new_urn("practice"),
            "key_id": "origin-key",
            "algorithm": "Ed25519",
            "signed_at": now,
            "value": "0" * 44,
        },
        "status": "RUNNING",
    }
    data.update(overrides)
    return TaskBundle.model_validate(data)


def _artifact_ref(artifact_id: str | None = None) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=artifact_id or new_urn("artifact"),
        content_hash="sha256:" + "b" * 64,
    )


# ---------------------------------------------------------------------------
# Protocol conformance. This assignment is itself the test: mypy (which
# type-checks tests/, per pyproject.toml's [tool.mypy] `files`) statically
# rejects it if ReferenceTaskExecutor's `execute` signature ever drifts from
# the `Executor` Protocol's — a structural check `make typecheck` enforces on
# every run, not just this one.
# ---------------------------------------------------------------------------


def test_reference_task_executor_satisfies_executor_protocol() -> None:
    executor: Executor = ReferenceTaskExecutor()
    assert isinstance(executor, ReferenceTaskExecutor)


# ---------------------------------------------------------------------------
# completed: deterministic, replayable output hash.
# ---------------------------------------------------------------------------


def test_completed_run_has_deterministic_replayable_output_hash() -> None:
    executor = ReferenceTaskExecutor()
    bundle = _bundle()
    inputs: Mapping[str, bytes] = {"a": b"hello", "b": b"world"}

    first = executor.execute(bundle, inputs, execution_attempt=1)

    assert first.outcome == "completed"
    assert first.output is not None
    assert first.output_hash == content_hash(first.output)
    assert first.is_deterministic is True
    assert first.detail is None

    # A second, entirely independent executor instance and a freshly built
    # (but content-identical) inputs mapping must reproduce the identical
    # output and hash — "replayable... across runs and machines".
    second_executor = ReferenceTaskExecutor()
    second_bundle = bundle.model_copy(deep=True)
    second_inputs: Mapping[str, bytes] = {"b": b"world", "a": b"hello"}  # different order
    second = second_executor.execute(second_bundle, second_inputs, execution_attempt=1)

    assert second.output == first.output
    assert second.output_hash == first.output_hash


def test_default_transform_ignores_dict_insertion_order() -> None:
    assert default_reference_transform({"a": b"1", "b": b"2"}) == default_reference_transform(
        {"b": b"2", "a": b"1"}
    )


def test_every_outcome_is_marked_deterministic() -> None:
    executor = ReferenceTaskExecutor(policy_gate=lambda _bundle: False)
    result = executor.execute(_bundle(), {}, execution_attempt=1)

    assert result.outcome == "policy_denied"
    assert result.is_deterministic is True


# ---------------------------------------------------------------------------
# failed: a raising transform.
# ---------------------------------------------------------------------------


def test_raising_transform_returns_failed_not_an_exception() -> None:
    def _raising_transform(_inputs: Mapping[str, bytes]) -> bytes:
        raise RuntimeError("boom")

    executor = ReferenceTaskExecutor(transform=_raising_transform)
    result = executor.execute(_bundle(), {}, execution_attempt=1)

    assert result.outcome == "failed"
    assert result.output is None
    assert result.output_hash is None
    assert result.detail is not None
    assert "boom" in result.detail


# ---------------------------------------------------------------------------
# timed_out: a genuinely slow transform against the schema-minimum bound.
# ---------------------------------------------------------------------------


def test_transform_exceeding_wall_clock_bound_returns_timed_out() -> None:
    def _slow_transform(_inputs: Mapping[str, bytes]) -> bytes:
        time.sleep(1.2)
        return b"too-late"

    executor = ReferenceTaskExecutor(transform=_slow_transform)
    bundle = _bundle(timeout_seconds=1)  # schema minimum (resource_limits ge=1)

    started = time.monotonic()
    result = executor.execute(bundle, {}, execution_attempt=1)
    elapsed = time.monotonic() - started

    assert result.outcome == "timed_out"
    assert result.output is None
    assert result.output_hash is None
    assert result.detail is not None
    assert "1" in result.detail
    # Returns promptly at the bound, not after the slow transform finishes.
    assert elapsed < 1.2


# ---------------------------------------------------------------------------
# cancelled / policy_denied: explicit pre-check paths, nothing executed.
# ---------------------------------------------------------------------------


def test_cancellation_signal_returns_cancelled_without_running() -> None:
    calls: list[Mapping[str, bytes]] = []

    def _counting_transform(inputs: Mapping[str, bytes]) -> bytes:
        calls.append(inputs)
        return default_reference_transform(inputs)

    executor = ReferenceTaskExecutor(transform=_counting_transform, is_cancelled=lambda: True)
    result = executor.execute(_bundle(), {"a": b"1"}, execution_attempt=1)

    assert result.outcome == "cancelled"
    assert result.output is None
    assert result.output_hash is None
    assert calls == []  # the transform never ran


def test_policy_gate_denial_returns_policy_denied_without_running() -> None:
    calls: list[Mapping[str, bytes]] = []

    def _counting_transform(inputs: Mapping[str, bytes]) -> bytes:
        calls.append(inputs)
        return default_reference_transform(inputs)

    executor = ReferenceTaskExecutor(transform=_counting_transform, policy_gate=lambda _b: False)
    result = executor.execute(_bundle(), {"a": b"1"}, execution_attempt=1)

    assert result.outcome == "policy_denied"
    assert result.output is None
    assert result.output_hash is None
    assert calls == []


def test_policy_gate_receives_the_task_bundle_and_can_allow() -> None:
    seen: list[TaskBundle] = []

    def _gate(bundle: TaskBundle) -> bool:
        seen.append(bundle)
        return True

    bundle = _bundle()
    executor = ReferenceTaskExecutor(policy_gate=_gate)
    result = executor.execute(bundle, {}, execution_attempt=1)

    assert result.outcome == "completed"
    assert seen == [bundle]


# ---------------------------------------------------------------------------
# partial: a declared input the caller did not resolve.
# ---------------------------------------------------------------------------


def test_missing_declared_input_returns_partial() -> None:
    present = _artifact_ref()
    missing = _artifact_ref()
    bundle = _bundle(declared_inputs=[present, missing])

    executor = ReferenceTaskExecutor()
    result = executor.execute(bundle, {present.artifact_id: b"payload"}, execution_attempt=1)

    assert result.outcome == "partial"
    assert result.output is not None
    assert result.output_hash == content_hash(result.output)
    assert result.detail is not None
    assert missing.artifact_id in result.detail


def test_all_declared_inputs_resolved_is_completed_not_partial() -> None:
    ref = _artifact_ref()
    bundle = _bundle(declared_inputs=[ref])

    executor = ReferenceTaskExecutor()
    result = executor.execute(bundle, {ref.artifact_id: b"payload"}, execution_attempt=1)

    assert result.outcome == "completed"


# ---------------------------------------------------------------------------
# Idempotency (MRR-FR-035): same (task_id, revision, execution_attempt).
# ---------------------------------------------------------------------------


def test_same_attempt_tuple_is_idempotent_and_does_not_rerun_transform() -> None:
    calls: list[Mapping[str, bytes]] = []

    def _counting_transform(inputs: Mapping[str, bytes]) -> bytes:
        calls.append(inputs)
        return default_reference_transform(inputs)

    executor = ReferenceTaskExecutor(transform=_counting_transform)
    bundle = _bundle()
    inputs: Mapping[str, bytes] = {"a": b"1"}

    first = executor.execute(bundle, inputs, execution_attempt=7)
    second = executor.execute(bundle, inputs, execution_attempt=7)

    assert first == second
    assert len(calls) == 1  # the transform ran exactly once


def test_idempotency_memoizes_non_completed_outcomes_too() -> None:
    checks = 0

    def _is_cancelled() -> bool:
        nonlocal checks
        checks += 1
        return True

    executor = ReferenceTaskExecutor(is_cancelled=_is_cancelled)
    bundle = _bundle()

    first = executor.execute(bundle, {}, execution_attempt=3)
    second = executor.execute(bundle, {}, execution_attempt=3)

    assert first == second
    assert first.outcome == "cancelled"
    assert checks == 1  # the cancellation check itself is not re-invoked


def test_different_execution_attempt_is_a_fresh_run() -> None:
    calls: list[Mapping[str, bytes]] = []

    def _counting_transform(inputs: Mapping[str, bytes]) -> bytes:
        calls.append(inputs)
        return default_reference_transform(inputs)

    executor = ReferenceTaskExecutor(transform=_counting_transform)
    bundle = _bundle()
    inputs: Mapping[str, bytes] = {"a": b"1"}

    first = executor.execute(bundle, inputs, execution_attempt=1)
    second = executor.execute(bundle, inputs, execution_attempt=2)

    # Same content -> the same deterministic outcome and output/hash ...
    assert first.outcome == second.outcome == "completed"
    assert first.output == second.output
    assert first.output_hash == second.output_hash
    # ... but each is its own attempt, and each attempt genuinely ran.
    assert first.execution_attempt == 1
    assert second.execution_attempt == 2
    assert len(calls) == 2


def test_different_revision_is_a_fresh_run_under_the_same_id() -> None:
    executor = ReferenceTaskExecutor()
    bundle = _bundle(revision=1)
    revised = bundle.model_copy(update={"revision": 2})

    first = executor.execute(bundle, {}, execution_attempt=1)
    second = executor.execute(revised, {}, execution_attempt=1)

    assert first.task_revision == 1
    assert second.task_revision == 2


def test_execution_attempt_must_be_positive() -> None:
    executor = ReferenceTaskExecutor()
    with pytest.raises(ValueError, match="execution_attempt"):
        executor.execute(_bundle(), {}, execution_attempt=0)


# ---------------------------------------------------------------------------
# Honesty boundary: this is not, and cannot be asked to become, a sandbox.
# ---------------------------------------------------------------------------


def test_reference_executor_does_not_claim_isolation() -> None:
    assert ReferenceTaskExecutor.provides_untrusted_isolation is False
    assert ReferenceTaskExecutor().provides_untrusted_isolation is False


def test_requesting_isolation_raises_instead_of_pretending() -> None:
    with pytest.raises(UntrustedIsolationNotAvailableError):
        ReferenceTaskExecutor(require_isolation=True)
