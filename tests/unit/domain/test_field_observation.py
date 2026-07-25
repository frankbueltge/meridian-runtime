"""Unit tests for ``mrr.domain.field_observation`` (task-packets/R2-T01.yaml
R1/R6, unit tier). DB-free, no-network — every input here is a hand-built
:class:`AnchorCheckResult`/hash string, never a fixture read from disk (the
real committed batch is exercised separately, at the contract tier, in
tests/contract/test_field_observation_acceptance.py).
"""

from __future__ import annotations

import pytest
from mrr.domain.field_observation import (
    BATCH_ROLES,
    BatchInput,
    IntegrityGateError,
    ObservationBatch,
    check_anchor,
    check_and_gate,
)

_OK_HASH = "sha256:" + "a" * 64
_OTHER_HASH = "sha256:" + "b" * 64


# ---------------------------------------------------------------------------
# check_anchor
# ---------------------------------------------------------------------------


def test_check_anchor_ok_when_hashes_are_exactly_equal() -> None:
    result = check_anchor("manifest", "some/path.json", _OK_HASH, _OK_HASH)
    assert result.status == "anchor_ok"
    assert result.role == "manifest"
    assert result.path == "some/path.json"
    assert result.declared_sha256 == _OK_HASH
    assert result.actual_sha256 == _OK_HASH


def test_check_anchor_mismatch_when_hashes_differ() -> None:
    result = check_anchor("snapshot", "some/path.json", _OK_HASH, _OTHER_HASH)
    assert result.status == "anchor_mismatch"
    assert result.declared_sha256 == _OK_HASH
    assert result.actual_sha256 == _OTHER_HASH


def test_check_anchor_never_normalises_case() -> None:
    """task-packets/R2-T01.yaml R1: "no normalisation guessed" — an
    otherwise-equal hash differing only by letter case is a mismatch, never
    silently treated as equal.
    """
    upper = "sha256:" + "A" * 64
    lower = "sha256:" + "a" * 64
    result = check_anchor("manifest", "p.json", lower, upper)
    assert result.status == "anchor_mismatch"


# ---------------------------------------------------------------------------
# check_and_gate — the fail-closed core.
# ---------------------------------------------------------------------------


def test_check_and_gate_does_not_raise_when_all_results_match() -> None:
    results = [
        check_anchor("manifest", "m.json", _OK_HASH, _OK_HASH),
        check_anchor("snapshot", "s.json", _OK_HASH, _OK_HASH),
    ]
    check_and_gate(results)  # must not raise


def test_check_and_gate_does_not_raise_on_empty_input() -> None:
    check_and_gate([])  # must not raise


def test_check_and_gate_raises_integrity_gate_error_on_a_single_mismatch() -> None:
    results = [
        check_anchor("manifest", "m.json", _OK_HASH, _OK_HASH),
        check_anchor("snapshot", "s.json", _OK_HASH, _OTHER_HASH),
    ]
    with pytest.raises(IntegrityGateError) as excinfo:
        check_and_gate(results)
    assert excinfo.value.role == "snapshot"
    assert excinfo.value.path == "s.json"
    assert excinfo.value.declared_sha256 == _OK_HASH
    assert excinfo.value.actual_sha256 == _OTHER_HASH


def test_check_and_gate_names_the_first_mismatch_in_role_sorted_order() -> None:
    """task-packets/R2-T01.yaml R1: "naming the FIRST mismatch in a stable
    order (sorted by role)" — passing snapshot before manifest in the
    caller's own argument order must not change which mismatch is named:
    "manifest" sorts before "snapshot", so a manifest mismatch is always
    named first when both mismatch.
    """
    results = [
        check_anchor("snapshot", "s.json", _OK_HASH, _OTHER_HASH),
        check_anchor("manifest", "m.json", _OK_HASH, _OTHER_HASH),
    ]
    with pytest.raises(IntegrityGateError) as excinfo:
        check_and_gate(results)
    assert excinfo.value.role == "manifest"


