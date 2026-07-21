"""Property tests for MRR-MTH-018 sensitivity-variation execution
(task-packets/K1-T03b.yaml) on top of
``mrr.services.node_runtime.synthesis_executor.SystematicEvidenceSynthesisExecutor``
— extends ``tests/property/test_synthesis_executor_properties.py``'s own
discipline (small, synthetic corpora, hypothesis-driven) to the new
variation-execution code path.

All generated corpora are small (2-6 entries) — synthetic test fixtures,
never the real atlases, never the real, sealed K1-T04 run.

Acceptance-test mapping (task-packets/K1-T03b.yaml):

- "[property, determinism]" ->
  ``test_two_calls_with_identical_inputs_including_variations_produce_identical_output_hash``.
- "[property, ceiling discipline regression]" ->
  ``test_every_eligible_claim_candidate_keeps_its_ruled_ceiling_when_variations_are_present``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from hypothesis import given
from hypothesis import strategies as st
from mrr.contracts import TaskBundle
from mrr.domain.identity import new_urn
from mrr.services.node_runtime.synthesis_executor import (
    CAPABILITY_NAME,
    RULED_CEILING,
    SystematicEvidenceSynthesisExecutor,
)

_VALID_HASH = "sha256:" + "b" * 64
_CORPUS_ARTIFACT_ID = new_urn("artifact")
_PROTOCOL_PARAMETERS_ARTIFACT_ID = new_urn("artifact")
_METHOD_PROTOCOL_ARTIFACT_ID = new_urn("artifact")
_QUESTION_ID = new_urn("question-model")
_PROTOCOL_ID = new_urn("method-protocol")

_VARIATION_ENTRY_IDS = ["variant-1", "variant-2", "variant-3"]
_VARIATION_ARTIFACT_IDS = {
    variation_entry_id: new_urn("artifact") for variation_entry_id in _VARIATION_ENTRY_IDS
}


def _bundle(*, sensitivity_variation_entry_ids: list[str], timeout_seconds: int = 10) -> TaskBundle:
    now = datetime.now(UTC)
    instructions: dict[str, Any] = {
        "corpus_artifact_id": _CORPUS_ARTIFACT_ID,
        "protocol_parameters_artifact_id": _PROTOCOL_PARAMETERS_ARTIFACT_ID,
        "method_protocol_artifact_id": _METHOD_PROTOCOL_ARTIFACT_ID,
        "question_id": _QUESTION_ID,
    }
    inputs = [
        {"artifact_id": _CORPUS_ARTIFACT_ID, "content_hash": _VALID_HASH},
        {"artifact_id": _PROTOCOL_PARAMETERS_ARTIFACT_ID, "content_hash": _VALID_HASH},
        {"artifact_id": _METHOD_PROTOCOL_ARTIFACT_ID, "content_hash": _VALID_HASH},
    ]
    if sensitivity_variation_entry_ids:
        artifact_id_map = {
            entry_id: _VARIATION_ARTIFACT_IDS[entry_id]
            for entry_id in sensitivity_variation_entry_ids
        }
        instructions["sensitivity_variation_artifact_ids"] = artifact_id_map
        for artifact_id in artifact_id_map.values():
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
        "purpose": "Property test fixture (K1-T03b sensitivity variations).",
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


#: One randomly-generated corpus entry within a single, shared
#: applies_to_analysis group — mirrors test_synthesis_executor_properties.py's
#: own strategy.
_entry_strategy = st.fixed_dictionaries(
    {
        "evidence_relation": st.sampled_from(["supports", "contradicts"]),
        "verification_status": st.sampled_from(["verified", "unverifiable", "pending"]),
        "source_family_id": st.one_of(st.none(), st.sampled_from(["fam-1", "fam-2", "fam-3"])),
    }
)

#: One randomly-generated variation: its own randomized
#: source_family_overrides (a subset of the corpus's own entry ids,
#: remapped to one of a small set of alternate family labels) and its own
#: randomized eligibility thresholds.
_variation_strategy = st.fixed_dictionaries(
    {
        "supported_threshold": st.integers(min_value=1, max_value=3),
        "contested_threshold": st.integers(min_value=1, max_value=3),
        "min_included_sources": st.integers(min_value=0, max_value=3),
        "override_family": st.sampled_from(["fam-1", "fam-2", "fam-3", "fam-4"]),
        "override_entry_indices": st.lists(st.integers(min_value=0, max_value=5), max_size=3),
    }
)


def _build_corpus(entries: list[dict[str, Any]], claim_type: str) -> list[dict[str, Any]]:
    corpus = []
    for index, entry in enumerate(entries):
        unverifiable_reason = (
            "provenance unresolved" if entry["verification_status"] == "unverifiable" else None
        )
        corpus.append(
            {
                "entry_id": f"entry-{index}",
                "applies_to_analysis": "candidate-a",
                "claim_type": claim_type,
                "evidence_relation": entry["evidence_relation"],
                "verification_status": entry["verification_status"],
                "unverifiable_reason": unverifiable_reason,
                "claim_relevant_finding": f"Finding {index}.",
                "extraction": {},
                "source_family_id": entry["source_family_id"],
                "title": f"Property fixture source {index}",
                "creators": [],
                "retrieval_timestamp": "2026-07-21T09:00:00Z",
                "retrieval_method": "property-test-fixture",
                "source_type": "property-test-fixture",
                "primary_secondary_derived": "primary",
            }
        )
    return corpus


def _build_protocol_params(
    supported_threshold: int, contested_threshold: int, min_included_sources: int
) -> dict[str, Any]:
    return {
        "protocol_id": _PROTOCOL_ID,
        "protocol_lock_content_hash": _VALID_HASH,
        "inclusion_filter": {},
        "eligibility_rules": {
            "supported": {"min_independent_source_families": supported_threshold},
            "contested": {"min_independent_source_families": contested_threshold},
        },
        "kill_conditions": {
            "stop_insufficient_evidence": {"min_included_sources": min_included_sources}
        },
        "non_applicability_conditions": ["Property test fixture non-applicability note."],
    }


def _build_variations(
    variation_specs: list[dict[str, Any]], corpus: list[dict[str, Any]]
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Returns ``(artifact_id -> variation body, [variation_entry_id, ...])``."""
    corpus_entry_ids = [entry["entry_id"] for entry in corpus]
    bodies: dict[str, dict[str, Any]] = {}
    variation_entry_ids: list[str] = []
    for index, spec in enumerate(variation_specs):
        variation_entry_id = _VARIATION_ENTRY_IDS[index]
        overrides = {
            corpus_entry_ids[i % len(corpus_entry_ids)]: spec["override_family"]
            for i in spec["override_entry_indices"]
            if corpus_entry_ids
        }
        body = {
            "protocol_id": _PROTOCOL_ID,
            "protocol_lock_content_hash": _VALID_HASH,
            "variation_entry_id": variation_entry_id,
            "inclusion_filter": {},
            "eligibility_rules": {
                "supported": {"min_independent_source_families": spec["supported_threshold"]},
                "contested": {"min_independent_source_families": spec["contested_threshold"]},
            },
            "kill_conditions": {
                "stop_insufficient_evidence": {"min_included_sources": spec["min_included_sources"]}
            },
            "source_family_overrides": overrides,
        }
        bodies[_VARIATION_ARTIFACT_IDS[variation_entry_id]] = body
        variation_entry_ids.append(variation_entry_id)
    return bodies, variation_entry_ids


