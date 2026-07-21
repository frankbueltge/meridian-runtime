"""K0-T02 (capability dispatch layer) — task-packets/K0-T02.yaml. Drives
``mrr.services.cli.orchestration.run_local_evidence_loop`` end to end against
a real PostgreSQL (this directory's own ``postgres_engine`` fixture,
``tests/e2e/conftest.py``), exactly like ``test_e2e_001_single_node_evidence_loop
.py`` — but exercising the NEW ``dispatch_table``/capability-dispatch wiring
this task adds, not the pinned E2E-001 scenario itself (that file is left
completely unmodified, per this task's own invariant).

Acceptance-test mapping (task-packets/K0-T02.yaml):

- "run_local_evidence_loop called with no dispatch_table and no executor
  override resolves to the same ReferenceTaskExecutor-backed default as
  before this task" is already pinned by every unmodified E2E-001 test in
  ``test_e2e_001_single_node_evidence_loop.py`` (not duplicated here).
- "run_local_evidence_loop called with an explicit executor= override still
  uses exactly that instance, ignoring any dispatch table" ->
  ``test_explicit_executor_override_takes_precedence_over_dispatch_table``.
- The dispatch MECHANISM generalizing beyond the single hardcoded reference
  capability, exercised through the full production composition function
  rather than only the pure ``mrr.services.node_runtime.dispatch`` unit
  tests (task-packets/K0-T02.yaml forbidden_changes: "this task's own tests
  exercise the dispatch MECHANISM with an in-test fake Executor registered
  under a second capability name, not a real second implementation") ->
  ``test_dispatch_table_routes_a_non_reference_capability_to_its_registered_executor``.
- "Unknown capability name fails closed with a distinct, typed error" for a
  capability with no dispatch entry at all (no profile, no override) ->
  ``test_unrouted_capability_fails_closed_before_any_run_manifest_or_crate``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.adapters.object_store.local import LocalFilesystemArtifactStore
from mrr.domain.exceptions import UnknownCapabilityError
from mrr.persistence.repositories import PostgresEventLog
from mrr.services.cli.orchestration import run_local_evidence_loop
from mrr.services.node_runtime.dispatch import CapabilityDispatchTable, build_dispatch_table
from mrr.services.node_runtime.executor import ExecutionResult, ReferenceTaskExecutor
from sqlalchemy import Engine

#: A fixed, caller-injected code revision — mirrors test_e2e_001's own
#: ``_TEST_CODE_REVISION`` (never derived from the real checked-out git
#: commit; see that module's docstring).
_TEST_CODE_REVISION = "git:k0-t02-test-fixture"

#: A capability name that is NOT ``DEFAULT_CAPABILITY_NAME`` — deliberately
#: never present in the single-entry default table
#: ``run_local_evidence_loop`` builds when no ``dispatch_table``/``executor``
#: override is supplied, so it can only ever be routed by a caller-supplied
#: ``dispatch_table``.
_SECOND_CAPABILITY_NAME = "mrr.k0-t02-test.second-capability"
_SECOND_CAPABILITY_VERSION = "1.0.0"


def _artifact_store(tmp_path: Path) -> LocalFilesystemArtifactStore:
    return LocalFilesystemArtifactStore(tmp_path / "artifacts")


@dataclass
class _SecondCapabilityExecutor:
    """An in-test double standing in for K1-T03's not-yet-built profile
    executor (task-packets/K0-T02.yaml forbidden_changes: "not a real second
    implementation"). Delegates to a fresh ``ReferenceTaskExecutor``'s own
    deterministic computation — this test cares only that dispatch routed to
    THIS instance under a second capability name, not about a novel
    execution semantics of its own.
    """

    _delegate: ReferenceTaskExecutor = field(default_factory=ReferenceTaskExecutor)

    def execute(self, task_bundle: Any, inputs: Any, *, execution_attempt: int) -> ExecutionResult:
        return self._delegate.execute(task_bundle, inputs, execution_attempt=execution_attempt)


def test_dispatch_table_routes_a_non_reference_capability_to_its_registered_executor(
    postgres_engine: Engine, tmp_path: Path
) -> None:
    store = _artifact_store(tmp_path)
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    second_executor = _SecondCapabilityExecutor()
    table = build_dispatch_table([], {_SECOND_CAPABILITY_NAME: lambda: second_executor})

    result = run_local_evidence_loop(
        engine=postgres_engine,
        artifact_store=store,
        origin_signing_key=origin_key,
        node_signing_key=node_key,
        capability_name=_SECOND_CAPABILITY_NAME,
        capability_version=_SECOND_CAPABILITY_VERSION,
        dispatch_table=table,
        code_revision=_TEST_CODE_REVISION,
    )

    assert result.run_state == "completed"
    assert result.output_hash is not None


def test_unrouted_capability_fails_closed_before_any_run_manifest_or_crate(
    postgres_engine: Engine, tmp_path: Path
) -> None:
    """A capability the node declares (so ``CapabilityNotDeclaredError``
    never fires) but that no ``executor``/``dispatch_table`` routes fails
    closed with ``UnknownCapabilityError`` — never a silent fallback to
    ``ReferenceTaskExecutor`` — and never reaches execution, so neither a
    ``RunManifest`` nor an ``EvidenceCrate`` is ever recorded for it.
    """
    store = _artifact_store(tmp_path)
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()

    with pytest.raises(UnknownCapabilityError) as exc_info:
        run_local_evidence_loop(
            engine=postgres_engine,
            artifact_store=store,
            origin_signing_key=origin_key,
            node_signing_key=node_key,
            capability_name=_SECOND_CAPABILITY_NAME,
            capability_version=_SECOND_CAPABILITY_VERSION,
            code_revision=_TEST_CODE_REVISION,
        )

    assert exc_info.value.capability_name == _SECOND_CAPABILITY_NAME

    event_log = PostgresEventLog(postgres_engine)
    recorded_manifest_events = [
        appended
        for appended in event_log.read_all()
        if appended.event.event_type == "run_manifest.recorded"
    ]
    sealed_crate_events = [
        appended
        for appended in event_log.read_all()
        if appended.event.event_type == "evidence_crate.sealed"
    ]
    assert recorded_manifest_events == []
    assert sealed_crate_events == []


def test_explicit_executor_override_takes_precedence_over_dispatch_table(
    postgres_engine: Engine, tmp_path: Path
) -> None:
    """An explicit ``executor=`` override is used exactly as supplied,
    ignoring ``dispatch_table`` entirely — even an EMPTY dispatch table (which
    would raise ``UnknownCapabilityError`` if actually consulted) must not
    stop the override from being honored, unchanged precedence over today's
    behavior.
    """
    store = _artifact_store(tmp_path)
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    override = ReferenceTaskExecutor(policy_gate=lambda _bundle: False)
    empty_table: CapabilityDispatchTable = {}

    result = run_local_evidence_loop(
        engine=postgres_engine,
        artifact_store=store,
        origin_signing_key=origin_key,
        node_signing_key=node_key,
        executor=override,
        dispatch_table=empty_table,
        code_revision=_TEST_CODE_REVISION,
    )

    assert result.run_state == "policy_denied"