def test_check_and_gate_error_message_carries_all_four_fields() -> None:
    results = [check_anchor("manifest", "m.json", _OK_HASH, _OTHER_HASH)]
    with pytest.raises(IntegrityGateError) as excinfo:
        check_and_gate(results)
    message = str(excinfo.value)
    assert "manifest" in message
    assert "m.json" in message
    assert _OK_HASH in message
    assert _OTHER_HASH in message


def test_check_and_gate_is_deterministic_regardless_of_input_order() -> None:
    """No unordered iteration (task-packets/R2-T01.yaml invariant): the same
    set of results, given in either order, raises for the identical role.
    """
    ok = check_anchor("manifest", "m.json", _OK_HASH, _OK_HASH)
    bad = check_anchor("snapshot", "s.json", _OK_HASH, _OTHER_HASH)

    with pytest.raises(IntegrityGateError) as first:
        check_and_gate([ok, bad])
    with pytest.raises(IntegrityGateError) as second:
        check_and_gate([bad, ok])

    assert first.value.role == second.value.role == "snapshot"


# ---------------------------------------------------------------------------
# The closed sets (AGENTS.md prohibited shortcut).
# ---------------------------------------------------------------------------


def test_batch_roles_is_the_closed_set_of_exactly_two() -> None:
    assert BATCH_ROLES == ("manifest", "snapshot")


def test_anchor_check_result_status_is_one_of_the_two_closed_values() -> None:
    ok_result = check_anchor("manifest", "m.json", _OK_HASH, _OK_HASH)
    mismatch_result = check_anchor("manifest", "m.json", _OK_HASH, _OTHER_HASH)
    assert ok_result.status in ("anchor_ok", "anchor_mismatch")
    assert mismatch_result.status in ("anchor_ok", "anchor_mismatch")
    assert ok_result.status != mismatch_result.status


# ---------------------------------------------------------------------------
# ObservationBatch.inputs() — role-sorted, regardless of construction order.
# ---------------------------------------------------------------------------


def _batch() -> ObservationBatch:
    return ObservationBatch(
        schema_version="observation-batch.v1",
        batch_id="test-batch",
        observation_kind="citation_audit",
        audit_target="a test target",
        manifest=BatchInput(
            role="manifest", path="citations.manifest.json", declared_sha256=_OK_HASH
        ),
        snapshot=BatchInput(
            role="snapshot",
            path="verification/resolution-snapshot.json",
            declared_sha256=_OTHER_HASH,
        ),
    )


def test_observation_batch_inputs_are_ordered_manifest_then_snapshot() -> None:
    batch = _batch()
    ordered = batch.inputs()
    assert [item.role for item in ordered] == ["manifest", "snapshot"]


def test_observation_batch_inputs_returns_exactly_the_two_declared_fields() -> None:
    batch = _batch()
    ordered = batch.inputs()
    assert len(ordered) == 2
    assert ordered[0] is batch.manifest
    assert ordered[1] is batch.snapshot


# ---------------------------------------------------------------------------
# Dataclasses are frozen (AGENTS.md rule 12 spirit: no mutable domain state).
# ---------------------------------------------------------------------------


def test_batch_input_is_frozen() -> None:
    batch_input = BatchInput(role="manifest", path="m.json", declared_sha256=_OK_HASH)
    with pytest.raises(AttributeError):
        batch_input.path = "changed.json"  # type: ignore[misc]


def test_anchor_check_result_is_frozen() -> None:
    result = check_anchor("manifest", "m.json", _OK_HASH, _OK_HASH)
    with pytest.raises(AttributeError):
        result.status = "anchor_mismatch"  # type: ignore[misc]


def test_integrity_gate_error_is_not_reachable_without_a_mismatch() -> None:
    """Documents the invariant directly: constructing a clean
    :class:`AnchorCheckResult` set and gating it never raises — the only
    way to observe :class:`IntegrityGateError` is a real mismatch.
    """
    clean = [check_anchor(role, f"{role}.json", _OK_HASH, _OK_HASH) for role in BATCH_ROLES]
    check_and_gate(clean)  # must not raise
