"""Mirrors schemas/evidence-crate.schema.json (docs/spec/02_DOMAIN_MODEL.md
section 2.9-2.10 context; the EvidenceCrate itself packages a run's
artifacts, source records, evidence anchors, proposed claims, and failures).
"""

from __future__ import annotations

from typing import Literal

from mrr.contracts.common import ArtifactRef, BaseObject, MRRModel, Sha256, Signature, Urn
from pydantic import Field

#: Mirrors the top-level `run_state` enum.
RunState = Literal["completed", "failed", "cancelled", "timed_out", "policy_denied", "partial"]

#: Mirrors `failures[].category` — the same failure taxonomy AGENTS.md's
#: "Prohibited shortcuts" section warns against collapsing into one generic
#: error ("collapsing unknown, not_found, contradicted, and failed into one
#: generic error").
FailureCategory = Literal[
    "not_found",
    "unknown",
    "null_result",
    "contradicted",
    "underpowered",
    "method_invalidated",
    "source_unavailable",
    "execution_error",
    "policy_denied",
]


class FailureEntry(MRRModel):
    """Mirrors a `failures[]` entry; all three properties are required."""

    code: str
    category: FailureCategory
    message: str


class EnvironmentInfo(MRRModel):
    """Mirrors the `environment` object. `model_profiles` is the only
    property absent from its `required: ["image_digest", "code_revision",
    "input_hashes"]` list.
    """

    image_digest: Sha256
    code_revision: str
    input_hashes: list[Sha256]
    model_profiles: list[str] = Field(default_factory=list)


class EvidenceCrate(BaseObject):
    """Mirrors schemas/evidence-crate.schema.json.

    Every top-level property is in the schema's `required` list — there are
    no optional fields at this level (unlike the other five entities).
    `sealed` is a JSON Schema `const: true`, so it is `Literal[True]` rather
    than a plain bool.
    """

    kind: Literal["EvidenceCrate"]
    task_id: Urn
    run_id: Urn
    run_state: RunState
    artifacts: list[ArtifactRef]
    source_records: list[Urn]
    evidence_anchors: list[Urn]
    proposed_claims: list[Urn]
    failures: list[FailureEntry]
    known_unknowns: list[str]
    environment: EnvironmentInfo
    sealed: Literal[True]
    signature: Signature
