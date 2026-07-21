"""Unit tests for
``mrr.services.node_runtime.synthesis_executor.SystematicEvidenceSynthesisExecutor``
(task-packets/K1-T03.yaml) — entirely DB-free, no PostgreSQL,
no ``sqlalchemy.Engine``: ``execute()`` is a pure, in-process transform over
already-resolved ``inputs: Mapping[str, bytes]``, exactly like
``mrr.services.node_runtime.executor.ReferenceTaskExecutor``.

All fixtures here are SMALL, synthetic/sample corpus excerpts (a handful of
entries shaped like the atlas records) — NOT the real atlases (that is
task-packets/K1-T04.yaml's job). Marked clearly as test fixtures throughout.

Acceptance-test mapping (task-packets/K1-T03.yaml):

- "[model-free end-to-end, the packet's headline proof]" ->
  ``test_model_free_end_to_end_produces_supported_and_contested_candidates``.
- "[MTH-007, first real enforcement]" ->
  ``test_check_protocol_lock_raises_protocol_not_locked_error``,
  ``test_check_protocol_lock_raises_protocol_lock_violation_error``,
  ``test_execute_reports_protocol_not_locked_as_a_failed_outcome``.
- "[MRR-FR-035 idempotency]" -> ``test_idempotent_same_triple_returns_memoized_result``,
  ``test_idempotency_never_reinvokes_injected_extraction_callable``.
- "[insufficient evidence, MRR-MTH-011]" is exercised at the ORCHESTRATION
  tier (tests/e2e/test_k1_t03_synthesis_evidence_loop.py) since it is the
  FULL loop's own acceptance test; this file additionally pins the
  executor-only half directly:
  ``test_insufficient_evidence_is_a_completed_outcome_with_no_claim_candidate``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from mrr.contracts import TaskBundle
from mrr.domain.exceptions import ProtocolLockViolationError, ProtocolNotLockedError
from mrr.domain.identity import new_urn
from mrr.services.node_runtime.synthesis_executor import (
    CAPABILITY_NAME,
    RULED_CEILING,
    ProtocolParameters,
    SystematicEvidenceSynthesisExecutor,
    _check_protocol_lock,
)

_VALID_HASH = "sha256:" + "a" * 64
_PROTOCOL_ID = "urn:mrr:method-protocol:01J00000000000000000000230"
_QUESTION_ID = "urn:mrr:question-model:01J00000000000000000000210"

_CORPUS_ARTIFACT_ID = new_urn("artifact")
_PROTOCOL_PARAMETERS_ARTIFACT_ID = new_urn("artifact")
_METHOD_PROTOCOL_ARTIFACT_ID = new_urn("artifact")


# ---------------------------------------------------------------------------
# Fixture builders — a SMALL, synthetic/sample corpus (test fixture only, not
# the real atlases; see the module docstring).
# ---------------------------------------------------------------------------


def _corpus_entry(
    entry_id: str,
    *,
    applies_to_analysis: str,
    claim_type: str = "interpretive",
    evidence_relation: str = "supports",
    verification_status: str = "verified",
    unverifiable_reason: str | None = None,
    source_family_id: str | None = None,
    primary_secondary_derived: str = "primary",
    claim_relevant_finding: str = "A finding relevant to this analysis.",
) -> dict[str, Any]:
    return {
        "entry_id": entry_id,
        "applies_to_analysis": applies_to_analysis,
        "claim_type": claim_type,
        "evidence_relation": evidence_relation,
        "verification_status": verification_status,
        "unverifiable_reason": unverifiable_reason,
        "claim_relevant_finding": claim_relevant_finding,
        "extraction": {},
        "source_family_id": source_family_id,
        "title": f"Test fixture source {entry_id}",
        "creators": ["Test Fixture Author"],
        "retrieval_timestamp": "2026-07-21T09:00:00Z",
        "retrieval_method": "test-fixture-direct-read",
        "source_type": "test-fixture-artifact",
        "primary_secondary_derived": primary_secondary_derived,
    }


def _headline_corpus() -> list[dict[str, Any]]:
    """The packet's own headline acceptance-test fixture: 2 sources clearing
    the "supported" floor with no contradiction; 2 sources for a "contested"
    candidate (supporting AND contradicting); 1 unverifiable source; 1
    source excluded by the inclusion filter. Six entries total.
    """
    return [
        _corpus_entry(
            "entry-supported-1",
            applies_to_analysis="candidate-supported",
            evidence_relation="supports",
            source_family_id="family-supported-1",
        ),
        _corpus_entry(
            "entry-supported-2",
            applies_to_analysis="candidate-supported",
            evidence_relation="supports",
            source_family_id="family-supported-2",
        ),
        _corpus_entry(
            "entry-contested-support",
            applies_to_analysis="candidate-contested",
            evidence_relation="supports",
            source_family_id="family-contested-a",
        ),
        _corpus_entry(
            "entry-contested-contradict",
            applies_to_analysis="candidate-contested",
            evidence_relation="contradicts",
            source_family_id="family-contested-b",
        ),
        _corpus_entry(
            "entry-unverifiable",
            applies_to_analysis="candidate-supported",
            verification_status="unverifiable",
            unverifiable_reason="training provenance could not be confirmed",
        ),
        _corpus_entry(
            "entry-excluded",
            applies_to_analysis="candidate-supported",
            primary_secondary_derived="derived",
        ),
    ]


def _protocol_parameters(
    *,
    protocol_id: str = _PROTOCOL_ID,
    protocol_lock_content_hash: str = _VALID_HASH,
    min_included_sources: int = 2,
) -> dict[str, Any]:
    return {
        "protocol_id": protocol_id,
        "protocol_lock_content_hash": protocol_lock_content_hash,
        "inclusion_filter": {
            "primary_secondary_derived": {"allowed_values": ["primary", "secondary"]}
        },
        "eligibility_rules": {
            "supported": {"min_independent_source_families": 2},
            "contested": {"min_independent_source_families": 1},
        },
        "kill_conditions": {
            "stop_insufficient_evidence": {"min_included_sources": min_included_sources}
        },
        "non_applicability_conditions": [
            "Applies only to catalogued works with disclosed training provenance."
        ],
    }


def _method_protocol_body(
    *, protocol_id: str = _PROTOCOL_ID, content_hash: str = _VALID_HASH, status: str = "locked"
) -> dict[str, Any]:
    return {
        "id": protocol_id,
        "content_hash": content_hash,
        "status": status,
        "extraction_fields": ["sample_size", "methodology_notes"],
    }


def _bundle(*, timeout_seconds: int = 10, **instructions_overrides: Any) -> TaskBundle:
    now = datetime.now(UTC)
    instructions = {
        "corpus_artifact_id": _CORPUS_ARTIFACT_ID,
        "protocol_parameters_artifact_id": _PROTOCOL_PARAMETERS_ARTIFACT_ID,
        "method_protocol_artifact_id": _METHOD_PROTOCOL_ARTIFACT_ID,
        "question_id": _QUESTION_ID,
    }
    instructions.update(instructions_overrides)
    data: dict[str, Any] = {
        "id": new_urn("task-bundle"),
        "api_version": "mrr/v1alpha1",
        "kind": "TaskBundle",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": now,
        "created_by": new_urn("agent-role"),
        "content_hash": _VALID_HASH,
        "origin_practice_id": new_urn("practice"),
        "target_node_id": new_urn("node"),
        "research_score_id": new_urn("research-score"),
        "research_score_revision": 1,
        "branch_id": new_urn("branch"),
        "capability": {"name": CAPABILITY_NAME, "version": "1.0.0"},
        "purpose": "Run the systematic_evidence_synthesis v1 executor task family.",
        "instructions": instructions,
        "inputs": [
            {"artifact_id": _CORPUS_ARTIFACT_ID, "content_hash": _VALID_HASH},
            {"artifact_id": _PROTOCOL_PARAMETERS_ARTIFACT_ID, "content_hash": _VALID_HASH},
            {"artifact_id": _METHOD_PROTOCOL_ARTIFACT_ID, "content_hash": _VALID_HASH},
        ],
        "data_access_mode": "read_local",
        "execution": {"image_digest": "sha256:" + "c" * 64, "entrypoint": ["run.sh"]},
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
    return TaskBundle.model_validate(data)


def _inputs(
    *,
    corpus: list[dict[str, Any]] | None = None,
    params: dict[str, Any] | None = None,
    protocol_body: dict[str, Any] | None = None,
) -> dict[str, bytes]:
    return {
        _CORPUS_ARTIFACT_ID: json.dumps(
            corpus if corpus is not None else _headline_corpus()
        ).encode("utf-8"),
        _PROTOCOL_PARAMETERS_ARTIFACT_ID: json.dumps(
            params if params is not None else _protocol_parameters()
        ).encode("utf-8"),
        _METHOD_PROTOCOL_ARTIFACT_ID: json.dumps(
            protocol_body if protocol_body is not None else _method_protocol_body()
        ).encode("utf-8"),
    }


# ---------------------------------------------------------------------------
# [model-free end-to-end, the packet's headline proof]
# ---------------------------------------------------------------------------


def test_model_free_end_to_end_produces_supported_and_contested_candidates() -> None:
    executor = SystematicEvidenceSynthesisExecutor()
    bundle = _bundle()

    result = executor.execute(bundle, _inputs(), execution_attempt=1)

    assert result.outcome == "completed"
    assert result.is_deterministic is True
    assert result.output is not None
    output = json.loads(result.output.decode("utf-8"))

    analyses = {a["applies_to_analysis"]: a for a in output["analyses"]}
    assert set(analyses) == {"candidate-supported", "candidate-contested"}

    supported = analyses["candidate-supported"]
    assert supported["outcome"] == "supported"
    assert supported["claim_candidate"]["ruled_ceiling"] == RULED_CEILING
    assert supported["decision"] is None

    contested = analyses["candidate-contested"]
    assert contested["outcome"] == "contested"
    assert contested["claim_candidate"]["ruled_ceiling"] == RULED_CEILING

    # The excluded source is absent from every claim candidate's own
    # evidence lists but present in the matrix-row output (corpus_rows).
    corpus_rows_by_id = {row["entry_id"]: row for row in output["corpus_rows"]}
    assert corpus_rows_by_id["entry-excluded"]["included"] is False
    assert "entry-excluded" not in supported["claim_candidate"]["supporting_entry_ids"]

    # The unverifiable source is present with a non-null unverifiable_reason
    # and contributes nothing to any independence count.
    unverifiable_row = corpus_rows_by_id["entry-unverifiable"]
    assert unverifiable_row["included"] is True
    assert unverifiable_row["verification_status"] == "unverifiable"
    assert unverifiable_row["unverifiable_reason"] is not None
    assert supported["distinct_independent_supporting_family_count"] == 2


def test_output_hash_is_content_hash_of_output() -> None:
    from mrr.crypto.hashing import content_hash

    executor = SystematicEvidenceSynthesisExecutor()
    result = executor.execute(_bundle(), _inputs(), execution_attempt=1)

    assert result.output is not None
    assert result.output_hash == content_hash(result.output)


def test_two_calls_with_identical_inputs_produce_byte_identical_output() -> None:
    """Determinism: no randomly-minted id anywhere in execute()'s own output
    (see the module docstring's "Why execute() never mints an id").
    """
    first = SystematicEvidenceSynthesisExecutor().execute(_bundle(), _inputs(), execution_attempt=1)
    second = SystematicEvidenceSynthesisExecutor().execute(
        _bundle(), _inputs(), execution_attempt=1
    )

    assert first.output == second.output
    assert first.output_hash == second.output_hash


# ---------------------------------------------------------------------------
# [MTH-007, first real enforcement]
# ---------------------------------------------------------------------------


def test_check_protocol_lock_raises_protocol_not_locked_error() -> None:
    params = ProtocolParameters.model_validate(_protocol_parameters())
    protocol_body = _method_protocol_body(status="reviewed")

    with pytest.raises(ProtocolNotLockedError) as exc_info:
        _check_protocol_lock(protocol_body, params)

    assert exc_info.value.error_code == "PROTOCOL_NOT_LOCKED"


@pytest.mark.parametrize("status", ["locked", "amended", "executed"])
def test_check_protocol_lock_accepts_every_lock_satisfying_status(status: str) -> None:
    params = ProtocolParameters.model_validate(_protocol_parameters())
    protocol_body = _method_protocol_body(status=status)

    _check_protocol_lock(protocol_body, params)  # must not raise


def test_check_protocol_lock_raises_protocol_lock_violation_error() -> None:
    params = ProtocolParameters.model_validate(
        _protocol_parameters(protocol_lock_content_hash=_VALID_HASH)
    )
    stale_protocol_body = _method_protocol_body(content_hash="sha256:" + "f" * 64)

    with pytest.raises(ProtocolLockViolationError) as exc_info:
        _check_protocol_lock(stale_protocol_body, params)

    assert exc_info.value.error_code == "PROTOCOL_LOCK_VIOLATION"


def test_execute_reports_protocol_not_locked_as_a_failed_outcome() -> None:
    """execute() never raises (the Executor Protocol's own "never raise for
    a task-level outcome" contract) — the internal precondition raise is
    caught and reported as an explicit `failed` ExecutionResult, mirroring
    ReferenceTaskExecutor's own "any raising computation -> failed"
    precedent.
    """
    executor = SystematicEvidenceSynthesisExecutor()
    bundle = _bundle()
    inputs = _inputs(protocol_body=_method_protocol_body(status="draft"))

    result = executor.execute(bundle, inputs, execution_attempt=1)

    assert result.outcome == "failed"
    assert result.output is None
    assert result.is_deterministic is True
    assert "ProtocolNotLockedError" in (result.detail or "")


def test_execute_reports_protocol_lock_violation_as_a_failed_outcome() -> None:
    executor = SystematicEvidenceSynthesisExecutor()
    bundle = _bundle()
    inputs = _inputs(protocol_body=_method_protocol_body(content_hash="sha256:" + "9" * 64))

    result = executor.execute(bundle, inputs, execution_attempt=1)

    assert result.outcome == "failed"
    assert result.output is None
    assert "ProtocolLockViolationError" in (result.detail or "")


# ---------------------------------------------------------------------------
# [insufficient evidence, MRR-MTH-011] — executor-only half.
# ---------------------------------------------------------------------------


def test_insufficient_evidence_is_a_completed_outcome_with_no_claim_candidate() -> None:
    corpus = [
        _corpus_entry(
            "entry-only-one",
            applies_to_analysis="candidate-thin",
            source_family_id="family-thin-1",
        )
    ]
    params = _protocol_parameters(min_included_sources=5)
    executor = SystematicEvidenceSynthesisExecutor()

    result = executor.execute(_bundle(), _inputs(corpus=corpus, params=params), execution_attempt=1)

    assert result.outcome == "completed"  # NEVER "failed" for insufficient evidence (MRR-MTH-011)
    assert result.output is not None
    output = json.loads(result.output.decode("utf-8"))
    analysis = output["analyses"][0]
    assert analysis["outcome"] == "insufficient_evidence"
    assert analysis["claim_candidate"] is None
    assert analysis["decision"]["decision_type"] == "stop_insufficient_evidence"
    assert "1 included source" in analysis["decision"]["rationale"]
    assert "5" in analysis["decision"]["rationale"]


# ---------------------------------------------------------------------------
# [MRR-FR-035 idempotency]
# ---------------------------------------------------------------------------


def test_idempotent_same_triple_returns_memoized_result() -> None:
    executor = SystematicEvidenceSynthesisExecutor()
    bundle = _bundle()
    inputs = _inputs()

    first = executor.execute(bundle, inputs, execution_attempt=1)
    second = executor.execute(bundle, inputs, execution_attempt=1)

    assert first is second


def test_idempotency_never_reinvokes_injected_extraction_callable() -> None:
    from mrr.services.node_runtime.synthesis_executor import CorpusEntry, ExtractionOutcome

    calls = 0

    def _counting_callable(
        entry: CorpusEntry, extraction_fields: Sequence[str]
    ) -> ExtractionOutcome:
        nonlocal calls
        calls += 1
        return ExtractionOutcome(
            extraction=dict(entry.extraction), verification_disposition="verified"
        )

    executor = SystematicEvidenceSynthesisExecutor(extraction_callable=_counting_callable)
    bundle = _bundle()
    inputs = _inputs()

    executor.execute(bundle, inputs, execution_attempt=1)
    calls_after_first = calls
    executor.execute(bundle, inputs, execution_attempt=1)

    assert calls_after_first > 0
    assert calls == calls_after_first  # not re-invoked on the memoized second call


def test_a_different_execution_attempt_is_a_fresh_run() -> None:
    executor = SystematicEvidenceSynthesisExecutor()
    bundle = _bundle()
    inputs = _inputs()

    first = executor.execute(bundle, inputs, execution_attempt=1)
    second = executor.execute(bundle, inputs, execution_attempt=2)

    assert first is not second
    assert first.output == second.output  # same deterministic content either way


def test_execute_rejects_non_positive_execution_attempt() -> None:
    executor = SystematicEvidenceSynthesisExecutor()
    with pytest.raises(ValueError, match="execution_attempt"):
        executor.execute(_bundle(), _inputs(), execution_attempt=0)


# ---------------------------------------------------------------------------
# Policy gate / cancellation — mirrors ReferenceTaskExecutor's own tests.
# ---------------------------------------------------------------------------


def test_policy_gate_denial_produces_policy_denied_outcome() -> None:
    executor = SystematicEvidenceSynthesisExecutor(policy_gate=lambda _bundle: False)
    result = executor.execute(_bundle(), _inputs(), execution_attempt=1)
    assert result.outcome == "policy_denied"
    assert result.output is None


def test_cancellation_check_produces_cancelled_outcome() -> None:
    executor = SystematicEvidenceSynthesisExecutor(is_cancelled=lambda: True)
    result = executor.execute(_bundle(), _inputs(), execution_attempt=1)
    assert result.outcome == "cancelled"
    assert result.output is None


def test_missing_declared_artifact_produces_failed_outcome() -> None:
    executor = SystematicEvidenceSynthesisExecutor()
    bundle = _bundle()
    incomplete_inputs = _inputs()
    del incomplete_inputs[_METHOD_PROTOCOL_ARTIFACT_ID]

    result = executor.execute(bundle, incomplete_inputs, execution_attempt=1)

    assert result.outcome == "failed"
    assert result.output is None


def test_executor_does_not_provide_untrusted_isolation() -> None:
    assert SystematicEvidenceSynthesisExecutor.provides_untrusted_isolation is False
