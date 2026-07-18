"""Property tests for the deterministic reference task (task-packets/
E2-T04.yaml, MRR-FR-044): "A property test over arbitrary inputs:
determinism holds (same input -> same hash; the run is marked
deterministic)".

Exercises ``default_reference_transform`` directly (the pure computation)
and ``ReferenceTaskExecutor.execute`` end to end (the full result, including
``is_deterministic`` and the ``output_hash``/``content_hash`` agreement).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from hypothesis import given
from hypothesis import strategies as st
from mrr.contracts import TaskBundle
from mrr.crypto.hashing import content_hash
from mrr.domain.identity import new_urn
from mrr.services.node_runtime.executor import (
    ReferenceTaskExecutor,
    default_reference_transform,
)

_inputs_strategy = st.dictionaries(
    keys=st.text(min_size=1, max_size=20),
    values=st.binary(max_size=64),
    max_size=8,
)


def _bundle(**overrides: Any) -> TaskBundle:
    """Minimal valid ``TaskBundle`` fixture — a local copy of the same
    factory ``tests/unit/services/node_runtime/test_executor.py`` uses (that
    unit-tier module's own docstring explains why this codebase duplicates
    such fixtures per test module rather than sharing them across tiers).
    The executor never verifies ``signature``/``nonce``/lifecycle ``status``,
    so these fields are present only to satisfy schema validity.
    """
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
        "capability": {"name": "reference.deterministic-transform", "version": "1.0.0"},
        "purpose": "Run the bounded, deterministic reference computation.",
        "instructions": {},
        "inputs": [],
        "data_access_mode": "none",
        "execution": {
            "image_digest": "sha256:" + "c" * 64,
            "entrypoint": ["run.sh"],
        },
        "resource_limits": {"cpu": 1.0, "memory_mb": 512, "disk_mb": 100, "timeout_seconds": 5},
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


@given(_inputs_strategy)
def test_default_transform_is_deterministic_across_repeated_calls(
    inputs: dict[str, bytes],
) -> None:
    first = default_reference_transform(inputs)
    second = default_reference_transform(dict(inputs))  # a fresh, equal-content mapping

    assert first == second
    assert content_hash(first) == content_hash(second)


@given(_inputs_strategy)
def test_default_transform_is_independent_of_key_construction_order(
    inputs: dict[str, bytes],
) -> None:
    reordered: Mapping[str, bytes] = dict(reversed(list(inputs.items())))

    assert default_reference_transform(inputs) == default_reference_transform(reordered)


@given(_inputs_strategy)
def test_executor_produces_identical_output_hash_for_identical_inputs(
    inputs: dict[str, bytes],
) -> None:
    # bundle.inputs stays empty (no declared artifact_ids), so an arbitrary
    # `inputs` mapping here never triggers the `partial` path — this
    # property is about `completed`'s determinism specifically.
    bundle = _bundle()

    first = ReferenceTaskExecutor().execute(bundle, inputs, execution_attempt=1)
    second = ReferenceTaskExecutor().execute(bundle, dict(inputs), execution_attempt=1)

    assert first.outcome == "completed"
    assert second.outcome == "completed"
    assert first.output == second.output
    assert first.output_hash == second.output_hash
    assert first.output is not None
    assert first.output_hash == content_hash(first.output)
    assert first.is_deterministic is True
    assert second.is_deterministic is True
