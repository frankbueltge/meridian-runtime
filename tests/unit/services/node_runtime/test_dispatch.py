"""Unit tests for ``mrr.services.node_runtime.dispatch`` (task-packets/
K0-T02.yaml) — entirely DB-free, no PostgreSQL, no ``sqlalchemy.Engine``:
``build_dispatch_table``/``dispatch`` are pure functions over caller-supplied
data with no persistence dependency at all.

Acceptance-test mapping (task-packets/K0-T02.yaml):

- "dispatching a TaskBundle whose capability.name matches a table entry
  returns that exact Executor instance" ->
  ``test_dispatch_returns_the_exact_registered_executor_instance``.
- "dispatching a TaskBundle whose capability.name has no table entry raises
  UnknownCapabilityError, carrying the unrecognized capability_name" ->
  ``test_dispatch_raises_unknown_capability_error_carrying_the_name``.
- "build_dispatch_table, given one accepted MethodProfile declaring a NEW
  capability name plus a caller-supplied fake Executor factory for that
  name, produces a table that successfully dispatches a TaskBundle declaring
  that capability" -> ``test_build_dispatch_table_routes_a_new_profile_declared_capability``.
- "build_dispatch_table, given a MethodProfile that declares a capability
  name with NO caller-supplied factory, produces a table where that name is
  simply absent" ->
  ``test_declared_but_unwired_capability_is_absent_and_fails_like_any_unknown_name``.
- "after adding the new module, both import-linter contracts are still KEPT"
  is exercised by ``tests/unit/architecture/test_import_boundaries.py``, not
  duplicated here.

Plus this task's own additions (not in the packet's literal acceptance list,
flagged in the PR as straightforward corollaries of its invariants):
``test_build_dispatch_table_reproduces_the_reference_capability_default``
(the exact default table ``run_local_evidence_loop`` builds when no
``dispatch_table``/``executor`` override is supplied) and
``test_unknown_capability_never_falls_back_to_reference_executor`` (pinning
the "no third outcome, no silent fallback" invariant directly).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from mrr.contracts import MethodProfile, TaskBundle
from mrr.domain.exceptions import UnknownCapabilityError
from mrr.domain.identity import new_urn
from mrr.services.node_runtime.dispatch import (
    build_dispatch_table,
    dispatch,
)
from mrr.services.node_runtime.executor import ExecutionResult, Executor, ReferenceTaskExecutor

# ---------------------------------------------------------------------------
# Fixture factories — same shape/convention as
# tests/unit/services/node_runtime/test_executor.py's ``_bundle`` and
# tests/unit/services/method_profile/test_service.py's ``_profile``.
# ---------------------------------------------------------------------------


def _bundle(
    *,
    capability_name: str = "reference.deterministic-transform",
    capability_version: str = "1.0.0",
    **overrides: Any,
) -> TaskBundle:
    now = datetime.now(UTC)
    data: dict[str, Any] = {
        "id": new_urn("task-bundle"),
        "api_version": "mrr/v1alpha1",
        "kind": "TaskBundle",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": now,
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "origin_practice_id": new_urn("practice"),
        "target_node_id": new_urn("node"),
        "research_score_id": new_urn("research-score"),
        "research_score_revision": 1,
        "branch_id": new_urn("branch"),
        "capability": {"name": capability_name, "version": capability_version},
        "purpose": "Run the bounded, deterministic reference computation.",
        "instructions": {},
        "inputs": [],
        "data_access_mode": "none",
        "execution": {
            "image_digest": "sha256:" + "c" * 64,
            "entrypoint": ["run.sh"],
        },
        "resource_limits": {
            "cpu": 1.0,
            "memory_mb": 512,
            "disk_mb": 100,
            "timeout_seconds": 5,
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


def _profile(*, executor_task_family: list[str], **overrides: Any) -> MethodProfile:
    data: dict[str, Any] = {
        "id": new_urn("method-profile"),
        "api_version": "mrr/v1alpha1",
        "kind": "MethodProfile",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": datetime.now(UTC),
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "profile_key": "systematic_evidence_synthesis",
        "version": "1.0.0",
        "claim_types": ["observational"],
        "max_claim_ceiling": "associational_unadjusted",
        "protocol_form": "synthesis_protocol",
        "executor_task_family": executor_task_family,
        "executor_steps": [{"name": "snapshot_loading", "kind": "deterministic"}],
        "inappropriate_uses": ["causal claims"],
        "status": "accepted",
    }
    data.update(overrides)
    return MethodProfile.model_validate(data)


class _FakeExecutor:
    """A minimal, in-test ``Executor`` stand-in — task-packets/K0-T02.yaml
    forbidden_changes: "this task's own tests exercise the dispatch
    MECHANISM with an in-test fake Executor registered under a second
    capability name, not a real second implementation" (a REAL
    profile-driven executor is K1-T03's job, out of this task's scope).
    """

    def __init__(self, tag: str = "fake") -> None:
        self.tag = tag

    def execute(
        self, task_bundle: TaskBundle, inputs: Any, *, execution_attempt: int
    ) -> ExecutionResult:
        raise NotImplementedError("never invoked by these dispatch-only tests")


def test_fake_executor_satisfies_executor_protocol() -> None:
    # Mirrors test_executor.py's own protocol-conformance pin: mypy statically
    # rejects this assignment if _FakeExecutor's `execute` ever drifts from
    # the `Executor` Protocol's shape.
    executor: Executor = _FakeExecutor()
    assert isinstance(executor, _FakeExecutor)


# ---------------------------------------------------------------------------
# dispatch(): exactly two outcomes.
# ---------------------------------------------------------------------------


def test_dispatch_returns_the_exact_registered_executor_instance() -> None:
    fake = _FakeExecutor("primary")
    table = {"reference.deterministic-transform": fake}
    bundle = _bundle(capability_name="reference.deterministic-transform")

    result = dispatch(bundle, table)

    assert result is fake


def test_dispatch_raises_unknown_capability_error_carrying_the_name() -> None:
    table: dict[str, Executor] = {"some.other-capability": _FakeExecutor()}
    bundle = _bundle(capability_name="never-registered.capability")

    with pytest.raises(UnknownCapabilityError) as exc_info:
        dispatch(bundle, table)

    assert exc_info.value.capability_name == "never-registered.capability"


def test_unknown_capability_never_falls_back_to_reference_executor() -> None:
    """Pins the "no third outcome, no silent fallback" invariant directly: an
    unrouted capability must not silently resolve to ``ReferenceTaskExecutor``
    (or anything else) even when a table happens to also contain a reference
    entry under a DIFFERENT name.
    """
    table: dict[str, Executor] = {"reference.deterministic-transform": ReferenceTaskExecutor()}
    bundle = _bundle(capability_name="totally-unrelated.capability")

    with pytest.raises(UnknownCapabilityError):
        dispatch(bundle, table)


def test_empty_table_raises_unknown_capability_error() -> None:
    bundle = _bundle()

    with pytest.raises(UnknownCapabilityError):
        dispatch(bundle, {})


# ---------------------------------------------------------------------------
# build_dispatch_table(): profile-declared capabilities paired with
# caller-supplied factories, plus the grandfathered reference capability.
# ---------------------------------------------------------------------------


def test_build_dispatch_table_routes_a_new_profile_declared_capability() -> None:
    """K0-T02 acceptance test: an accepted MethodProfile declaring a brand
    new capability name, paired with an in-test fake Executor factory,
    produces a table that successfully dispatches — proving the mechanism
    generalizes beyond the single hardcoded reference capability, without
    any real K1-T03 executor existing yet.
    """
    profile = _profile(executor_task_family=["mrr.method.new-synthesis/1"])
    fake = _FakeExecutor("synthesis")
    table = build_dispatch_table([profile], {"mrr.method.new-synthesis/1": lambda: fake})

    bundle = _bundle(capability_name="mrr.method.new-synthesis/1")
    assert dispatch(bundle, table) is fake


def test_declared_but_unwired_capability_is_absent_and_fails_like_any_unknown_name() -> None:
    """K0-T02 acceptance test: a MethodProfile declaring a capability with NO
    caller-supplied factory leaves the table without that entry — dispatching
    it later raises the SAME UnknownCapabilityError as any other unrecognized
    name, not a distinct table-construction-time error (derived_decisions
    (e): "no real method-profile executor exists yet" makes this the
    everyday case for every non-reference profile capability today).
    """
    profile = _profile(executor_task_family=["mrr.method.unwired/1"])

    table = build_dispatch_table([profile], {})

    assert "mrr.method.unwired/1" not in table
    bundle = _bundle(capability_name="mrr.method.unwired/1")
    with pytest.raises(UnknownCapabilityError) as exc_info:
        dispatch(bundle, table)
    assert exc_info.value.capability_name == "mrr.method.unwired/1"


def test_build_dispatch_table_reproduces_the_reference_capability_default() -> None:
    """The exact single-entry table ``run_local_evidence_loop`` builds when
    no ``dispatch_table``/``executor`` override is supplied — an empty
    profile list plus ``{DEFAULT_CAPABILITY_NAME: ReferenceTaskExecutor}`` —
    reproducing today's hardcoded default byte-for-byte (task-packets/
    K0-T02.yaml derived_decisions (d)).
    """
    table = build_dispatch_table([], {"reference.deterministic-transform": ReferenceTaskExecutor})

    assert set(table) == {"reference.deterministic-transform"}
    assert isinstance(table["reference.deterministic-transform"], ReferenceTaskExecutor)

    bundle = _bundle(capability_name="reference.deterministic-transform")
    resolved = dispatch(bundle, table)
    assert isinstance(resolved, ReferenceTaskExecutor)


def test_build_dispatch_table_calls_each_factory_exactly_once() -> None:
    calls = 0

    def _factory() -> Executor:
        nonlocal calls
        calls += 1
        return _FakeExecutor()

    profile = _profile(executor_task_family=["mrr.method.counted/1"])
    build_dispatch_table([profile], {"mrr.method.counted/1": _factory})

    assert calls == 1


def test_an_unrelated_profile_declared_capability_does_not_interfere() -> None:
    """A profile declaring some OTHER capability, with its own matching
    factory, leaves the reference capability's own entry (and dispatch
    behavior) completely unaffected — table entries are additive, not
    exclusive.
    """
    profile = _profile(executor_task_family=["mrr.method.other/1"])
    other_fake = _FakeExecutor("other")
    table = build_dispatch_table(
        [profile],
        {
            "mrr.method.other/1": lambda: other_fake,
            "reference.deterministic-transform": ReferenceTaskExecutor,
        },
    )

    assert set(table) == {"mrr.method.other/1", "reference.deterministic-transform"}
    reference_bundle = _bundle(capability_name="reference.deterministic-transform")
    assert isinstance(dispatch(reference_bundle, table), ReferenceTaskExecutor)
    other_bundle = _bundle(capability_name="mrr.method.other/1")
    assert dispatch(other_bundle, table) is other_fake
