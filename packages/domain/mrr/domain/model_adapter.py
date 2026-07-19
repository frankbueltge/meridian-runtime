"""Provider-neutral ``ModelAdapter`` port, per docs/spec/01_SYSTEM_SPEC.md
MRR-FR-045 ("Model invocations MUST use a provider-neutral adapter and
record model profile, prompt/configuration hash, tool calls, token usage,
and response hash subject to local redaction policy") and MRR-NFR-004
(vendor neutrality: "LLM ... providers MUST be behind interfaces"). First
task of Epic E4 (task-packets/E4-T01.yaml).

This module is framework- and provider-free (no model-provider SDK, no
network client, no web/workflow framework import anywhere in it —
MRR-NFR-010, enforced by the import-linter contract in pyproject.toml and
by tests/unit/architecture/test_import_boundaries.py). It mirrors the
``ArtifactStore`` precedent (``mrr.domain.artifacts``) exactly: a
``Protocol`` plus frozen, self-validating request/result value objects, and
NO concrete implementation. The first concrete implementation is the
structured-generation adapter under ``adapters/llm`` (task-packets/
E4-T02.yaml, out of this task's scope); tests here use only an in-test fake.

--- Why this module, not ``mrr.contracts``, owns the shared vocabulary ------

``OperationKind``, ``TerminalStatus``, ``ToolCallStatus``, and
``RedactionPolicy`` are defined once, here, rather than in
``mrr.contracts.model_invocation`` — both the port's request/result value
objects AND the ``mrr.contracts.ModelInvocation`` record need the exact
same four vocabularies (the port describes what a call can produce; the
contract records what a call actually produced), and ``mrr.contracts``
already depends on ``mrr.domain`` (e.g. ``mrr.contracts.common`` imports
``mrr.domain.identity.URN_PATTERN``), so importing these four names FROM
here INTO ``mrr.contracts.model_invocation``/``model_profile`` introduces no
new dependency edge and no cycle — unlike ``mrr.domain.artifacts.
Classification``, which is redeclared locally specifically because
importing ``mrr.contracts`` back INTO ``mrr.domain`` would be a new cycle
(domain -> contracts -> domain). Single-sourcing here avoids two vocabularies
silently drifting apart, which duplicating them would risk.

--- No redaction Python default: the field must always be explicit ---------

``RedactionPolicy`` carries no field default anywhere it appears (this
module's ``ModelInvocationRequest``/``ModelInvocationOutcome``, and
``mrr.contracts.model_invocation.ModelInvocation``) — mirroring exactly how
``OperationKind`` is never silently defaulted (MRR-FR-044's own "explicit,
REQUIRED field" language). ``DEFAULT_REDACTION_POLICY`` is exposed as a
named constant precisely so a caller states the safe choice explicitly
(``redaction_policy=DEFAULT_REDACTION_POLICY``) rather than one being
implied by omission — the safest reading of "no secret is ever recorded"
(AGENTS.md rule 11) is that the field recording whether raw text COULD be
present is itself never left unstated.

--- Request/result are plain value objects, not a persisted record ---------

``ModelInvocationRequest``/``ModelInvocationOutcome`` are the domain-layer
shape of "what one call needs" and "what one call produced" — NOT the
persisted, BaseObject-enveloped ``mrr.contracts.model_invocation.
ModelInvocation`` (which carries identity, practice, revision, and audit
fields no single call outcome can supply by itself). This mirrors
``mrr.domain.artifacts.ArtifactDescriptor`` standing apart from the
(not-yet-built) first-class ``Artifact`` entity. Turning an
``ModelInvocationOutcome`` into a stored ``ModelInvocation`` revision is a
future recording service's job (E4-T02/E4-T05, out of this task's scope) —
this module does not do it and does not import ``mrr.contracts`` to do it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from mrr.crypto.hashing import content_hash
from mrr.domain.artifacts import require_valid_content_hash
from mrr.domain.identity import is_valid_urn

#: MRR-FR-044: "The system MUST distinguish deterministic transformations
#: from stochastic model-assisted operations." Shared verbatim by
#: ``mrr.contracts.model_profile.ModelProfile.determinism`` (the profile's
#: own configured disposition) and ``mrr.contracts.model_invocation.
#: ModelInvocation.operation_kind`` (what a specific call actually was) —
#: two different fields, one shared closed vocabulary.
OperationKind = Literal["deterministic", "stochastic"]

#: The terminal outcome of one model call. Kept as five DISTINCT values —
#: never collapsed to one generic failure (AGENTS.md prohibited shortcut:
#: "collapsing `unknown`, `not_found`, `contradicted`, and `failed` into one
#: generic error"). ``refused`` (the model itself declined) and
#: ``content_filtered`` (a policy filter intervened) are kept apart per the
#: task packet's own "refused (or content_filtered)" phrasing, rather than
#: merged into a single "refused" value.
TerminalStatus = Literal["completed", "refused", "content_filtered", "error", "timed_out"]

#: The terminal outcome of one tool call made during a model invocation.
#: Deliberately a NARROWER, separate vocabulary from ``TerminalStatus``:
#: ``content_filtered`` is a model-text-generation-specific concept that
#: does not apply to a deterministic tool invocation, so it is dropped;
#: ``refused`` is kept (a tool call outside the profile's declared
#: ``tool_permissions`` can be refused by the adapter before it ever runs).
#: Not a specification-given vocabulary (domain 2.6 names "tool ...
#: invocations" without enumerating outcome values) — this is this task's
#: own minimal proposal, flagged as an open specification question in the
#: PR body, mirroring ``mrr.contracts.evidence_anchor.RecomputationStatus``'s
#: own "not spec-derived" precedent.
ToolCallStatus = Literal["completed", "refused", "error", "timed_out"]

#: MRR-FR-045 ("... subject to local redaction policy") and AGENTS.md rule
#: 11 ("no ... secrets in prompts"): whether raw prompt/response text may be
#: retained alongside its hash. ``"hashes_only"`` is the safe reading of
#: "by default ... record only ... hashes" (task-packets/E4-T01.yaml
#: derived_decisions) — retaining raw text requires an explicit,
#: affirmative ``"raw_permitted"``, never an inferred or implicit choice.
RedactionPolicy = Literal["hashes_only", "raw_permitted"]

#: The safe choice — see this module's docstring section on why no field
#: anywhere defaults to this implicitly. Callers that want "record only
#: hashes" pass this constant explicitly.
DEFAULT_REDACTION_POLICY: RedactionPolicy = "hashes_only"


def apply_redaction(policy: RedactionPolicy, raw_text: str) -> tuple[str, str | None]:
    """The redaction default helper: hash ``raw_text`` and, per ``policy``,
    decide whether the raw text itself may also be retained.

    Returns ``(content_hash, raw_text_or_none)``. Under
    ``"hashes_only"`` the second element is ALWAYS ``None`` — no caller of
    this function can accidentally retain raw text under the default/safe
    policy; only ``"raw_permitted"`` ever returns the text itself, and even
    then a caller MAY still choose to discard it (this helper permits
    retention, it does not force it).
    """
    hashed = content_hash(raw_text.encode("utf-8"))
    if policy == "hashes_only":
        return hashed, None
    return hashed, raw_text


@dataclass(frozen=True, slots=True, kw_only=True)
class TokenUsage:
    """Mirrors MRR-FR-045's "token usage". All three counts are token
    counts, never a cost/currency figure (see ``mrr.contracts.run_manifest.
    RunCost`` for the separate, optional cost concept).
    """

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    def __post_init__(self) -> None:
        for field_name in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = getattr(self, field_name)
            if value < 0:
                raise ValueError(f"{field_name} must be >= 0, got {value}")


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolCallOutcome:
    """One tool call made during a model invocation. Per this task's
    derived_decisions, records the tool's name plus argument/result
    HASHES and its own status — never a raw tool payload by default.

    ``result_hash`` is present if and only if ``status == "completed"``:
    a tool call that did not complete produced no result to hash, and one
    that did complete always has one (mirrors
    ``ModelInvocationOutcome.response_hash``'s identical biconditional).
    """

    name: str
    arguments_hash: str
    result_hash: str | None
    status: ToolCallStatus

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name must not be empty")
        require_valid_content_hash(self.arguments_hash)
        if self.result_hash is not None:
            require_valid_content_hash(self.result_hash)
        if self.status == "completed" and self.result_hash is None:
            raise ValueError("a completed tool call must carry a result_hash")
        if self.status != "completed" and self.result_hash is not None:
            raise ValueError(
                f"a tool call with status {self.status!r} must not carry a result_hash "
                "(only a completed call has a result to hash)"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelInvocationRequest:
    """Everything a concrete adapter (E4-T02) needs to make, or an in-test
    fake needs to simulate, one model call.

    ``model_profile_id``/``model_profile_hash`` pin which
    ``mrr.contracts.model_profile.ModelProfile`` (and which exact revision
    of it, by content hash) governs this call — the request carries this
    pinned reference, never the profile object itself (avoiding a
    domain -> contracts dependency; see this module's docstring).
    ``prompt_text`` is the actual, in-memory-only prompt content: it is
    never itself persisted raw by this module — whether a caller later
    retains it is governed by ``redaction_policy`` at the point a
    ``ModelInvocationOutcome`` (or, eventually, a persisted
    ``ModelInvocation``) is built.
    """

    model_profile_id: str
    model_profile_hash: str
    prompt_text: str
    operation_kind: OperationKind
    redaction_policy: RedactionPolicy
    tool_names_available: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not is_valid_urn(self.model_profile_id):
            raise ValueError(f"model_profile_id is not a valid MRR urn: {self.model_profile_id!r}")
        require_valid_content_hash(self.model_profile_hash)
        if not self.prompt_text:
            raise ValueError("prompt_text must not be empty")


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelInvocationOutcome:
    """The raw outcome of one model call. Proposal-only by shape, exactly
    like ``mrr.contracts.model_invocation.ModelInvocation`` (MRR-FR-046,
    AGENTS.md rule 7): no field here expresses a claim status, a
    verification verdict, or authoritative acceptance.

    ``response_hash`` is present if and only if ``status == "completed"`` —
    a refused, filtered, errored, or timed-out call is never recorded as
    completed and carries no response that any field marks as accepted
    (task-packets/E4-T01.yaml invariant).
    """

    status: TerminalStatus
    prompt_config_hash: str
    token_usage: TokenUsage
    redaction_policy: RedactionPolicy
    response_hash: str | None = None
    tool_calls: tuple[ToolCallOutcome, ...] = ()
    raw_prompt_text: str | None = None
    raw_response_text: str | None = None

    def __post_init__(self) -> None:
        require_valid_content_hash(self.prompt_config_hash)
        if self.response_hash is not None:
            require_valid_content_hash(self.response_hash)
        if self.status == "completed" and self.response_hash is None:
            raise ValueError("a completed invocation must carry a response_hash")
        if self.status != "completed" and self.response_hash is not None:
            raise ValueError(
                f"an invocation with status {self.status!r} must not carry a response_hash "
                "(only a completed call is recorded as having produced a response)"
            )
        if self.redaction_policy == "hashes_only" and (
            self.raw_prompt_text is not None or self.raw_response_text is not None
        ):
            raise ValueError(
                "redaction_policy is 'hashes_only' but raw prompt/response text is present — "
                "retaining raw text requires redaction_policy='raw_permitted' (MRR-FR-045)"
            )


@runtime_checkable
class ModelAdapter(Protocol):
    """The abstract, provider-neutral port every concrete model-provider
    adapter implements (MRR-NFR-004, MRR-NFR-010). ``provider`` never
    appears as a type here — a concrete adapter's own class identifies the
    provider; this Protocol has exactly one method and takes no
    provider-specific argument of any kind.

    No concrete implementation exists in this module or anywhere under
    ``packages/``/``adapters/`` yet — task-packets/E4-T01.yaml's
    forbidden_changes reserves that for E4-T02. Tests use only an in-test
    fake implementing this Protocol.
    """

    def invoke(self, request: ModelInvocationRequest) -> ModelInvocationOutcome:
        """Perform (or, for a fake, simulate) exactly one model call
        described by ``request`` and return its outcome.

        A conforming implementation MUST NOT retain or return raw prompt or
        response text when ``request.redaction_policy == "hashes_only"``
        (``ModelInvocationOutcome.__post_init__`` already refuses to
        construct such a value, so a conforming adapter cannot return one
        even by mistake without raising first).
        """
        ...


__all__ = [
    "DEFAULT_REDACTION_POLICY",
    "ModelAdapter",
    "ModelInvocationOutcome",
    "ModelInvocationRequest",
    "OperationKind",
    "RedactionPolicy",
    "TerminalStatus",
    "ToolCallOutcome",
    "ToolCallStatus",
    "TokenUsage",
    "apply_redaction",
]
