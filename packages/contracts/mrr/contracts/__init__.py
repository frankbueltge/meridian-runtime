"""JSON Schema and Pydantic contracts for externally visible objects and domain events
per docs/spec/03_API_AND_EVENTS.md.

Six hand-written Pydantic v2 models mirror the six entity schemas in
schemas/ (E1-T03): ``ResearchScore``, ``NodeManifest``, ``TaskBundle``,
``Claim``, ``EvidenceCrate``, ``CorrectionEvent``. Shared building blocks
mirroring ``schemas/common.schema.json`` live in ``mrr.contracts.common``
and are re-exported here too (``Signature``, ``ArtifactRef``, ``Scope``,
``Budget``, ``BaseObject``, ``MRRModel``).

These models are hand-written rather than generated from the JSON Schemas
(see the PR body for the full rationale): the claim schema's conditional
`if`/`then` rule (a supported claim requires evidence and verification) has
no datamodel-code-generator equivalent and must be a hand-written validator
either way; drift between schemas, models, and examples is instead caught by
``scripts/check_contracts.py`` and ``tests/contract/``.
"""

from __future__ import annotations

from mrr.contracts.claim import Claim, ClaimStatus, ClaimType, UncertaintyEntry, UncertaintyKind
from mrr.contracts.common import (
    ApiVersion,
    ApprovalMode,
    ArtifactRef,
    AutonomyLevel,
    BaseObject,
    Budget,
    Classification,
    MRRModel,
    Scope,
    Sha256,
    Signature,
    Urn,
)
from mrr.contracts.correction_event import (
    AffectedObjectRef,
    CorrectionEvent,
    CorrectionSeverity,
    CorrectionStatus,
    CorrectionType,
)
from mrr.contracts.evidence_crate import (
    EnvironmentInfo,
    EvidenceCrate,
    FailureCategory,
    FailureEntry,
    RunState,
)
from mrr.contracts.node_manifest import (
    CapabilityDefinition,
    NetworkProfile,
    NodeManifest,
    TransportMode,
)
from mrr.contracts.research_score import (
    MaxDisclosure,
    MethodsPolicy,
    PublicationPolicy,
    ResearchScore,
    ResearchScoreStatus,
)
from mrr.contracts.task_bundle import (
    CapabilityRef,
    DataAccessMode,
    ExecutionSpec,
    NetworkPolicy,
    NetworkPolicyMode,
    ResourceLimits,
    TaskBundle,
)

__all__ = [
    "AffectedObjectRef",
    "ApiVersion",
    "ApprovalMode",
    "ArtifactRef",
    "AutonomyLevel",
    "BaseObject",
    "Budget",
    "CapabilityDefinition",
    "CapabilityRef",
    "Claim",
    "ClaimStatus",
    "ClaimType",
    "Classification",
    "CorrectionEvent",
    "CorrectionSeverity",
    "CorrectionStatus",
    "CorrectionType",
    "DataAccessMode",
    "EnvironmentInfo",
    "EvidenceCrate",
    "ExecutionSpec",
    "FailureCategory",
    "FailureEntry",
    "MRRModel",
    "MaxDisclosure",
    "MethodsPolicy",
    "NetworkPolicy",
    "NetworkPolicyMode",
    "NetworkProfile",
    "NodeManifest",
    "PublicationPolicy",
    "ResearchScore",
    "ResearchScoreStatus",
    "ResourceLimits",
    "RunState",
    "Scope",
    "Sha256",
    "Signature",
    "TaskBundle",
    "TransportMode",
    "UncertaintyEntry",
    "UncertaintyKind",
    "Urn",
]