@given(
    claim_type=st.sampled_from(["observational", "interpretive"]),
    entries=st.lists(_entry_strategy, min_size=1, max_size=6),
    supported_threshold=st.integers(min_value=1, max_value=3),
    contested_threshold=st.integers(min_value=1, max_value=3),
    min_included_sources=st.integers(min_value=0, max_value=3),
    variation_specs=st.lists(_variation_strategy, min_size=1, max_size=3),
)
def test_two_calls_with_identical_inputs_including_variations_produce_identical_output_hash(
    claim_type: str,
    entries: list[dict[str, Any]],
    supported_threshold: int,
    contested_threshold: int,
    min_included_sources: int,
    variation_specs: list[dict[str, Any]],
) -> None:
    corpus = _build_corpus(entries, claim_type)
    params = _build_protocol_params(supported_threshold, contested_threshold, min_included_sources)
    variation_bodies, variation_entry_ids = _build_variations(variation_specs, corpus)
    protocol_body = {
        "id": _PROTOCOL_ID,
        "content_hash": _VALID_HASH,
        "status": "locked",
        "extraction_fields": [],
        "sensitivity_variations": variation_entry_ids,
    }
    inputs = {
        _CORPUS_ARTIFACT_ID: json.dumps(corpus).encode("utf-8"),
        _PROTOCOL_PARAMETERS_ARTIFACT_ID: json.dumps(params).encode("utf-8"),
        _METHOD_PROTOCOL_ARTIFACT_ID: json.dumps(protocol_body).encode("utf-8"),
        **{
            artifact_id: json.dumps(body).encode("utf-8")
            for artifact_id, body in variation_bodies.items()
        },
    }
    bundle = _bundle(sensitivity_variation_entry_ids=variation_entry_ids)

    first = SystematicEvidenceSynthesisExecutor().execute(bundle, inputs, execution_attempt=1)
    second = SystematicEvidenceSynthesisExecutor().execute(bundle, inputs, execution_attempt=1)

    assert first.outcome == "completed"
    assert first.output == second.output
    assert first.output_hash == second.output_hash


