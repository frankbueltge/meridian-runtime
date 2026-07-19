"""Mirrors schemas/model-profile.schema.json (docs/spec/01_SYSTEM_SPEC.md
MRR-FR-045: "Model invocations MUST use a provider-neutral adapter and
record model profile ..."). First task of Epic E4 (task-packets/
E4-T01.yaml); the twelfth entity schema/model pair in this repository.

A ``ModelProfile`` is a canonically hashable, provider-opaque description of
one model configuration — NOT a call record (that is
``mrr.contracts.model_invocation.ModelInvocation``) and NOT an
independence/lineage calculation (E4-T05, out of this task's scope: this
model only CARRIES the ``model_family``/``prompt_family`` dimension fields
that calculation will eventually consume).

--- ``provider`` is an opaque string label, never a vendor type -------------

MRR-NFR-004 (vendor neutrality) and MRR-NFR-010 (framework-/provider-free
core): ``provider`` is a plain ``str`` (e.g. ``"anthropic"``, ``"openai"``,
``"local-ollama"``) with no enumerated vocabulary and no import of any
provider SDK anywhere in this module or its schema — checked structurally by
the import-linter contract in pyproject.toml, not by convention.

--- ``determinism`` self-validated against ``decoding_parameters`` ---------

MRR-FR-044 ("The system MUST distinguish deterministic transformations from
stochastic model-assisted operations") requires ``determinism`` to be an
explicit, REQUIRED field, never silently defaulted or inferred — and a
self-contradictory declaration must be rejected, not silently accepted
(task-packets/E4-T01.yaml invariant, with the packet's own example:
"deterministic while declaring nonzero sampling"). ``decoding_parameters``
is deliberately a fully open ``dict[str, Any]`` (mirroring
``mrr.contracts.task_bundle.TaskBundle.instructions``'s own "genuinely
open-ended JSON object" precedent) rather than a closed, provider-specific
schema — providers name their sampling knobs differently (temperature,
top_p, top_k, candidate_count, ...) and a closed vocabulary would silently
stop being provider-neutral for the next provider added.

``temperature`` is the one key this module gives conventional meaning to
(near-universal across providers, used verbatim by OpenAI, Anthropic,
Google, Cohere, Mistral, and others): if present and truthy-nonzero while
``determinism == "deterministic"``, construction fails. This is NOT a
specification-given vocabulary — flagged as an open specification question
in this task's PR body, mirroring ``mrr.contracts.evidence_anchor.
RecomputationStatus``'s own "not spec-derived" precedent. No other key in
``decoding_parameters`` (e.g. ``top_p``, ``top_k``) is checked, to avoid
inventing domain behavior beyond the packet's own named example
(AGENTS.md rule 3).

--- ``config_hash``: a narrower, caller-supplied hash than ``content_hash``---

``content_hash`` (inherited from ``BaseObject``) covers every field on this
object, including identity/audit metadata (``id``, ``practice_id``,
``created_at``, ...) — two profiles with byte-identical CONFIGURATION but
different creation times or authoring practices would get different
``content_hash`` values. ``config_hash`` is a second, narrower hash over
exactly the eight semantic configuration fields (``provider`` through
``tool_permissions``, see ``compute_config_hash`` below) so that two
independently created profiles sharing the same configuration are
recognizable as such by comparing this one field — the "canonically
hashable ... description" the task's objective calls for. Like
``content_hash`` itself, this module does not compute or verify it inline
(no model, in this codebase, self-validates its own ``content_hash``
either — that responsibility sits with whichever service constructs the
object); ``compute_config_hash`` is exposed for callers (tests, and any
future recording service) to compute it correctly and consistently, reusing
``mrr.domain.hashing_policy.compute_content_hash`` (RFC 8785 canonicalization
+ SHA-256) rather than a new hash implementation.
"""

from __future__ import annotations

from typing import Any, Literal, Self

from mrr.contracts.common import BaseObject, Sha256
from mrr.domain.hashing_policy import compute_content_hash
from mrr.domain.model_adapter import OperationKind
from pydantic import Field, model_validator

__all__ = [
    "ModelProfile",
    "OperationKind",
    "compute_config_hash",
]


def compute_config_hash(
    *,
    provider: str,
    model_family: str,
    model_identifier: str,
    decoding_parameters: dict[str, Any],
    determinism: OperationKind,
    seed: int | None,
    prompt_family: str | None,
    tool_permissions: list[str],
) -> str:
    """Compute a ``ModelProfile``'s ``config_hash``: the canonical SHA-256
    content hash of exactly its eight semantic configuration fields (every
    field this module defines except the inherited ``BaseObject`` identity/
    audit fields and ``config_hash`` itself).

    Reuses ``mrr.domain.hashing_policy.compute_content_hash`` directly
    (RFC 8785 canonicalization is invariant to map key order; any semantic
    byte change to the payload changes the hash) rather than a new hash
    implementation — the same invariant family E1-T02 already established
    and property-tests in tests/property/test_canonical_hash_properties.py.
    """
    payload = {
        "provider": provider,
        "model_family": model_family,
        "model_identifier": model_identifier,
        "decoding_parameters": decoding_parameters,
        "determinism": determinism,
        "seed": seed,
        "prompt_family": prompt_family,
        "tool_permissions": tool_permissions,
    }
    return compute_content_hash(payload)


class ModelProfile(BaseObject):
    """Mirrors schemas/model-profile.schema.json.

    Every property is in the schema's top-level `required` list except
    `decoding_parameters`, `seed`, `prompt_family`, and `tool_permissions` —
    the four with Python defaults here, matching
    `mrr.contracts.task_bundle.TaskBundle`'s own `tools`/`secret_refs`
    precedent (a plain, non-nullable type omitted from `required`, not an
    `anyOf`-nullable field).
    """

    kind: Literal["ModelProfile"]
    provider: str = Field(min_length=1)
    model_family: str = Field(min_length=1)
    model_identifier: str = Field(min_length=1)
    decoding_parameters: dict[str, Any] = Field(default_factory=dict)
    determinism: OperationKind
    seed: int | None = Field(default=None, ge=0)
    prompt_family: str | None = None
    tool_permissions: list[str] = Field(default_factory=list)
    config_hash: Sha256

    @model_validator(mode="after")
    def _determinism_matches_decoding_parameters(self) -> Self:
        if self.determinism == "deterministic":
            temperature = self.decoding_parameters.get("temperature")
            if isinstance(temperature, int | float) and temperature != 0:
                raise ValueError(
                    "determinism is 'deterministic' but decoding_parameters declares a "
                    f"nonzero sampling temperature ({temperature!r}) — MRR-FR-044 requires "
                    "a declared operation kind not contradict the configuration"
                )
        return self
