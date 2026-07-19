"""Mirrors schemas/model-invocation.schema.json (docs/spec/01_SYSTEM_SPEC.md
MRR-FR-045: "... record model profile, prompt/configuration hash, tool
calls, token usage, and response hash subject to local redaction policy").
First task of Epic E4 (task-packets/E4-T01.yaml); the thirteenth entity
schema/model pair in this repository.

A ``ModelInvocation`` is an auditable record of a SINGLE model call. It
references a ``mrr.contracts.model_profile.ModelProfile`` by id and pinned
content hash rather than embedding one (mirrors
``mrr.contracts.evidence_anchor.EvidenceAnchor.source_record_id``'s own
by-reference-only precedent).

--- Proposal-only by shape (MRR-FR-046, AGENTS.md rule 7) -------------------

"A model response MUST be treated as a proposal until domain validation
accepts it." This model carries no ``claim_status``, no verification
verdict, and no authoritative-acceptance field of any kind — ``status``
below is the CALL's own terminal outcome (did the call complete, get
refused, get filtered, error, or time out), never a judgment about whether
its content is true, accepted, or authoritative. There is structurally no
field anywhere on this object a caller could set to mark a response
"accepted"; a component wanting to accept model output must go through
``mrr.contracts.claim.Claim``/``mrr.contracts.verification_result.
VerificationResult`` instead (a future integration, out of this task's
scope) — this is E4's "no model output can mutate authoritative state
directly" exit criterion, established here at the data layer.

--- Hash-first, redaction-default recording (MRR-FR-045, AGENTS.md rule 11) -

By default this object records only the SHA-256 hash of the prompt/
configuration (``prompt_config_hash``) and of the response
(``response_hash``) — never the raw text. ``redaction_policy`` carries no
Python default anywhere (mirrors ``operation_kind``'s own "never silently
defaulted" treatment, and see ``mrr.domain.model_adapter``'s module
docstring for the full rationale): a caller must explicitly state
``"hashes_only"`` (the safe choice — ``mrr.domain.model_adapter.
DEFAULT_REDACTION_POLICY``) or ``"raw_permitted"``. `_redaction_forbids_raw_text`
below enforces, at construction time, that `"hashes_only"` NEVER coexists
with a non-null `raw_prompt_text`/`raw_response_text` — the structural
guarantee that "no secret is ever recorded" under the default.

--- Terminal status stays five-way distinct --------------------------------

`status` reuses `mrr.domain.model_adapter.TerminalStatus` verbatim
(`completed`/`refused`/`content_filtered`/`error`/`timed_out`) — never
collapsed to one generic failure (AGENTS.md prohibited shortcut).
`_response_hash_matches_completion` enforces the biconditional a completed
call always has a `response_hash` and no other status ever does — "a
refused or filtered call is NOT recorded as completed and yields no
response that any field marks as accepted" (task-packets/E4-T01.yaml
invariant), enforced identically at the JSON Schema level (two `if`/`then`
conditionals) and here, mirroring `mrr.contracts.verification_result.
VerificationResult`'s own "enforced twice" precedent.

--- Tool calls: name + argument/result hashes + own status -----------------

Each `ModelToolCall` mirrors `ToolCallOutcome` in `mrr.domain.model_adapter`
field-for-field (this module's Pydantic model is the schema-validated,
persisted mirror of that domain value object) — the same "result_hash
present iff status == 'completed'" biconditional is enforced here too, at
both the schema and model level, for the same reason.
"""

from __future__ import annotations

from typing import Literal, Self

from mrr.contracts.common import BaseObject, MRRModel, Sha256, Urn
from mrr.domain.model_adapter import OperationKind, RedactionPolicy, TerminalStatus, ToolCallStatus
from pydantic import Field, model_validator

__all__ = [
    "ModelInvocation",
    "ModelToolCall",
    "OperationKind",
    "RedactionPolicy",
    "TerminalStatus",
    "TokenUsage",
    "ToolCallStatus",
]


class TokenUsage(MRRModel):
    """Mirrors `token_usage`; all three properties are required. Token
    counts only — never a cost/currency figure (see
    `mrr.contracts.run_manifest.RunCost` for the separate, optional cost
    concept).
    """

    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class ModelToolCall(MRRModel):
    """Mirrors one `tool_calls[]` entry. Per this task's derived_decisions,
    records the tool's name plus argument/result HASHES and its own status
    — never a raw tool payload by default (there is no raw-argument or
    raw-result field anywhere on this model; that redaction question does
    not even arise structurally).
    """

    name: str = Field(min_length=1)
    arguments_hash: Sha256
    result_hash: Sha256 | None = None
    status: ToolCallStatus

    @model_validator(mode="after")
    def _result_hash_matches_completion(self) -> Self:
        if self.status == "completed" and self.result_hash is None:
            raise ValueError("a completed tool call must carry a result_hash")
        if self.status != "completed" and self.result_hash is not None:
            raise ValueError(
                f"a tool call with status {self.status!r} must not carry a result_hash "
                "(only a completed call has a result to hash)"
            )
        return self


class ModelInvocation(BaseObject):
    """Mirrors schemas/model-invocation.schema.json.

    Every property is in the schema's top-level `required` list except
    `response_hash`, `raw_prompt_text`, and `raw_response_text` — the three
    with a Python default of `None` here (a plain, non-nullable schema type
    omitted from `required`, matching `mrr.contracts.task_bundle.
    ExecutionSpec.code_revision`'s own precedent), each present only under
    the conditions this module's docstring and validators describe.
    `tool_calls` defaults to an empty list (a call that used no tools),
    matching `mrr.contracts.task_bundle.TaskBundle.tools`'s own precedent.
    """

    kind: Literal["ModelInvocation"]
    model_profile_id: Urn
    model_profile_hash: Sha256
    operation_kind: OperationKind
    prompt_config_hash: Sha256
    token_usage: TokenUsage
    tool_calls: list[ModelToolCall] = Field(default_factory=list)
    response_hash: Sha256 | None = None
    status: TerminalStatus
    redaction_policy: RedactionPolicy
    raw_prompt_text: str | None = None
    raw_response_text: str | None = None

    @model_validator(mode="after")
    def _response_hash_matches_completion(self) -> Self:
        if self.status == "completed" and self.response_hash is None:
            raise ValueError("a completed invocation must carry a response_hash")
        if self.status != "completed" and self.response_hash is not None:
            raise ValueError(
                f"an invocation with status {self.status!r} must not carry a response_hash "
                "(only a completed call is recorded as having produced a response)"
            )
        return self

    @model_validator(mode="after")
    def _redaction_forbids_raw_text(self) -> Self:
        if self.redaction_policy == "hashes_only" and (
            self.raw_prompt_text is not None or self.raw_response_text is not None
        ):
            raise ValueError(
                "redaction_policy is 'hashes_only' but raw prompt/response text is present — "
                "retaining raw text requires redaction_policy='raw_permitted' (MRR-FR-045)"
            )
        return self