@given(
    claim_type=st.sampled_from(["observational", "interpretive"]),
    entries=st.lists(_entry_strategy, min_size=1, max_size=6),
    supported_threshold=st.integers(min_value=1, max_value=3),
    contested_threshold=st.integers(min_value=1, max_value=3),
    min_included_sources=st.integers(min_value=0, max_value=3),
    variation_specs=st.lists(_variation_strategy, min_size=1, max_size=3),
)
def test_every_eligible_claim_candidate_keeps_its_ruled_ceiling_when_variations_are_present(
    claim_type: str,
    entries: list[dict[str, Any]],
    supported_threshold: int,
    contested_threshold: int,
    min_included_sources: int,
    variation_specs: list[dict[str, Any]],
) -> None:
    """Regression: variations never touch claim minting (derived_decisions
    (e)) — the existing ceiling-discipline property continues to hold
    unchanged when sensitivity variations are present.
    """
    corpus = _build_corpus(entries, claim_type)
    params = _build_protocol_params(supported_threshold, contested_threshold, min_included_sources)
    variation_bodies, variation_entry_ids = _build_variations(variation_specs, corpus)
    protocol_body = {
        "id": _PROTOCOL_ID,
        "content_hash": _VALID_HASH,
        "status": "locked",
        "extraction_fields": [],
        "sensitivity_variations": variation_entry_ids,
    }
    inputs = {
        _CORPUS_ARTIFACT_ID: json.dumps(corpus).encode("utf-8"),
        _PROTOCOL_PARAMETERS_ARTIFACT_ID: json.dumps(params).encode("utf-8"),
        _METHOD_PROTOCOL_ARTIFACT_ID: json.dumps(protocol_body).encode("utf-8"),
        **{
            artifact_id: json.dumps(body).encode("utf-8")
            for artifact_id, body in variation_bodies.items()
        },
    }
    bundle = _bundle(sensitivity_variation_entry_ids=variation_entry_ids)

    result = SystematicEvidenceSynthesisExecutor().execute(bundle, inputs, execution_attempt=1)

    assert result.outcome == "completed"
    assert result.output is not None
    output = json.loads(result.output.decode("utf-8"))

    for analysis in output["analyses"]:
        candidate = analysis["claim_candidate"]
        if candidate is None:
            continue
        assert candidate["claim_type"] in {"observational", "interpretive"}
        assert candidate["claim_type"] != "causal"
        assert candidate["ruled_ceiling"] == RULED_CEILING
        assert candidate["ruled_ceiling"] == "associational_unadjusted"

    # The sensitivity-results array itself carries no claim_candidate /
    # ruled_ceiling field at all — structurally proving variations never
    # participate in claim minting.
    for entry in output["sensitivity_analysis_results"]:
        assert "claim_candidate" not in entry
        assert "ruled_ceiling" not in entry
