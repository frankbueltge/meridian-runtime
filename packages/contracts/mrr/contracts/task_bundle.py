"""Mirrors schemas/task-bundle.schema.json (docs/spec/02_DOMAIN_MODEL.md
section 2.5, "TaskBundle": a signed request for a bounded action).
"""

from __future__ import annotations

from typing import Any, Literal

from mrr.contracts.common import (
    ApprovalMode,
    ArtifactRef,
    BaseObject,
    Classification,
    MRRModel,
    Sha256,
    Signature,
    Urn,
)
from pydantic import AwareDatetime, Field

#: Mirrors `data_access_mode`.
DataAccessMode = Literal["none", "read_local", "compute_to_data", "approved_transfer"]

#: Mirrors `network_policy.mode`.
NetworkPolicyMode = Literal["deny_all", "allowlist"]

#: Mirrors the top-level `status` enum.
TaskBundleStatus = Literal[
    "CREATED",
    "OFFERED",
    "ACCEPTED",
    "MODIFICATION_PROPOSED",
    "DEFERRED",
    "REJECTED",
    "QUEUED",
    "EXPIRED",
    "CANCELLED",
    "RUNNING",
    "FAILED",
    "COMPLETED",
    "SEALED",
    "INVALID_RESULT",
]


class CapabilityRef(MRRModel):
    """Mirrors the `capability` object: a `{name, version}` reference to a
    NodeManifest capability, not the full `CapabilityDefinition`.
    """

    name: str
    version: str


class ExecutionSpec(MRRModel):
    """Mirrors the `execution` object. `code_revision` is the only property
    absent from its `required: ["image_digest", "entrypoint"]` list.
    """

    image_digest: Sha256
    entrypoint: list[str] = Field(min_length=1)
    code_revision: str | None = None


class ResourceLimits(MRRModel):
    """Mirrors the `resource_limits` object; all four properties are required."""

    cpu: float = Field(gt=0)
    memory_mb: int = Field(ge=64)
    disk_mb: int = Field(ge=1)
    timeout_seconds: int = Field(ge=1)


class NetworkPolicy(MRRModel):
    """Mirrors the `network_policy` object; both properties are required."""

    mode: NetworkPolicyMode
    allowlist: list[str]


class TaskBundle(BaseObject):
    """Mirrors schemas/task-bundle.schema.json.

    Every property is in the schema's top-level `required` list except
    `tools` and `secret_refs`, which are the only two with defaults here.

    `instructions` mirrors `{"type": "object"}` with no `properties` or
    `additionalProperties` restriction — a genuinely open-ended JSON object
    — so it is `dict[str, Any]` rather than a closed `MRRModel`.
    `output_schema` mirrors a plain `{"type": "string"}` (like
    node-manifest.schema.json's `input_schema`/`output_schema`), not a
    `$ref` to `$defs.urn`, so it stays plain `str`.
    """

    kind: Literal["TaskBundle"]
    origin_practice_id: Urn
    target_node_id: Urn
    research_score_id: Urn
    research_score_revision: int = Field(ge=1)
    branch_id: Urn
    capability: CapabilityRef
    purpose: str = Field(min_length=1)
    instructions: dict[str, Any]
    inputs: list[ArtifactRef]
    data_access_mode: DataAccessMode
    execution: ExecutionSpec
    resource_limits: ResourceLimits
    network_policy: NetworkPolicy
    tools: list[str] = Field(default_factory=list)
    secret_refs: list[str] = Field(default_factory=list)
    output_schema: str
    classification: Classification
    approval_requirement: ApprovalMode
    expires_at: AwareDatetime
    nonce: str = Field(min_length=16)
    signature: Signature
    status: TaskBundleStatus
