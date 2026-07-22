"""JSON Schema and Pydantic contracts for externally visible objects and domain events
per docs/spec/03_API_AND_EVENTS.md.

Twenty-nine hand-written Pydantic v2 models mirror the twenty-nine entity
schemas in schemas/: ``ResearchScore``, ``NodeManifest``, ``TaskBundle``,
``Claim``, ``EvidenceCrate``, ``CorrectionEvent`` (E1-T03), ``RunManifest``
(E2-T05), ``SourceRecord``/``EvidenceAnchor`` (E3-T01), ``SourceFamily``
(E3-T03), ``VerificationResult`` (E3-T04), ``ModelProfile``/
``ModelInvocation`` (E4-T01), ``Hypothesis`` (E4-T03),
``SkepticalChallenge`` (E4-T04), ``Practice`` (E5-T01),
``NodeMessageEnvelope`` (E5-T03), ``OfflineBundle`` (E5-T06),
``TransferContract`` (E6-T01), ``Obligation`` (E6-T02),
``MethodProfile`` (task-packets/K0-T01.yaml — the Research Method Kernel's
first task, numbered as a K epic rather than an E epic; NOT
``ModelProfile``, see ``mrr.contracts.method_profile``'s module docstring),
``CorrectionNotification`` (E6-T03), ``CorrectionResponse`` (E6-T04), and
``QuestionModel``/``ConceptCharter``/``MethodProtocol``/``EvidenceMatrix``/
``MethodRuling``/``ResearchDecision`` (task-packets/K1-T01.yaml — kernel
governance contracts; NOT ``MethodProfile``, a distinct entity from
K0-T01). Shared building blocks mirroring ``schemas/common.schema.json``
live in ``mrr.contracts.common`` and are re-exported here too
(``Signature``, ``ArtifactRef``, ``Scope``, ``Budget``, ``BaseObject``,
``MRRModel``).

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
    BaseObjectClassification,
    Budget,
    Classification,
    MRRModel,
    Scope,
    Sha256,
    Signature,
    Urn,
)
from mrr.contracts.concept_charter import ConceptCharter, ConceptCharterEntry, ConceptCharterStatus
from mrr.contracts.correction_event import (
    AffectedObjectRef,
    CorrectionEvent,
    CorrectionSeverity,
    CorrectionStatus,
    CorrectionType,
)
from mrr.contracts.correction_notification import CorrectionNotification
from mrr.contracts.correction_response import (
    CorrectionResponse,
    CorrectionResponseAdaptation,
    CorrectionResponseDecision,
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
from mrr.contracts.evidence_matrix import (
    EvidenceMatrix,
    EvidenceMatrixRow,
    EvidenceMatrixStatus,
    EvidenceMatrixVerificationStatus,
    SensitivityAnalysisResult,
)
from mrr.contracts.hypothesis import (
    BRANCH_ROLES,
    BranchRole,
    Hypothesis,
    HypothesisStatus,
    check_falsifiability,
)
from mrr.contracts.method_profile import (
    CLAIM_CEILING_ORDER,
    ClaimCeiling,
    ExecutorStepDeclaration,
    ExecutorStepKind,
    MethodProfile,
    MethodProfileStatus,
)
from mrr.contracts.method_protocol import MethodProtocol, MethodProtocolStatus, ProtocolAmendment
from mrr.contracts.method_ruling import (
    HumanReviewReference,
    MethodRuling,
    MethodRulingStatus,
    RulingBasis,
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
from mrr.contracts.obligation import Obligation, ObligationStatus
from mrr.contracts.offline_bundle import BundleEncryption, BundleEntry, OfflineBundle
from mrr.contracts.practice import DisclosureAndTrust, Practice, PublicKeyDescriptor
from mrr.contracts.question_model import QuestionModel, QuestionModelStatus
from mrr.contracts.release_record import (
    Approval,
    Bundle,
    BundleFile,
    PersonUrn,
    ReleaseApprovalMode,
    ReleaseRecord,
    ReleaseStatus,
)
from mrr.contracts.release_record import Disclosure as ReleaseDisclosure
from mrr.contracts.research_decision import (
    ResearchDecision,
    ResearchDecisionStatus,
    ResearchDecisionType,
)
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
from mrr.contracts.transfer_contract import (
    ObligationKind,
    ObligationStub,
    TransferContract,
    TransferredObjectRef,
    TransferStatus,
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
    "Approval",
    "ApprovalMode",
    "ArtifactRef",
    "AutonomyLevel",
    "BRANCH_ROLES",
    "BaseObject",
    "BaseObjectClassification",
    "BranchRole",
    "Budget",
    "Bundle",
    "BundleFile",
    "BundleEncryption",
    "BundleEntry",
    "CHALLENGE_TYPES",
    "CLAIM_CEILING_ORDER",
    "CapabilityDefinition",
    "CapabilityRef",
    "ChallengeType",
    "Claim",
    "ClaimCeiling",
    "ClaimStatus",
    "ClaimType",
    "Classification",
    "ComputationalSelector",
    "ConceptCharter",
    "ConceptCharterEntry",
    "ConceptCharterStatus",
    "CorrectionEvent",
    "CorrectionNotification",
    "CorrectionResponse",
    "CorrectionResponseAdaptation",
    "CorrectionResponseDecision",
    "CorrectionSeverity",
    "CorrectionStatus",
    "CorrectionType",
    "DataAccessMode",
    "DisclosureAndTrust",
    "EnvironmentInfo",
    "EvidenceAnchor",
    "EvidenceCrate",
    "EvidenceMatrix",
    "EvidenceMatrixRow",
    "EvidenceMatrixStatus",
    "EvidenceMatrixVerificationStatus",
    "EvidenceRelation",
    "ExecutionSpec",
    "ExecutorStepDeclaration",
    "ExecutorStepKind",
    "FailureCategory",
    "FailureEntry",
    "Finding",
    "FindingSeverity",
    "HumanReviewReference",
    "Hypothesis",
    "HypothesisStatus",
    "IndependenceProfile",
    "MRRModel",
    "MaxDisclosure",
    "MethodProfile",
    "MethodProfileStatus",
    "MethodProtocol",
    "MethodProtocolStatus",
    "MethodRuling",
    "MethodRulingStatus",
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
    "Obligation",
    "ObligationKind",
    "ObligationStatus",
    "ObligationStub",
    "OfflineBundle",
    "OperationKind",
    "Practice",
    "ProtocolAmendment",
    "PersonUrn",
    "PublicKeyDescriptor",
    "PublicationPolicy",
    "QuestionModel",
    "QuestionModelStatus",
    "RecomputationStatus",
    "Recommendation",
    "RedactionPolicy",
    "RelationshipType",
    "ReleaseApprovalMode",
    "ReleaseDisclosure",
    "ReleaseRecord",
    "ReleaseStatus",
    "ResearchDecision",
    "ResearchDecisionStatus",
    "ResearchDecisionType",
    "ResearchScore",
    "ResearchScoreStatus",
    "ResourceLimits",
    "RulingBasis",
    "RunCost",
    "RunManifest",
    "RunResourceUsage",
    "RunState",
    "Scope",
    "SensitivityAnalysisResult",
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
    "TransferContract",
    "TransferStatus",
    "TransferredObjectRef",
    "TransportMode",
    "UncertaintyEntry",
    "UncertaintyKind",
    "Urn",
    "VerificationResult",
    "VerificationType",
    "check_falsifiability",
    "compute_config_hash",
]
