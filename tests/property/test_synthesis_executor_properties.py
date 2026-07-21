"""Property test for
``mrr.services.node_runtime.synthesis_executor.SystematicEvidenceSynthesisExecutor``
(task-packets/K1-T03.yaml) — mirrors task-packets/K1-T02.yaml's own
``inspect.signature``-style structural proof discipline, extended into a
genuine end-to-end property test: over MANY randomly generated small
synthetic corpora and eligibility-table thresholds, every claim candidate
``execute()`` proposes has ``claim_type`` in
``{"observational", "interpretive"}`` and, when eligible, ``ruled_ceiling ==
"associational_unadjusted"`` exactly — never ``"causal"``, never any ceiling
above ``associational_unadjusted``.

All generated corpora are small (2-6 entries, one ``applies_to_analysis``
group per example) — synthetic test fixtures, not the real atlases.
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


def _bundle(timeout_seconds: int = 10) -> TaskBundle:
    now = datetime.now(UTC)
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
        "purpose": "Property test fixture.",
        "instructions": {
            "corpus_artifact_id": _CORPUS_ARTIFACT_ID,
            "protocol_parameters_artifact_id": _PROTOCOL_PARAMETERS_ARTIFACT_ID,
            "method_protocol_artifact_id": _METHOD_PROTOCOL_ARTIFACT_ID,
            "question_id": _QUESTION_ID,
        },
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


#: One randomly-generated corpus entry within a single, shared
#: applies_to_analysis group — see the module docstring.
_entry_strategy = st.fixed_dictionaries(
    {
        "evidence_relation": st.sampled_from(["supports", "contradicts"]),
        "verification_status": st.sampled_from(["verified", "unverifiable", "pending"]),
        "source_family_id": st.one_of(st.none(), st.sampled_from(["fam-1", "fam-2", "fam-3"])),
    }
)


@given(
    claim_type=st.sampled_from(["observational", "interpretive"]),
    entries=st.lists(_entry_strategy, min_size=1, max_size=6),
    supported_threshold=st.integers(min_value=1, max_value=3),
    contested_threshold=st.integers(min_value=1, max_value=3),
    min_included_sources=st.integers(min_value=0, max_value=3),
    data=st.data(),
)
def test_every_eligible_claim_candidate_has_valid_claim_type_and_ruled_ceiling(
    claim_type: str,
    entries: list[dict[str, Any]],
    supported_threshold: int,
    contested_threshold: int,
    min_included_sources: int,
    data: st.DataObject,
) -> None:
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

    params = {
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
    protocol_body = {
        "id": _PROTOCOL_ID,
        "content_hash": _VALID_HASH,
        "status": "locked",
        "extraction_fields": [],
    }
    inputs = {
        _CORPUS_ARTIFACT_ID: json.dumps(corpus).encode("utf-8"),
        _PROTOCOL_PARAMETERS_ARTIFACT_ID: json.dumps(params).encode("utf-8"),
        _METHOD_PROTOCOL_ARTIFACT_ID: json.dumps(protocol_body).encode("utf-8"),
    }

    result = SystematicEvidenceSynthesisExecutor().execute(_bundle(), inputs, execution_attempt=1)

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
