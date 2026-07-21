"""Unit tests for MRR-MTH-018 sensitivity-variation execution
(task-packets/K1-T03b.yaml) on top of
``mrr.services.node_runtime.synthesis_executor.SystematicEvidenceSynthesisExecutor``.
Entirely DB-free, no PostgreSQL — mirrors
``tests/unit/services/node_runtime/test_synthesis_executor.py``'s own
fixture-builder style exactly, extended with the new
``sensitivity_variation_artifact_ids`` instructions key and
``SensitivityVariationParameters`` sidecar.

All fixtures here are SMALL, synthetic/sample corpus excerpts (2-6 entries,
mirroring ``test_synthesis_executor.py``'s own fixture scale) — NOT the real
atlases, and NEVER the real, sealed K1-T04 run (schema
``mrr_k1t04_real_run_v2``) — this file opens no PostgreSQL connection at all.

Acceptance-test mapping (task-packets/K1-T03b.yaml):

- "[unit, executor core]" ->
  ``test_declared_variation_with_coverage_yields_one_result_per_analysis``.
- "[unit, divergence]" ->
  ``test_source_family_collapse_flips_the_outcome_for_at_least_one_entry``.
- "[unit, coverage — missing]" ->
  ``test_coverage_check_raises_on_missing_variation_artifact``.
- "[unit, coverage — undeclared extra]" ->
  ``test_coverage_check_raises_on_undeclared_variation_artifact``.
- "[unit, empty declaration, regression]" ->
  ``test_empty_declaration_and_no_instructions_key_is_unchanged``.
- "[unit, empty-group guard]" ->
  ``test_empty_group_variation_yields_insufficient_evidence_no_indexerror``.
- "[unit, extraction reuse]" ->
  ``test_variation_never_reinvokes_the_extraction_callable``.
- "[unit, claim/ruling isolation]" ->
  ``test_sensitivity_variations_never_change_the_claim_minting_input``
  (structural proof at the executor tier: ``_persist_synthesis_output``'s
  own claim/ruling/decision minting loop reads ONLY ``output["analyses"]``,
  never ``output["sensitivity_analysis_results"]`` — see this packet's own
  PR body for the reinforcing integration-tier belt-and-braces check, which
  additionally drives the full DB-backed loop and compares real claim/
  ruling/decision id sets).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from mrr.contracts import TaskBundle
from mrr.domain.exceptions import SensitivityVariationDeclarationMismatchError
from mrr.domain.identity import new_urn
from mrr.services.node_runtime.synthesis_executor import (
    CAPABILITY_NAME,
    SystematicEvidenceSynthesisExecutor,
    _check_sensitivity_variation_coverage,
)

_VALID_HASH = "sha256:" + "a" * 64
_PROTOCOL_ID = "urn:mrr:method-protocol:01J00000000000000000000230"
_QUESTION_ID = "urn:mrr:question-model:01J00000000000000000000210"

_CORPUS_ARTIFACT_ID = new_urn("artifact")
_PROTOCOL_PARAMETERS_ARTIFACT_ID = new_urn("artifact")
_METHOD_PROTOCOL_ARTIFACT_ID = new_urn("artifact")
_VARIATION_A_ARTIFACT_ID = new_urn("artifact")
_VARIATION_B_ARTIFACT_ID = new_urn("artifact")


# ---------------------------------------------------------------------------
# Fixture builders — SMALL, synthetic/sample corpus excerpts (test fixture
# only, not the real atlases; see the module docstring).
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
) -> dict[str, Any]:
    return {
        "entry_id": entry_id,
        "applies_to_analysis": applies_to_analysis,
        "claim_type": claim_type,
        "evidence_relation": evidence_relation,
        "verification_status": verification_status,
        "unverifiable_reason": unverifiable_reason,
        "claim_relevant_finding": f"Finding for {entry_id}.",
        "extraction": {},
        "source_family_id": source_family_id,
        "title": f"Test fixture source {entry_id}",
        "creators": ["Test Fixture Author"],
        "retrieval_timestamp": "2026-07-21T09:00:00Z",
        "retrieval_method": "test-fixture-direct-read",
        "source_type": "test-fixture-artifact",
        "primary_secondary_derived": primary_secondary_derived,
    }


def _divergence_corpus() -> list[dict[str, Any]]:
    """The packet's own headline divergence fixture (derived_decisions (l)):
    two entries, ``entry-a``/``entry-b``, both supporting the SAME
    ``applies_to_analysis`` group ("candidate-x"), each from a DISTINCT
    ``source_family_id`` ("family-1"/"family-2"). Two verified, independent
    families clears a ``min_independent_source_families: 2`` "supported"
    floor in the base run. A variation's own ``source_family_overrides``
    collapses ``entry-b`` into ``entry-a``'s own family — now only ONE
    distinct family remains, below the "supported" floor but still at/above
    the "contested" floor of 1 on the supporting side alone with ZERO
    contradicting evidence, which ``_classify_analysis``'s own three-way
    branch resolves to "unsupported" (neither "supported" — insufficient
    family count — nor "contested" — no contradicting family at all) — a
    genuine, fixture-DESIGNED outcome flip, not a degenerate no-op.
    """
    return [
        _corpus_entry("entry-a", applies_to_analysis="candidate-x", source_family_id="family-1"),
        _corpus_entry("entry-b", applies_to_analysis="candidate-x", source_family_id="family-2"),
    ]


def _divergence_protocol_parameters() -> dict[str, Any]:
    return {
        "protocol_id": _PROTOCOL_ID,
        "protocol_lock_content_hash": _VALID_HASH,
        "inclusion_filter": {},
        "eligibility_rules": {
            "supported": {"min_independent_source_families": 2},
            "contested": {"min_independent_source_families": 1},
        },
        "kill_conditions": {"stop_insufficient_evidence": {"min_included_sources": 0}},
        "non_applicability_conditions": ["Divergence fixture non-applicability note."],
    }


def _variation_params(
    variation_entry_id: str,
    *,
    protocol_id: str = _PROTOCOL_ID,
    protocol_lock_content_hash: str = _VALID_HASH,
    inclusion_filter: dict[str, Any] | None = None,
    eligibility_rules: dict[str, Any] | None = None,
    min_included_sources: int = 0,
    source_family_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "protocol_id": protocol_id,
        "protocol_lock_content_hash": protocol_lock_content_hash,
        "variation_entry_id": variation_entry_id,
        "inclusion_filter": inclusion_filter or {},
        "eligibility_rules": eligibility_rules
        or {
            "supported": {"min_independent_source_families": 2},
            "contested": {"min_independent_source_families": 1},
        },
        "kill_conditions": {
            "stop_insufficient_evidence": {"min_included_sources": min_included_sources}
        },
        "source_family_overrides": source_family_overrides or {},
    }


def _method_protocol_body(
    *,
    protocol_id: str = _PROTOCOL_ID,
    content_hash: str = _VALID_HASH,
    status: str = "locked",
    sensitivity_variations: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": protocol_id,
        "content_hash": content_hash,
        "status": status,
        "extraction_fields": [],
        "sensitivity_variations": sensitivity_variations or [],
    }


def _bundle(*, timeout_seconds: int = 10, **instructions_overrides: Any) -> TaskBundle:
    now = datetime.now(UTC)
    instructions: dict[str, Any] = {
        "corpus_artifact_id": _CORPUS_ARTIFACT_ID,
        "protocol_parameters_artifact_id": _PROTOCOL_PARAMETERS_ARTIFACT_ID,
        "method_protocol_artifact_id": _METHOD_PROTOCOL_ARTIFACT_ID,
        "question_id": _QUESTION_ID,
    }
    instructions.update(instructions_overrides)
    inputs: list[dict[str, Any]] = [
        {"artifact_id": _CORPUS_ARTIFACT_ID, "content_hash": _VALID_HASH},
        {"artifact_id": _PROTOCOL_PARAMETERS_ARTIFACT_ID, "content_hash": _VALID_HASH},
        {"artifact_id": _METHOD_PROTOCOL_ARTIFACT_ID, "content_hash": _VALID_HASH},
    ]
    variation_ids: dict[str, str] = instructions.get("sensitivity_variation_artifact_ids", {})
    for artifact_id in variation_ids.values():
        inputs.append({"artifact_id": artifact_id, "content_hash": _VALID_HASH})
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
        "inputs": inputs,
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
    corpus: list[dict[str, Any]],
    params: dict[str, Any],
    protocol_body: dict[str, Any],
    variations: dict[str, dict[str, Any]] | None = None,
) -> dict[str, bytes]:
    resolved = {
        _CORPUS_ARTIFACT_ID: json.dumps(corpus).encode("utf-8"),
        _PROTOCOL_PARAMETERS_ARTIFACT_ID: json.dumps(params).encode("utf-8"),
        _METHOD_PROTOCOL_ARTIFACT_ID: json.dumps(protocol_body).encode("utf-8"),
    }
    if variations:
        for artifact_id, variation_body in variations.items():
            resolved[artifact_id] = json.dumps(variation_body).encode("utf-8")
    return resolved


# ---------------------------------------------------------------------------
# [unit, executor core]
# ---------------------------------------------------------------------------


def test_declared_variation_with_coverage_yields_one_result_per_analysis() -> None:
    corpus = [
        _corpus_entry(
            "entry-supported-1",
            applies_to_analysis="candidate-supported",
            source_family_id="family-supported-1",
        ),
        _corpus_entry(
            "entry-supported-2",
            applies_to_analysis="candidate-supported",
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
    ]
    params = {
        "protocol_id": _PROTOCOL_ID,
        "protocol_lock_content_hash": _VALID_HASH,
        "inclusion_filter": {},
        "eligibility_rules": {
            "supported": {"min_independent_source_families": 2},
            "contested": {"min_independent_source_families": 1},
        },
        "kill_conditions": {"stop_insufficient_evidence": {"min_included_sources": 0}},
        "non_applicability_conditions": ["Applies only to catalogued works."],
    }
    protocol_body = _method_protocol_body(sensitivity_variations=["variant-a"])
    # A deliberately identity/no-op variation (same shape as the base run's
    # own params) — the acceptance test's own concern is SHAPE (one result
    # per base analysis group, all required fields present), not divergence.
    variation_a = _variation_params("variant-a")

    bundle = _bundle(sensitivity_variation_artifact_ids={"variant-a": _VARIATION_A_ARTIFACT_ID})
    inputs = _inputs(
        corpus=corpus,
        params=params,
        protocol_body=protocol_body,
        variations={_VARIATION_A_ARTIFACT_ID: variation_a},
    )

    result = SystematicEvidenceSynthesisExecutor().execute(bundle, inputs, execution_attempt=1)

    assert result.outcome == "completed"
    assert result.output is not None
    output = json.loads(result.output.decode("utf-8"))
    base_analysis_keys = {a["applies_to_analysis"] for a in output["analyses"]}
    assert base_analysis_keys == {"candidate-supported", "candidate-contested"}

    sensitivity_results = output["sensitivity_analysis_results"]
    assert len(sensitivity_results) == len(base_analysis_keys)
    reported_keys = {r["applies_to_analysis"] for r in sensitivity_results}
    assert reported_keys == base_analysis_keys

    required_fields = {
        "variation_entry_id",
        "applies_to_analysis",
        "outcome",
        "included_source_count",
        "verified_source_count",
        "distinct_independent_supporting_family_count",
        "distinct_independent_contradicting_family_count",
        "decision_rationale",
        "matches_base_outcome",
    }
    for entry in sensitivity_results:
        assert set(entry) == required_fields
        assert entry["variation_entry_id"] == "variant-a"
        assert isinstance(entry["matches_base_outcome"], bool)


# ---------------------------------------------------------------------------
# [unit, divergence]
# ---------------------------------------------------------------------------


def test_source_family_collapse_flips_the_outcome_for_at_least_one_entry() -> None:
    corpus = _divergence_corpus()
    params = _divergence_protocol_parameters()
    protocol_body = _method_protocol_body(
        sensitivity_variations=["variant-collapse", "variant-noop"]
    )
    variation_collapse = _variation_params(
        "variant-collapse", source_family_overrides={"entry-b": "family-1"}
    )
    variation_noop = _variation_params("variant-noop")

    bundle = _bundle(
        sensitivity_variation_artifact_ids={
            "variant-collapse": _VARIATION_A_ARTIFACT_ID,
            "variant-noop": _VARIATION_B_ARTIFACT_ID,
        }
    )
    inputs = _inputs(
        corpus=corpus,
        params=params,
        protocol_body=protocol_body,
        variations={
            _VARIATION_A_ARTIFACT_ID: variation_collapse,
            _VARIATION_B_ARTIFACT_ID: variation_noop,
        },
    )

    result = SystematicEvidenceSynthesisExecutor().execute(bundle, inputs, execution_attempt=1)
    assert result.outcome == "completed"
    assert result.output is not None
    output = json.loads(result.output.decode("utf-8"))

    # Base run: two independent families clear the "supported" floor.
    base_analysis = output["analyses"][0]
    assert base_analysis["outcome"] == "supported"
    assert base_analysis["distinct_independent_supporting_family_count"] == 2

    by_variation = {
        entry["variation_entry_id"]: entry for entry in output["sensitivity_analysis_results"]
    }
    # At least two variations, structurally asserted (never a hardcoded
    # claim value) — one diverges, one does not.
    assert set(by_variation) == {"variant-collapse", "variant-noop"}
    assert any(entry["matches_base_outcome"] is False for entry in by_variation.values())

    collapsed = by_variation["variant-collapse"]
    assert collapsed["distinct_independent_supporting_family_count"] == 1
    assert collapsed["outcome"] != base_analysis["outcome"]
    assert collapsed["matches_base_outcome"] is False

    noop = by_variation["variant-noop"]
    assert noop["outcome"] == base_analysis["outcome"]
    assert noop["matches_base_outcome"] is True


# ---------------------------------------------------------------------------
# [unit, coverage — missing] / [unit, coverage — undeclared extra]
# ---------------------------------------------------------------------------


def test_coverage_check_raises_on_missing_variation_artifact() -> None:
    with pytest.raises(SensitivityVariationDeclarationMismatchError) as exc_info:
        _check_sensitivity_variation_coverage(
            {"id": _PROTOCOL_ID, "sensitivity_variations": ["variant-a", "variant-b"]},
            {"sensitivity_variation_artifact_ids": {"variant-a": _VARIATION_A_ARTIFACT_ID}},
        )
    assert exc_info.value.declared == frozenset({"variant-a", "variant-b"})
    assert exc_info.value.supplied == frozenset({"variant-a"})


def test_execute_reports_missing_variation_coverage_as_a_failed_outcome() -> None:
    corpus = _divergence_corpus()
    params = _divergence_protocol_parameters()
    protocol_body = _method_protocol_body(
        sensitivity_variations=["variant-collapse", "variant-noop"]
    )
    variation_collapse = _variation_params(
        "variant-collapse", source_family_overrides={"entry-b": "family-1"}
    )

    # Only "variant-collapse" is covered; "variant-noop" is declared but
    # missing.
    bundle = _bundle(
        sensitivity_variation_artifact_ids={"variant-collapse": _VARIATION_A_ARTIFACT_ID}
    )
    inputs = _inputs(
        corpus=corpus,
        params=params,
        protocol_body=protocol_body,
        variations={_VARIATION_A_ARTIFACT_ID: variation_collapse},
    )

    result = SystematicEvidenceSynthesisExecutor().execute(bundle, inputs, execution_attempt=1)

    assert result.outcome == "failed"
    assert result.output is None
    assert "SensitivityVariationDeclarationMismatchError" in (result.detail or "")


def test_coverage_check_raises_on_undeclared_variation_artifact() -> None:
    with pytest.raises(SensitivityVariationDeclarationMismatchError) as exc_info:
        _check_sensitivity_variation_coverage(
            {"id": _PROTOCOL_ID, "sensitivity_variations": ["variant-a"]},
            {
                "sensitivity_variation_artifact_ids": {
                    "variant-a": _VARIATION_A_ARTIFACT_ID,
                    "variant-undeclared": _VARIATION_B_ARTIFACT_ID,
                }
            },
        )
    assert exc_info.value.declared == frozenset({"variant-a"})
    assert exc_info.value.supplied == frozenset({"variant-a", "variant-undeclared"})


# ---------------------------------------------------------------------------
# [unit, empty declaration, regression]
# ---------------------------------------------------------------------------


def test_empty_declaration_and_no_instructions_key_is_unchanged() -> None:
    corpus = _divergence_corpus()
    params = _divergence_protocol_parameters()
    protocol_body = _method_protocol_body(sensitivity_variations=[])

    bundle = _bundle()  # no sensitivity_variation_artifact_ids key at all
    inputs = _inputs(corpus=corpus, params=params, protocol_body=protocol_body)

    result = SystematicEvidenceSynthesisExecutor().execute(bundle, inputs, execution_attempt=1)

    assert result.outcome == "completed"
    assert result.output is not None
    output = json.loads(result.output.decode("utf-8"))
    assert output.get("sensitivity_analysis_results") in (None, [])


# ---------------------------------------------------------------------------
# [unit, empty-group guard]
# ---------------------------------------------------------------------------


def test_empty_group_variation_yields_insufficient_evidence_no_indexerror() -> None:
    corpus = [
        _corpus_entry(
            "entry-only", applies_to_analysis="candidate-only", source_family_id="family-only"
        )
    ]
    params: dict[str, Any] = {
        "protocol_id": _PROTOCOL_ID,
        "protocol_lock_content_hash": _VALID_HASH,
        "inclusion_filter": {},
        "eligibility_rules": {
            "supported": {"min_independent_source_families": 1},
            "contested": {"min_independent_source_families": 1},
        },
        "kill_conditions": {"stop_insufficient_evidence": {"min_included_sources": 0}},
        "non_applicability_conditions": ["note"],
    }
    protocol_body = _method_protocol_body(sensitivity_variations=["variant-empties"])
    # A stricter inclusion_filter that no entry in the corpus can satisfy —
    # empties the group entirely — combined with min_included_sources == 0,
    # the ONLY circumstance under which the base path's own unsafe
    # group_rows[0] access would be reachable (derived_decisions (g)).
    variation_empties = _variation_params(
        "variant-empties",
        inclusion_filter={"primary_secondary_derived": {"allowed_values": ["derived"]}},
        eligibility_rules=params["eligibility_rules"],
        min_included_sources=0,
    )

    bundle = _bundle(
        sensitivity_variation_artifact_ids={"variant-empties": _VARIATION_A_ARTIFACT_ID}
    )
    inputs = _inputs(
        corpus=corpus,
        params=params,
        protocol_body=protocol_body,
        variations={_VARIATION_A_ARTIFACT_ID: variation_empties},
    )

    # Must not raise IndexError — execute() itself never raises regardless
    # (outer exception handling), so the precise assertion is on the
    # REPORTED outcome, not on the absence of a Python exception escaping.
    result = SystematicEvidenceSynthesisExecutor().execute(bundle, inputs, execution_attempt=1)

    assert result.outcome == "completed"
    assert result.output is not None
    output = json.loads(result.output.decode("utf-8"))
    sensitivity_results = output["sensitivity_analysis_results"]
    assert len(sensitivity_results) == 1
    entry = sensitivity_results[0]
    assert entry["applies_to_analysis"] == "candidate-only"
    assert entry["outcome"] == "insufficient_evidence"
    assert entry["included_source_count"] == 0
    assert entry["decision_rationale"] is not None
    assert "variant-empties" in entry["decision_rationale"]


# ---------------------------------------------------------------------------
# [unit, extraction reuse]
# ---------------------------------------------------------------------------


def test_variation_never_reinvokes_the_extraction_callable() -> None:
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

    corpus = _divergence_corpus()
    params = _divergence_protocol_parameters()

    # Baseline: zero declared variations.
    protocol_body_no_variation = _method_protocol_body(sensitivity_variations=[])
    bundle_no_variation = _bundle()
    inputs_no_variation = _inputs(
        corpus=corpus, params=params, protocol_body=protocol_body_no_variation
    )
    executor_no_variation = SystematicEvidenceSynthesisExecutor(
        extraction_callable=_counting_callable
    )
    executor_no_variation.execute(bundle_no_variation, inputs_no_variation, execution_attempt=1)
    calls_with_zero_variations = calls
    assert calls_with_zero_variations == len(corpus)  # once per included base row

    # Two declared variations over the SAME corpus.
    calls = 0
    protocol_body_with_variations = _method_protocol_body(
        sensitivity_variations=["variant-collapse", "variant-noop"]
    )
    variation_collapse = _variation_params(
        "variant-collapse", source_family_overrides={"entry-b": "family-1"}
    )
    variation_noop = _variation_params("variant-noop")
    bundle_with_variations = _bundle(
        sensitivity_variation_artifact_ids={
            "variant-collapse": _VARIATION_A_ARTIFACT_ID,
            "variant-noop": _VARIATION_B_ARTIFACT_ID,
        }
    )
    inputs_with_variations = _inputs(
        corpus=corpus,
        params=params,
        protocol_body=protocol_body_with_variations,
        variations={
            _VARIATION_A_ARTIFACT_ID: variation_collapse,
            _VARIATION_B_ARTIFACT_ID: variation_noop,
        },
    )
    executor_with_variations = SystematicEvidenceSynthesisExecutor(
        extraction_callable=_counting_callable
    )
    result = executor_with_variations.execute(
        bundle_with_variations, inputs_with_variations, execution_attempt=1
    )

    assert result.outcome == "completed"
    # NOT multiplied by the number of declared variations (2): still exactly
    # once per included base row.
    assert calls == calls_with_zero_variations


# ---------------------------------------------------------------------------
# [unit, claim/ruling isolation] — structural proof at the executor tier.
# ---------------------------------------------------------------------------


def test_sensitivity_variations_never_change_the_claim_minting_input() -> None:
    """derived_decisions (e): a variation never mints, modifies, or
    supersedes a Claim/MethodRuling/ResearchDecision.
    ``synthesis_orchestration._persist_synthesis_output``'s own claim/
    ruling/decision minting loop iterates ONLY ``output["analyses"]``
    (untouched by this packet) — proven directly here: that key is
    byte-identical between an otherwise-identical run with and without
    declared sensitivity variations, so it is structurally impossible for a
    variation's own outcome to influence claim minting.
    """
    corpus = _divergence_corpus()
    params = _divergence_protocol_parameters()

    protocol_body_no_variation = _method_protocol_body(sensitivity_variations=[])
    bundle_no_variation = _bundle()
    inputs_no_variation = _inputs(
        corpus=corpus, params=params, protocol_body=protocol_body_no_variation
    )
    result_no_variation = SystematicEvidenceSynthesisExecutor().execute(
        bundle_no_variation, inputs_no_variation, execution_attempt=1
    )

    protocol_body_with_variation = _method_protocol_body(
        sensitivity_variations=["variant-collapse"]
    )
    variation_collapse = _variation_params(
        "variant-collapse", source_family_overrides={"entry-b": "family-1"}
    )
    bundle_with_variation = _bundle(
        sensitivity_variation_artifact_ids={"variant-collapse": _VARIATION_A_ARTIFACT_ID}
    )
    inputs_with_variation = _inputs(
        corpus=corpus,
        params=params,
        protocol_body=protocol_body_with_variation,
        variations={_VARIATION_A_ARTIFACT_ID: variation_collapse},
    )
    result_with_variation = SystematicEvidenceSynthesisExecutor().execute(
        bundle_with_variation, inputs_with_variation, execution_attempt=1
    )

    assert result_no_variation.output is not None
    assert result_with_variation.output is not None
    output_no_variation = json.loads(result_no_variation.output.decode("utf-8"))
    output_with_variation = json.loads(result_with_variation.output.decode("utf-8"))

    assert output_with_variation["analyses"] == output_no_variation["analyses"]
    # The variation genuinely ran and diverged — this isn't vacuously true.
    assert output_with_variation["sensitivity_analysis_results"]
    assert any(
        entry["matches_base_outcome"] is False
        for entry in output_with_variation["sensitivity_analysis_results"]
    )
