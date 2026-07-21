"""Unit tests for
``mrr.services.node_runtime.synthesis_executor.build_model_assisted_extraction_callable``
(task-packets/K1-T03.yaml derived_decisions (m)) — the OPTIONAL,
separately-tested model-assisted extraction slice. NEVER exercised by this
packet's own model-free acceptance path (``extraction_callable=None``); this
file is the "separate, dedicated set of unit tests" that module's own
docstring names.

Driven entirely by an in-test scripted fake ``ModelAdapter`` — never a real
provider, never a network call (mirrors
tests/unit/adapters/llm/test_structured_generation.py's own identical
discipline).
"""

from __future__ import annotations

import json

from mrr.domain.model_adapter import (
    ModelInvocationOutcome,
    ModelInvocationRequest,
    TokenUsage,
    apply_redaction,
)
from mrr.services.node_runtime.synthesis_executor import (
    CorpusEntry,
    build_model_assisted_extraction_callable,
)

_VALID_HASH = "sha256:" + "e" * 64
_VALID_PROFILE_URN = "urn:mrr:model-profile:01ARZ3NDEKTSV4RRFFQ69G5FAV"


class _ScriptedFakeAdapter:
    """An in-memory, deterministic fake ``ModelAdapter`` — no network of any
    kind. Mirrors
    tests/unit/adapters/llm/test_structured_generation.py's own
    ``_ScriptedFakeAdapter`` exactly.
    """

    def __init__(self, script: list[ModelInvocationOutcome]) -> None:
        self._script = script
        self.calls: list[ModelInvocationRequest] = []

    def invoke(self, request: ModelInvocationRequest) -> ModelInvocationOutcome:
        self.calls.append(request)
        index = len(self.calls) - 1
        return self._script[index]


def _completed_outcome(payload: dict[str, str]) -> ModelInvocationOutcome:
    response_text = json.dumps(payload)
    response_hash, raw_response = apply_redaction("raw_permitted", response_text)
    return ModelInvocationOutcome(
        status="completed",
        prompt_config_hash=_VALID_HASH,
        token_usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        redaction_policy="raw_permitted",
        response_hash=response_hash,
        raw_response_text=raw_response,
    )


def _refused_outcome() -> ModelInvocationOutcome:
    return ModelInvocationOutcome(
        status="refused",
        prompt_config_hash=_VALID_HASH,
        token_usage=TokenUsage(prompt_tokens=10, completion_tokens=0, total_tokens=10),
        redaction_policy="hashes_only",
    )


def _entry(**overrides: object) -> CorpusEntry:
    data: dict[str, object] = {
        "entry_id": "entry-1",
        "applies_to_analysis": "candidate-a",
        "claim_type": "interpretive",
        "evidence_relation": "supports",
        "verification_status": "verified",
        "claim_relevant_finding": "The work's own production notes describe recursive training.",
        "extraction": {"sample_size": "1 work (human-supplied fallback)"},
        "title": "A test fixture source",
        "creators": [],
        "retrieval_timestamp": "2026-07-21T09:00:00Z",
        "retrieval_method": "test-fixture",
        "source_type": "test-fixture",
        "primary_secondary_derived": "primary",
    }
    data.update(overrides)
    return CorpusEntry.model_validate(data)


def test_proposal_status_becomes_the_row_extraction_with_verified_disposition() -> None:
    adapter = _ScriptedFakeAdapter(
        [_completed_outcome({"sample_size": "2 works", "methodology_notes": "cross-checked"})]
    )
    extract = build_model_assisted_extraction_callable(
        adapter, model_profile_id=_VALID_PROFILE_URN, model_profile_hash=_VALID_HASH
    )

    outcome = extract(_entry(), ["sample_size", "methodology_notes"])

    assert outcome.verification_disposition == "verified"
    assert outcome.extraction == {"sample_size": "2 works", "methodology_notes": "cross-checked"}
    assert len(adapter.calls) == 1


def test_non_proposal_status_downgrades_to_the_entrys_own_human_supplied_extraction() -> None:
    adapter = _ScriptedFakeAdapter([_refused_outcome()])
    extract = build_model_assisted_extraction_callable(
        adapter,
        model_profile_id=_VALID_PROFILE_URN,
        model_profile_hash=_VALID_HASH,
        max_repair_attempts=0,
    )
    entry = _entry(extraction={"sample_size": "1 work (human-supplied fallback)"})

    outcome = extract(entry, ["sample_size"])

    assert outcome.verification_disposition == "downgraded-to-proposal"
    assert outcome.extraction == {"sample_size": "1 work (human-supplied fallback)"}


def test_never_invoked_by_the_default_model_free_executor() -> None:
    """Documents, rather than merely asserts, the packet's own "never
    required" framing: the default ``extraction_callable=None`` never
    imports or touches this module's adapter-calling code path at all.
    """
    from mrr.services.node_runtime.synthesis_executor import SystematicEvidenceSynthesisExecutor

    executor = SystematicEvidenceSynthesisExecutor()
    assert executor._extraction_callable is None
