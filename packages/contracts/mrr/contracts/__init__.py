"""JSON Schema and Pydantic contracts for externally visible objects and domain events
per docs/spec/03_API_AND_EVENTS.md.

Eighteen hand-written Pydantic v2 models mirror the eighteen entity
schemas in schemas/: ``ResearchScore``, ``NodeManifest``, ``TaskBundle``,
``Claim``, ``EvidenceCrate``, ``CorrectionEvent`` (E1-T03), ``RunManifest``
(E2-T05), ``SourceRecord``/``EvidenceAnchor`` (E3-T01), ``SourceFamily``
(E3-T03), ``VerificationResult`` (E3-T04), ``ModelProfile``/
``ModelInvocation`` (E4-T01), ``Hypothesis`` (E4-T03),
``SkepticalChallenge`` (E4-T04), ``Practice`` (E5-T01),
``NodeMessageEnvelope`` (E5-T03), and ``OfflineBundle`` (E5-T06). Shared
building blocks mirroring ``schemas/common.schema.json`` live in
``mrr.contracts.common`` and are re-exported here too (``Signature``,
``ArtifactRef``, ``Scope``, ``Budget``, ``BaseObject``, ``MRRModel``).

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
from mrr.contracts.evidence_anchor import (
    AnchorKind,
    AnchorValidationStatus,
    ComputationalSelector,
    EvidenceAnchor,
    EvidenceRelation,
    RecomputationStatus,
    TextLocator,
)
from mrr.contracts.evidence_crate import (
    EnvironmentInfo,
    EvidenceCrate,
    FailureCategory,
    FailureEntry,
    RunState,
)
from mrr.contracts.hypothesis import (
    BRANCH_ROLES,
    BranchRole,
    Hypothesis,
    HypothesisStatus,
    check_falsifiability,
)
from mrr.contracts.model_invocation import (
    ModelInvocation,
    ModelToolCall,
    OperationKind,
    RedactionPolicy,
    TerminalStatus,
    TokenUsage,
    ToolCallStatus,
)
from mrr.contracts.model_profile import ModelProfile, compute_config_hash
from mrr.contracts.node_manifest import (
    CapabilityDefinition,
    NetworkProfile,
    NodeManifest,
    TransportMode,
)
from mrr.contracts.node_message_envelope import NodeMessageEnvelope
from mrr.contracts.offline_bundle import BundleEncryption, BundleEntry, OfflineBundle
from mrr.contracts.practice import DisclosureAndTrust, Practice, PublicKeyDescriptor
from mrr.contracts.research_score import (
    MaxDisclosure,
    MethodsPolicy,
    PublicationPolicy,
    ResearchScore,
    ResearchScoreStatus,
)
from mrr.contracts.run_manifest import RunCost, RunManifest, RunResourceUsage
from mrr.contracts.skeptical_challenge import CHALLENGE_TYPES, ChallengeType, SkepticalChallenge
from mrr.contracts.source_family import RelationshipType, SourceFamily
from mrr.contracts.source_record import SourceClassification, SourceIdentifiers, SourceRecord
from mrr.contracts.task_bundle import (
    CapabilityRef,
    DataAccessMode,
    ExecutionSpec,
    NetworkPolicy,
    NetworkPolicyMode,
    ResourceLimits,
    TaskBundle,
    TaskBundleStatus,
)
from mrr.contracts.verification_result import (
    Finding,
    FindingSeverity,
    IndependenceProfile,
    NumericRecomputation,
    Recommendation,
    TargetKind,
    VerificationResult,
    VerificationType,
)

__all__ = [
    "AffectedObjectRef",
    "AnchorKind",
    "AnchorValidationStatus",
    "ApiVersion",
    "ApprovalMode",
    "ArtifactRef",
    "AutonomyLevel",
    "BRANCH_ROLES",
    "BaseObject",
    "BranchRole",
    "Budget",
    "BundleEncryption",
    "BundleEntry",
    "CHALLENGE_TYPES",
    "CapabilityDefinition",
    "CapabilityRef",
    "ChallengeType",
    "Claim",
    "ClaimStatus",
    "ClaimType",
    "Classification",
    "ComputationalSelector",
    "CorrectionEvent",
    "CorrectionSeverity",
    "CorrectionStatus",
    "CorrectionType",
    "DataAccessMode",
    "DisclosureAndTrust",
    "EnvironmentInfo",
    "EvidenceAnchor",
    "EvidenceCrate",
    "EvidenceRelation",
    "ExecutionSpec",
    "FailureCategory",
    "FailureEntry",
    "Finding",
    "FindingSeverity",
    "Hypothesis",
    "HypothesisStatus",
    "IndependenceProfile",
    "MRRModel",
    "MaxDisclosure",
    "MethodsPolicy",
    "ModelInvocation",
    "ModelProfile",
    "ModelToolCall",
    "NetworkPolicy",
    "NetworkPolicyMode",
    "NetworkProfile",
    "NodeManifest",
    "NodeMessageEnvelope",
    "NumericRecomputation",
    "OfflineBundle",
    "OperationKind",
    "Practice",
    "PublicKeyDescriptor",
    "PublicationPolicy",
    "RecomputationStatus",
    "Recommendation",
    "RedactionPolicy",
    "RelationshipType",
    "ResearchScore",
    "ResearchScoreStatus",
    "ResourceLimits",
    "RunCost",
    "RunManifest",
    "RunResourceUsage",
    "RunState",
    "Scope",
    "Sha256",
    "Signature",
    "SkepticalChallenge",
    "SourceClassification",
    "SourceFamily",
    "SourceIdentifiers",
    "SourceRecord",
    "TargetKind",
    "TaskBundle",
    "TaskBundleStatus",
    "TerminalStatus",
    "TextLocator",
    "TokenUsage",
    "ToolCallStatus",
    "TransportMode",
    "UncertaintyEntry",
    "UncertaintyKind",
    "Urn",
    "VerificationResult",
    "VerificationType",
    "check_falsifiability",
    "compute_config_hash",
]
