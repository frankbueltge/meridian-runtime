"""``SystematicEvidenceSynthesisExecutor`` — the ``systematic_evidence_synthesis``
v1 executor task family (task-packets/K1-T03.yaml,
docs/spec/08_RESEARCH_METHOD_KERNEL.md section 5, capability name
``"mrr.method.systematic_evidence_synthesis/1"``, the exact capability
``examples/method-profile.example.json`` already declares).

--- Scope: this module builds candidates; it persists nothing ---------------

Exactly mirroring ``mrr.services.node_runtime.executor.ReferenceTaskExecutor``'s
own scope note: this ``Executor`` implementation is a PURE, DB-free,
in-process transform over already-resolved ``inputs: Mapping[str, bytes]``.
It does NOT construct or persist any ``EvidenceMatrix``/``MethodRuling``/
``Claim``/``ResearchDecision``/``EvidenceCrate`` object, does not call
``ObjectRepository``/``EventLog``, and imports no SQLAlchemy driver. A
SEPARATE, new composition function,
``mrr.services.cli.synthesis_orchestration.run_synthesis_evidence_loop``,
deserializes this executor's JSON output and is the ONLY place that mints
real object identities and persists anything — see that module's own
docstring for the full orchestration choreography.

--- Why execute() never mints an id --------------------------------------

``execute()`` must be idempotent per ``(task_bundle.id, task_bundle.revision,
execution_attempt)`` (MRR-FR-035): the SAME inputs must always produce the
SAME output bytes, byte for byte, indefinitely. Random ULID minting
(``mrr.domain.identity.new_urn``) would break that guarantee outright. This
executor's own JSON output therefore never contains a freshly-minted object
id anywhere — every corpus entry is addressed purely by its own
caller-supplied ``entry_id`` (a plain string key, not a URN), and it is
``run_synthesis_evidence_loop`` — which is NOT required to be pure or
idempotent in this same sense, exactly like ``run_local_evidence_loop``
mints ``new_urn("artifact")``/``new_urn("evidence-crate")`` AFTER
``execute()`` returns — that mints every real ``SourceRecord``/
``EvidenceAnchor``/``Claim`` id, keyed by ``entry_id``, when it deserializes
this output.

--- The six deterministic steps (MTH-016 declaration, verbatim) -------------

``examples/method-profile.example.json``'s already-accepted seven
``executor_steps`` (K0-T01, not redesigned here): six deterministic —
``snapshot_loading_and_hash_verification`` (a no-op beyond parsing:
``ArtifactStore.get()``'s own content-addressing already guarantees the
bytes handed to ``inputs`` hash to their declared key; nothing further to
verify here), ``inclusion_filtering``, ``matrix_assembly``,
``independence_validation`` (``mrr.domain.source_independence``, NOT
E3-T05's reviewer-independence module — see that module's own docstring),
``eligibility_and_ceiling_rules``, ``crate_sealing`` (read here as "package
output into the UNCHANGED ``EvidenceCrateSealer.seal()`` call's expected
input shape" — this executor never calls the sealer itself, see
specification_gaps) — plus one OPTIONAL model-assisted step,
``extraction_and_classification_proposal``, wired via an injected,
never-required callable (derived_decisions (m); see
``build_model_assisted_extraction_callable`` below for the separately-tested
reference implementation).

--- Kill conditions are a SUCCESSFUL terminal result, never a failure -------

MRR-MTH-011: ``execute()`` NEVER returns a non-``completed`` outcome because
of insufficient evidence. When an ``applies_to_analysis`` group's included,
``"verified"`` row count is strictly below the protocol-parameters sidecar's
own ``kill_conditions.stop_insufficient_evidence.min_included_sources``, the
JSON output records a ``"decision"`` block for that analysis
(``decision_type: "stop_insufficient_evidence"``) and mints NO claim
candidate for it — the run itself still completes normally.

--- MTH-007 is enforced here first, and fails the RUN, not the finding ------

By contrast, an unlocked or hash-mismatched ``MethodProtocol`` (MRR-MTH-007)
is a genuine execution PRECONDITION failure, not a research finding: the
internal ``_check_protocol_lock`` helper below raises
``mrr.domain.exceptions.ProtocolNotLockedError``/``ProtocolLockViolationError``
— provably, at the unit level, exactly as task-packets/K1-T03.yaml's own
acceptance test describes ("execute() ... raises ProtocolNotLockedError...
raises ProtocolLockViolationError"). ``execute()`` ITSELF never raises,
though (the unchanged ``Executor`` Protocol contract, "never raise for a
task-level outcome"): its own outer exception handling — mirroring
``ReferenceTaskExecutor._execute_uncached``'s identical "any raising
computation -> failed" precedent — catches every exception the internal
pipeline raises, including these two, and reports a ``failed``
``ExecutionResult`` whose ``detail`` names the exact typed error and its
canonical ``error_code``. A unit test calling the internal helper directly
proves the typed raise; a unit test calling ``execute()`` proves the
resulting terminal outcome. Both are pinned, satisfying the acceptance
test's own wording without violating the Protocol's "never raise" contract.

--- MTH-018: sensitivity variations are executed here (task-packets/K1-T03b.yaml) ---

Where a locked protocol declares ``sensitivity_variations`` (MRR-MTH-018),
each declared entry is executed by RE-RUNNING ONLY the four deterministic
classification stages (``_passes_inclusion_filter``,
``_group_included_rows_by_analysis``, independence counting via the
UNCHANGED ``mrr.domain.source_independence``, and ``_classify_analysis``)
against the SAME already-parsed, already-extracted ``rows`` the base run
already computed — the extraction step is NEVER re-invoked per variation
(``_resolve_variation_rows`` copies each row's own ``extraction`` dict
verbatim). A new per-variation sidecar, ``SensitivityVariationParameters``,
mirrors ``ProtocolParameters``' own precedent exactly; a new fail-closed
precondition check, ``_check_sensitivity_variation_coverage``, runs right
after ``_check_protocol_lock`` and raises
``mrr.domain.exceptions.SensitivityVariationDeclarationMismatchError`` — the
SAME "precondition, not a finding" framing as MTH-007 above — when the
protocol's own declared set and the caller's own supplied artifact-id set
disagree in EITHER direction. Results land as one more key,
``"sensitivity_analysis_results"``, in the SAME canonicalized output dict —
see ``_run_sensitivity_variations``'s own docstring for the full design,
including why a variation-emptied group is never fed to ``_classify_analysis``
(the empty-``group_rows`` guard).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Any, ClassVar, Literal, Self

from mrr.contracts import TaskBundle, Urn
from mrr.crypto.canonical import canonicalize
from mrr.crypto.hashing import content_hash
from mrr.domain.exceptions import (
    ProtocolLockViolationError,
    ProtocolNotLockedError,
    SensitivityVariationDeclarationMismatchError,
)
from mrr.domain.model_adapter import ModelAdapter, ModelInvocationRequest
from mrr.domain.source_independence import distinct_independent_source_family_count
from mrr.services.node_runtime.executor import (
    CancellationCheck,
    Clock,
    ExecutionResult,
    PolicyGate,
    ResourceUsage,
    TerminalOutcome,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "CAPABILITY_NAME",
    "CorpusEntry",
    "CorpusSourceIdentifiers",
    "DETERMINISTIC_RULE_REFERENCE",
    "ExtractionCallable",
    "ExtractionOutcome",
    "ProtocolParameters",
    "RULED_CEILING",
    "SensitivityVariationParameters",
    "SystematicEvidenceSynthesisExecutor",
    "build_model_assisted_extraction_callable",
]

#: The exact capability name ``examples/method-profile.example.json``
#: (K0-T01, already accepted) declares in ``executor_task_family`` — reused
#: verbatim, never redesigned here.
CAPABILITY_NAME = "mrr.method.systematic_evidence_synthesis/1"

#: MethodRuling.deterministic_rule_reference (derived_decisions (f)): a
#: fixed, versioned string naming the exact rule this module implements, so
#: a future revision of the rule is distinguishable in the historical
#: record.
DETERMINISTIC_RULE_REFERENCE = "k1-t03.eligibility_and_ceiling_rules.v1"

#: Spec 08 section 5's own text: "Maximum ceiling: associational_unadjusted"
#: — this profile's single strongest licensable tier. Every claim candidate
#: this executor proposes that clears its eligibility floor is ruled to
#: EXACTLY this ceiling, never any finer graduation (see the module
#: docstring / task-packets/K1-T03.yaml specification_gaps).
RULED_CEILING = "associational_unadjusted"

#: TaskBundle.instructions keys this executor reads to learn which resolved
#: ``inputs`` key is which — see derived_decisions (b)/(g).
_INSTRUCTIONS_CORPUS_KEY = "corpus_artifact_id"
_INSTRUCTIONS_PROTOCOL_PARAMETERS_KEY = "protocol_parameters_artifact_id"
_INSTRUCTIONS_METHOD_PROTOCOL_KEY = "method_protocol_artifact_id"
_INSTRUCTIONS_QUESTION_ID_KEY = "question_id"

#: task-packets/K1-T03b.yaml derived_decisions (b): one more optional
#: instructions key, mirroring the four keys above exactly —
#: ``variation_entry_id -> artifact_id`` for every declared
#: ``MethodProtocol.sensitivity_variations`` entry a caller supplies a
#: sidecar for. Absent/empty is the overwhelmingly common case (every
#: existing ``sensitivity_variations: []`` protocol).
_INSTRUCTIONS_SENSITIVITY_VARIATION_ARTIFACT_IDS_KEY = "sensitivity_variation_artifact_ids"

#: MethodProtocol.status values MRR-MTH-007 treats as "locked enough" for
#: confirmatory work — derived_decisions (g): status "locked" itself, plus
#: "amended"/"executed" (both carry the lock fields forward per
#: mrr.contracts.method_protocol's own co-occurrence validator).
_LOCK_SATISFYING_STATUSES = frozenset({"locked", "amended", "executed"})

#: The two evidence relations this module treats as moving the eligibility
#: needle (derived_decisions, this module's own reading) — "qualifies"/
#: "contextualizes" rows are still included in the matrix but count toward
#: neither the supporting nor the contradicting independence bucket.
_SUPPORTING_RELATION = "supports"
_CONTRADICTING_RELATION = "contradicts"

#: The reserved mrr.contracts.evidence_matrix.EvidenceMatrixRow.extraction
#: key MTH-016's "verification disposition" is recorded at (derived_decisions
#: (c)) — no dedicated contract field exists for it.
_VERIFICATION_DISPOSITION_KEY = "_verification_disposition"


# ---------------------------------------------------------------------------
# Parsed input shapes — this executor's own Pydantic models, validated only
# here (not schematized under schemas/), per derived_decisions (b) and
# specification_gaps.
# ---------------------------------------------------------------------------


class CorpusSourceIdentifiers(BaseModel):
    """Mirrors ``mrr.contracts.source_record.SourceIdentifiers`` field-for-field."""

    model_config = ConfigDict(extra="forbid")

    doi: str | None = None
    repository_id: str | None = None
    archive_id: str | None = None
    local_asset_id: str | None = None


class CorpusEntry(BaseModel):
    """One entry of the corpus-snapshot artifact — a small, human-curated
    source excerpt shaped to become one ``SourceRecord`` plus one
    ``EvidenceMatrixRow`` (derived_decisions (b)(i)).

    ``entry_id`` is this executor's own portable, non-URN key (see the
    module docstring's "Why execute() never mints an id" section) — unique
    within one corpus snapshot, enforced by
    ``ProtocolParametersOrCorpusError``-free structural validation in
    ``_parse_corpus`` below.

    ``applies_to_analysis``/``claim_type`` are this module's own minimal,
    human-supplied extensions beyond the SourceRecord/EvidenceMatrixRow
    fields (specification_gaps): every entry contributing evidence for a
    given ``applies_to_analysis`` group MUST declare the SAME ``claim_type``
    (enforced in ``_group_entries_by_analysis``), so the group's single
    resulting claim candidate has one unambiguous ``claim_type``.
    """

    model_config = ConfigDict(extra="forbid")

    entry_id: str = Field(min_length=1)
    applies_to_analysis: str = Field(min_length=1)
    claim_type: Literal["observational", "interpretive"]
    evidence_relation: Literal["supports", "contradicts", "qualifies", "contextualizes"]
    verification_status: Literal["verified", "unverifiable", "pending"]
    unverifiable_reason: str | None = None
    claim_relevant_finding: str = Field(min_length=1)
    extraction: dict[str, str] = Field(default_factory=dict)
    source_family_id: str | None = None

    # SourceRecord-shaped fields (mrr.contracts.source_record.SourceRecord).
    identifiers: CorpusSourceIdentifiers = Field(default_factory=CorpusSourceIdentifiers)
    title: str = Field(min_length=1)
    creators: list[str] = Field(default_factory=list)
    publication_date: str | None = None
    version: str | None = None
    retrieval_timestamp: str = Field(min_length=1)
    retrieval_method: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    primary_secondary_derived: Literal["primary", "secondary", "derived"]
    derivation_evidence: str | None = None
    accessibility: dict[str, str] | None = None
    licensing: dict[str, str] | None = None

    @model_validator(mode="after")
    def _unverifiable_requires_reason(self) -> Self:
        """Mirrors ``EvidenceMatrixRow``'s own identical validator (MRR-MTH-015)
        — checked here too, at PARSE time, so a malformed corpus entry fails
        closed before any pipeline step runs, not only later when
        orchestration builds the real ``EvidenceMatrixRow``.
        """
        if self.verification_status == "unverifiable" and not self.unverifiable_reason:
            raise ValueError(
                f"corpus entry {self.entry_id!r}: verification_status 'unverifiable' requires "
                "a non-null, non-empty unverifiable_reason (MRR-MTH-015)"
            )
        return self


class _InclusionFieldPredicate(BaseModel):
    """One ``inclusion_filter`` predicate: a corpus entry is INCLUDED for
    this field exactly when its own value for ``field_name`` (looked up by
    the sidecar's own dict key) is a member of ``allowed_values`` — the
    machine-checkable, structured predicate derived_decisions (b) requires
    in place of ``MethodProtocol.inclusion_criteria``'s free prose.
    """

    model_config = ConfigDict(extra="forbid")

    allowed_values: list[str] = Field(min_length=1)


class _EligibilityRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_independent_source_families: int = Field(ge=0)


class _KillConditionInsufficientEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_included_sources: int = Field(ge=0)


class _KillConditions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stop_insufficient_evidence: _KillConditionInsufficientEvidence


class ProtocolParameters(BaseModel):
    """The protocol-parameters sidecar (derived_decisions (b)(ii)): a small,
    versioned, content-hash-pinned JSON object carrying the machine-checkable
    parameterization ``MethodProtocol``'s own K1-T01 schema cannot itself
    carry (every one of ``inclusion_criteria``/``exclusion_criteria``/
    ``kill_conditions`` is free HUMAN PROSE, ``list[str]``, not a structured
    predicate or threshold table).

    ``eligibility_rules`` MUST declare exactly the two outcome keys this
    module's own eligibility algorithm reads — ``"supported"`` and
    ``"contested"`` (see ``_classify_analysis`` below) — enforced by
    ``_requires_supported_and_contested_rules``.

    ``non_applicability_conditions`` is this module's own small, disclosed
    extension: a non-empty list of human-authored MTH-017 non-applicability
    statements, required because ``ruled_ceiling`` (``RULED_CEILING``,
    "associational_unadjusted") always sits above "descriptive" in
    ``CLAIM_CEILING_ORDER``, so every ``MethodRuling`` this run issues MUST
    carry at least one (mrr.contracts.method_ruling's own validator).
    """

    model_config = ConfigDict(extra="forbid")

    protocol_id: str = Field(min_length=1)
    protocol_lock_content_hash: str = Field(min_length=1)
    inclusion_filter: dict[str, _InclusionFieldPredicate] = Field(default_factory=dict)
    eligibility_rules: dict[str, _EligibilityRule]
    kill_conditions: _KillConditions
    non_applicability_conditions: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _requires_supported_and_contested_rules(self) -> Self:
        missing = {"supported", "contested"} - set(self.eligibility_rules)
        if missing:
            raise ValueError(
                f"protocol-parameters sidecar eligibility_rules is missing required key(s) "
                f"{sorted(missing)!r} (this module's eligibility algorithm requires both "
                "'supported' and 'contested')"
            )
        return self


class SensitivityVariationParameters(BaseModel):
    """The per-variation sidecar (task-packets/K1-T03b.yaml derived_decisions
    (b)): one instance per declared ``MethodProtocol.sensitivity_variations``
    entry, mirroring ``ProtocolParameters``' own precedent exactly —
    ``sensitivity_variations`` is, like every other
    ``MethodProtocol.list[str]`` field, free human prose with no
    machine-executable content of its own (MRR-MTH-018).

    ``protocol_id``/``protocol_lock_content_hash`` are checked against the
    resolved protocol via the SAME, UNCHANGED ``_check_protocol_lock`` every
    base run already uses (a stale or mismatched variation sidecar fails
    exactly like a stale base-run sidecar does today, the same typed errors,
    ``ProtocolNotLockedError``/``ProtocolLockViolationError``).
    ``variation_entry_id`` is self-describing and checked against the
    instructions key it is resolved under (``_run_sensitivity_variations``).

    ``inclusion_filter``/``eligibility_rules``/``kill_conditions`` reuse
    ``ProtocolParameters``' own nested Pydantic classes BY REFERENCE (zero
    duplication) — a variation may vary any or all of the three. The one
    genuinely NEW field beyond ``ProtocolParameters``' own shape,
    ``source_family_overrides``, is the concrete mechanism derived_decisions
    (a) names for varying "party families"/"cluster taxonomies": an
    ``entry_id -> alternate source_family_id`` mapping, applied per corpus
    row before independence counting (``source_independence.family_key``
    reads only ``row.source_family_id``, so overriding that one field per
    entry is a minimal, already-supported lever requiring zero change to
    ``mrr.domain.source_independence`` itself).
    """

    model_config = ConfigDict(extra="forbid")

    protocol_id: str = Field(min_length=1)
    protocol_lock_content_hash: str = Field(min_length=1)
    variation_entry_id: str = Field(min_length=1)
    inclusion_filter: dict[str, _InclusionFieldPredicate] = Field(default_factory=dict)
    eligibility_rules: dict[str, _EligibilityRule]
    kill_conditions: _KillConditions
    source_family_overrides: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _requires_supported_and_contested_rules(self) -> Self:
        missing = {"supported", "contested"} - set(self.eligibility_rules)
        if missing:
            raise ValueError(
                f"sensitivity-variation-parameters sidecar eligibility_rules is missing "
                f"required key(s) {sorted(missing)!r} (this module's eligibility algorithm "
                "requires both 'supported' and 'contested')"
            )
        return self


# ---------------------------------------------------------------------------
# Model-assisted extraction (derived_decisions (m)) — injected, OPTIONAL,
# never required for v1's own model-free acceptance path.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExtractionOutcome:
    """The result of one model-assisted extraction attempt over one
    ``CorpusEntry`` — MTH-016's "verification disposition" recorded
    alongside the extraction dict itself.
    """

    extraction: dict[str, str]
    verification_disposition: Literal["verified", "downgraded-to-proposal", "rejected"]


#: Mirrors ``mrr.services.node_runtime.executor.ReferenceTransform``'s own
#: "injectable callable of a fixed shape" precedent — ``None`` (v1's own
#: default) means "genuinely model-free": every row's extraction comes
#: entirely from the corpus snapshot's own human-supplied data.
ExtractionCallable = Callable[[CorpusEntry, Sequence[str]], ExtractionOutcome]


def build_model_assisted_extraction_callable(
    adapter: ModelAdapter,
    *,
    model_profile_id: Urn,
    model_profile_hash: str,
    max_repair_attempts: int = 1,
) -> ExtractionCallable:
    """A REAL (not stubbed) reference ``ExtractionCallable`` wrapping
    ``mrr.adapters.llm.structured_generation.generate_structured`` (E4-T02,
    unchanged) — the ``extraction_and_classification_proposal`` step, WHEN a
    caller chooses to exercise it. NEVER invoked by this module's own
    model-free acceptance path (``extraction_callable=None`` on
    ``SystematicEvidenceSynthesisExecutor``); a separate, dedicated set of
    unit tests exercises this factory directly against a scripted fake
    ``ModelAdapter`` (never a real provider or network call).

    One ``generate_structured`` call per corpus row, targeting a small,
    dynamically-built Pydantic model whose fields mirror
    ``MethodProtocol.extraction_fields`` as plain optional strings. On
    ``StructuredGenerationResult.status == "proposal"``, the proposal's own
    field values become the row's ``extraction`` dict and
    ``verification_disposition == "verified"``; on any other status, the
    row's OWN human-supplied ``extraction`` dict is kept unchanged and
    ``verification_disposition == "downgraded-to-proposal"`` — this callable
    never returns ``"rejected"`` on its own (that disposition is reserved for
    a FUTURE caller-side verification step this module does not implement;
    see task-packets/K1-T03.yaml specification_gaps).
    """
    from mrr.adapters.llm.structured_generation import generate_structured

    def _extract(entry: CorpusEntry, extraction_fields: Sequence[str]) -> ExtractionOutcome:
        target_model = _build_extraction_target_model(extraction_fields)
        request = ModelInvocationRequest(
            model_profile_id=model_profile_id,
            model_profile_hash=model_profile_hash,
            prompt_text=(
                f"Extract the following fields from this source excerpt: "
                f"{', '.join(extraction_fields)}.\n\nTitle: {entry.title}\n"
                f"Finding: {entry.claim_relevant_finding}"
            ),
            operation_kind="stochastic",
            redaction_policy="hashes_only",
        )
        result = generate_structured(
            adapter, request, target_model, max_repair_attempts=max_repair_attempts
        )
        if result.status == "proposal" and result.proposal is not None:
            proposal_dict = {
                field_name: value
                for field_name, value in result.proposal.model_dump().items()
                if value is not None
            }
            return ExtractionOutcome(extraction=proposal_dict, verification_disposition="verified")
        return ExtractionOutcome(
            extraction=dict(entry.extraction), verification_disposition="downgraded-to-proposal"
        )

    return _extract


def _build_extraction_target_model(extraction_fields: Sequence[str]) -> type[BaseModel]:
    """A small, dynamically-built Pydantic model whose fields mirror
    ``extraction_fields`` (``MethodProtocol.extraction_fields``) as plain
    optional strings — the caller-defined target model
    ``build_model_assisted_extraction_callable``'s own docstring names.
    """
    from pydantic import create_model

    fields: dict[str, Any] = dict.fromkeys(extraction_fields, (str | None, None))
    model: type[BaseModel] = create_model(
        "ExtractionProposal", __config__=ConfigDict(extra="forbid"), **fields
    )
    return model


# ---------------------------------------------------------------------------
# Pure pipeline internals.
# ---------------------------------------------------------------------------


def _check_protocol_lock(protocol_body: Mapping[str, Any], params: ProtocolParameters) -> None:
    """MRR-MTH-007, derived_decisions (g) — the first real enforcement site
    for this requirement in this codebase. Raises before any further
    deterministic step runs; see the module docstring for why this raises
    from a pure, in-memory comparison, and how ``execute()`` converts it to
    a ``failed`` terminal outcome without violating the ``Executor``
    Protocol's own "never raise" contract.
    """
    protocol_id = str(protocol_body.get("id", params.protocol_id))
    status = protocol_body.get("status")
    if status not in _LOCK_SATISFYING_STATUSES:
        raise ProtocolNotLockedError(protocol_id, actual_status=str(status))

    actual_content_hash = str(protocol_body.get("content_hash"))
    if actual_content_hash != params.protocol_lock_content_hash:
        raise ProtocolLockViolationError(
            protocol_id,
            declared_content_hash=params.protocol_lock_content_hash,
            actual_content_hash=actual_content_hash,
        )


def _check_sensitivity_variation_coverage(
    protocol_body: Mapping[str, Any], instructions: Mapping[str, Any]
) -> dict[str, str]:
    """MRR-MTH-018, task-packets/K1-T03b.yaml derived_decisions (b) — a
    SYMMETRIC fail-closed precondition check, called immediately after
    ``_check_protocol_lock`` (i.e. before the corpus is even parsed — a
    precondition check, not a research finding, exactly mirroring
    ``_check_protocol_lock``'s own placement and rationale).

    The locked protocol's own declared ``sensitivity_variations`` set MUST
    exactly equal the set of ``variation_entry_id`` keys the caller supplied
    via ``instructions[_INSTRUCTIONS_SENSITIVITY_VARIATION_ARTIFACT_IDS_KEY]``
    — a declared-but-uncovered variation fails closed (the MUST this whole
    packet exists to satisfy would otherwise be silently unfulfilled), and a
    supplied-but-undeclared variation artifact ALSO fails closed. Trivially
    passes with an empty mapping when both sets are empty — the
    overwhelmingly common case today, ZERO behavior change for every
    existing ``sensitivity_variations: []`` protocol.

    Returns the resolved ``variation_entry_id -> artifact_id`` mapping (never
    ``None``/absent — an empty ``dict`` when there is nothing to run).
    """
    protocol_id = str(protocol_body.get("id", ""))
    declared = frozenset(
        str(entry_id) for entry_id in protocol_body.get("sensitivity_variations", [])
    )
    raw_supplied = instructions.get(_INSTRUCTIONS_SENSITIVITY_VARIATION_ARTIFACT_IDS_KEY, {})
    supplied_map = {str(key): str(value) for key, value in raw_supplied.items()}
    supplied = frozenset(supplied_map)
    if declared != supplied:
        raise SensitivityVariationDeclarationMismatchError(
            protocol_id, declared=declared, supplied=supplied
        )
    return supplied_map


def _passes_inclusion_filter(
    entry: CorpusEntry, inclusion_filter: Mapping[str, _InclusionFieldPredicate]
) -> tuple[bool, str | None]:
    """``(True, None)`` iff ``entry`` satisfies every declared
    ``inclusion_filter`` predicate; otherwise ``(False, <reason>)`` naming
    the first failing field, deterministically (iteration order over a
    Pydantic model's own dict is insertion order, i.e. the sidecar's own
    JSON key order — stable given fixed input bytes).
    """
    entry_fields = entry.model_dump()
    for field_name, predicate in inclusion_filter.items():
        actual_value = entry_fields.get(field_name)
        if actual_value not in predicate.allowed_values:
            return False, (
                f"field {field_name!r} value {actual_value!r} is not one of the declared "
                f"allowed_values {predicate.allowed_values!r}"
            )
    return True, None


@dataclass(frozen=True, slots=True)
class _CorpusRowResult:
    entry: CorpusEntry
    included: bool
    exclusion_reason: str | None
    extraction: dict[str, str]


def _resolve_corpus_rows(
    entries: Sequence[CorpusEntry],
    params: ProtocolParameters,
    extraction_callable: ExtractionCallable | None,
    extraction_fields: Sequence[str],
) -> list[_CorpusRowResult]:
    rows: list[_CorpusRowResult] = []
    for entry in entries:
        included, exclusion_reason = _passes_inclusion_filter(entry, params.inclusion_filter)
        if extraction_callable is None:
            extraction = dict(entry.extraction)
        else:
            outcome = extraction_callable(entry, extraction_fields)
            extraction = dict(outcome.extraction)
            extraction[_VERIFICATION_DISPOSITION_KEY] = outcome.verification_disposition
        rows.append(
            _CorpusRowResult(
                entry=entry,
                included=included,
                exclusion_reason=exclusion_reason,
                extraction=extraction,
            )
        )
    return rows


def _group_included_rows_by_analysis(
    rows: Sequence[_CorpusRowResult],
) -> dict[str, list[_CorpusRowResult]]:
    groups: dict[str, list[_CorpusRowResult]] = {}
    for row in rows:
        if not row.included:
            continue
        groups.setdefault(row.entry.applies_to_analysis, []).append(row)
    for applies_to_analysis, group_rows in groups.items():
        claim_types = {row.entry.claim_type for row in group_rows}
        if len(claim_types) > 1:
            raise ValueError(
                f"analysis {applies_to_analysis!r}: entries declare inconsistent claim_type "
                f"values {sorted(claim_types)!r} — every entry contributing to one analysis "
                "must declare the same claim_type"
            )
    return groups


class _MatrixRowLike:
    """A tiny, structural stand-in satisfying
    ``mrr.domain.source_independence``'s own ``EvidenceMatrixRow`` shape
    requirement (``source_family_id``, ``source_record_id``,
    ``verification_status``) without needing a real, URN-typed
    ``source_record_id`` yet — ``entry_id`` stands in for it, since
    ``distinct_independent_source_family_count`` only ever compares these
    values for equality, never validates them as URNs.
    """

    __slots__ = ("source_family_id", "source_record_id", "verification_status")

    def __init__(
        self, *, source_family_id: str | None, source_record_id: str, verification_status: str
    ) -> None:
        self.source_family_id = source_family_id
        self.source_record_id = source_record_id
        self.verification_status = verification_status


def _as_matrix_row_like(row: _CorpusRowResult) -> _MatrixRowLike:
    return _MatrixRowLike(
        source_family_id=row.entry.source_family_id,
        source_record_id=row.entry.entry_id,
        verification_status=row.entry.verification_status,
    )


@dataclass(frozen=True, slots=True)
class _AnalysisResult:
    applies_to_analysis: str
    claim_type: str | None
    included_source_count: int
    verified_source_count: int
    distinct_independent_supporting_family_count: int
    distinct_independent_contradicting_family_count: int
    outcome: Literal["supported", "contested", "unsupported", "insufficient_evidence"]
    supporting_entry_ids: tuple[str, ...]
    contradicting_entry_ids: tuple[str, ...]
    non_applicability_notes: tuple[str, ...]
    decision_rationale: str | None


def _classify_analysis(
    applies_to_analysis: str, group_rows: Sequence[_CorpusRowResult], params: ProtocolParameters
) -> _AnalysisResult:
    verified_rows = [row for row in group_rows if row.entry.verification_status == "verified"]
    min_included_sources = params.kill_conditions.stop_insufficient_evidence.min_included_sources

    if len(verified_rows) < min_included_sources:
        return _AnalysisResult(
            applies_to_analysis=applies_to_analysis,
            claim_type=None,
            included_source_count=len(group_rows),
            verified_source_count=len(verified_rows),
            distinct_independent_supporting_family_count=0,
            distinct_independent_contradicting_family_count=0,
            outcome="insufficient_evidence",
            supporting_entry_ids=(),
            contradicting_entry_ids=(),
            non_applicability_notes=(),
            decision_rationale=(
                f"{len(verified_rows)} included source(s), below the declared minimum of "
                f"{min_included_sources}"
            ),
        )

    supporting_rows = [
        row for row in group_rows if row.entry.evidence_relation == _SUPPORTING_RELATION
    ]
    contradicting_rows = [
        row for row in group_rows if row.entry.evidence_relation == _CONTRADICTING_RELATION
    ]
    supporting_family_count = distinct_independent_source_family_count(
        _as_matrix_row_like(row) for row in supporting_rows
    )
    contradicting_family_count = distinct_independent_source_family_count(
        _as_matrix_row_like(row) for row in contradicting_rows
    )

    supported_threshold = params.eligibility_rules["supported"].min_independent_source_families
    contested_threshold = params.eligibility_rules["contested"].min_independent_source_families

    notes: list[str] = []
    unverifiable_ids = [
        row.entry.entry_id for row in group_rows if row.entry.verification_status == "unverifiable"
    ]
    if unverifiable_ids:
        notes.append(
            "does not apply to source(s) whose training/verification provenance is "
            f"unverifiable: {sorted(unverifiable_ids)!r}"
        )

    if (
        supporting_family_count >= supported_threshold
        and contradicting_family_count < contested_threshold
    ):
        outcome: Literal["supported", "contested", "unsupported"] = "supported"
        if contradicting_rows:
            notes.append(
                "does not resolve the qualifying/contradicting evidence from source(s) "
                f"{sorted(row.entry.entry_id for row in contradicting_rows)!r}"
            )
    elif (
        supporting_family_count >= contested_threshold
        and contradicting_family_count >= contested_threshold
    ):
        outcome = "contested"
    else:
        outcome = "unsupported"

    claim_type = group_rows[0].entry.claim_type
    return _AnalysisResult(
        applies_to_analysis=applies_to_analysis,
        claim_type=claim_type,
        included_source_count=len(group_rows),
        verified_source_count=len(verified_rows),
        distinct_independent_supporting_family_count=supporting_family_count,
        distinct_independent_contradicting_family_count=contradicting_family_count,
        outcome=outcome,
        supporting_entry_ids=tuple(row.entry.entry_id for row in supporting_rows),
        contradicting_entry_ids=tuple(row.entry.entry_id for row in contradicting_rows),
        non_applicability_notes=tuple(notes),
        decision_rationale=None,
    )


# ---------------------------------------------------------------------------
# Sensitivity-variation execution (task-packets/K1-T03b.yaml, MRR-MTH-018).
# Re-runs ONLY the four classification stages above — never re-invokes
# extraction, never re-fetches the corpus — see derived_decisions (a).
# ---------------------------------------------------------------------------


def _resolve_variation_rows(
    base_rows: Sequence[_CorpusRowResult], variation_params: SensitivityVariationParameters
) -> list[_CorpusRowResult]:
    """Re-run ONLY the inclusion-filter stage over the SAME already-parsed,
    already-extracted base rows (derived_decisions (a)) — ``extraction`` is
    copied verbatim from ``base_row``, never recomputed.
    ``source_family_overrides`` is applied via a per-row COPY of the entry
    (``CorpusEntry.model_copy``), never mutating the base run's own shared
    ``CorpusEntry`` instance that the base run and every other variation
    still read.
    """
    variation_rows: list[_CorpusRowResult] = []
    for base_row in base_rows:
        override = variation_params.source_family_overrides.get(base_row.entry.entry_id)
        entry = (
            base_row.entry
            if override is None
            else base_row.entry.model_copy(update={"source_family_id": override})
        )
        included, exclusion_reason = _passes_inclusion_filter(
            entry, variation_params.inclusion_filter
        )
        variation_rows.append(
            _CorpusRowResult(
                entry=entry,
                included=included,
                exclusion_reason=exclusion_reason,
                extraction=base_row.extraction,
            )
        )
    return variation_rows


def _sensitivity_result_to_dict(
    variation_entry_id: str, result: _AnalysisResult, base_outcome: str
) -> dict[str, Any]:
    return {
        "variation_entry_id": variation_entry_id,
        "applies_to_analysis": result.applies_to_analysis,
        "outcome": result.outcome,
        "included_source_count": result.included_source_count,
        "verified_source_count": result.verified_source_count,
        "distinct_independent_supporting_family_count": (
            result.distinct_independent_supporting_family_count
        ),
        "distinct_independent_contradicting_family_count": (
            result.distinct_independent_contradicting_family_count
        ),
        "decision_rationale": (
            result.decision_rationale if result.outcome == "insufficient_evidence" else None
        ),
        "matches_base_outcome": result.outcome == base_outcome,
    }


def _empty_group_sensitivity_result(
    variation_entry_id: str, applies_to_analysis: str, base_outcome: str
) -> dict[str, Any]:
    """derived_decisions (g): a variation whose own ``inclusion_filter``
    empties a base-run-non-empty group NEVER calls ``_classify_analysis``
    with an empty ``group_rows`` list (that would ``IndexError`` at
    ``_classify_analysis``'s own unsafe ``group_rows[0]`` access whenever a
    variation's own ``kill_conditions.stop_insufficient_evidence.
    min_included_sources`` happens to be ``0`` — a legal value). The
    ``"insufficient_evidence"`` result is synthesized directly here instead,
    with an explicit rationale naming the variation and the zero count.
    """
    rationale = (
        f"sensitivity variation {variation_entry_id!r}: 0 included source(s) for analysis "
        f"{applies_to_analysis!r} under this variation's own inclusion_filter"
    )
    return {
        "variation_entry_id": variation_entry_id,
        "applies_to_analysis": applies_to_analysis,
        "outcome": "insufficient_evidence",
        "included_source_count": 0,
        "verified_source_count": 0,
        "distinct_independent_supporting_family_count": 0,
        "distinct_independent_contradicting_family_count": 0,
        "decision_rationale": rationale,
        "matches_base_outcome": base_outcome == "insufficient_evidence",
    }


def _run_sensitivity_variations(
    protocol_body: Mapping[str, Any],
    base_rows: Sequence[_CorpusRowResult],
    base_outcome_by_analysis: Mapping[str, str],
    base_params: ProtocolParameters,
    variation_artifact_ids: Mapping[str, str],
    inputs: Mapping[str, bytes],
) -> list[dict[str, Any]]:
    """Execute every declared sensitivity variation (derived_decisions (a))
    against the SAME already-parsed, already-extracted base rows — never
    re-invoking extraction, never touching the base run's own claim-minting
    output (``output["analyses"]``, untouched by this function).

    For every ``applies_to_analysis`` key present in the BASE run's own
    analyses (the set of groups worth a variation comparison — derived_
    decisions (g)), and only those, one ``SensitivityAnalysisResult``-shaped
    dict is appended per declared variation, sorted by
    ``(variation_entry_id, applies_to_analysis)`` for determinism (derived_
    decisions (d) — this output feeds the SAME ``canonicalize()`` call the
    base ``analyses`` list does; array element order is significant to
    canonical JSON, unlike object key order).
    """
    results: list[dict[str, Any]] = []
    for variation_entry_id, artifact_id in sorted(variation_artifact_ids.items()):
        variation_params = SensitivityVariationParameters.model_validate_json(inputs[artifact_id])
        if variation_params.variation_entry_id != variation_entry_id:
            raise ValueError(
                f"sensitivity-variation-parameters artifact declared under instructions key "
                f"{variation_entry_id!r} carries its own variation_entry_id "
                f"{variation_params.variation_entry_id!r} instead — the two must match"
            )

        # protocol_lock_content_hash is checked via the UNCHANGED
        # _check_protocol_lock, against THIS variation's own declared
        # protocol_id/hash (derived_decisions (b)) — a real ProtocolParameters
        # instance built from the base sidecar's own fields plus this
        # variation's own overrides, so _check_protocol_lock's own signature
        # and behavior are exercised completely unmodified.
        effective_params = base_params.model_copy(
            update={
                "protocol_id": variation_params.protocol_id,
                "protocol_lock_content_hash": variation_params.protocol_lock_content_hash,
                "inclusion_filter": variation_params.inclusion_filter,
                "eligibility_rules": variation_params.eligibility_rules,
                "kill_conditions": variation_params.kill_conditions,
            }
        )
        _check_protocol_lock(protocol_body, effective_params)

        variation_rows = _resolve_variation_rows(base_rows, variation_params)
        variation_groups = _group_included_rows_by_analysis(variation_rows)

        for applies_to_analysis in sorted(base_outcome_by_analysis):
            base_outcome = base_outcome_by_analysis[applies_to_analysis]
            group_rows = variation_groups.get(applies_to_analysis, [])
            if group_rows:
                result = _classify_analysis(applies_to_analysis, group_rows, effective_params)
                results.append(
                    _sensitivity_result_to_dict(variation_entry_id, result, base_outcome)
                )
            else:
                results.append(
                    _empty_group_sensitivity_result(
                        variation_entry_id, applies_to_analysis, base_outcome
                    )
                )
    return results


def _build_assertion(result: _AnalysisResult, corpus_by_id: Mapping[str, CorpusEntry]) -> str:
    parts = [f"Analysis {result.applies_to_analysis!r}."]
    if result.supporting_entry_ids:
        findings = [
            corpus_by_id[entry_id].claim_relevant_finding
            for entry_id in result.supporting_entry_ids
        ]
        parts.append("Supporting: " + " ".join(findings))
    if result.contradicting_entry_ids:
        findings = [
            corpus_by_id[entry_id].claim_relevant_finding
            for entry_id in result.contradicting_entry_ids
        ]
        parts.append("Contradicting: " + " ".join(findings))
    return " ".join(parts)


def _run_pipeline(
    task_bundle: TaskBundle,
    inputs: Mapping[str, bytes],
    extraction_callable: ExtractionCallable | None,
) -> bytes:
    """The pure, in-process transform (derived_decisions (a)): parse the two
    (or three, counting the method-protocol body) declared input artifacts,
    verify the MethodProtocol lock precondition, apply the inclusion filter,
    assemble matrix rows, count independence, determine eligibility/ceiling
    and kill conditions, and serialize everything into one canonical JSON
    blob.
    """
    instructions = task_bundle.instructions
    corpus_artifact_id = str(instructions[_INSTRUCTIONS_CORPUS_KEY])
    protocol_parameters_artifact_id = str(instructions[_INSTRUCTIONS_PROTOCOL_PARAMETERS_KEY])
    method_protocol_artifact_id = str(instructions[_INSTRUCTIONS_METHOD_PROTOCOL_KEY])
    question_id = str(instructions[_INSTRUCTIONS_QUESTION_ID_KEY])

    params = ProtocolParameters.model_validate_json(inputs[protocol_parameters_artifact_id])
    protocol_body: dict[str, Any] = _load_json(inputs[method_protocol_artifact_id])
    _check_protocol_lock(protocol_body, params)
    variation_artifact_ids = _check_sensitivity_variation_coverage(protocol_body, instructions)

    raw_entries = _load_json(inputs[corpus_artifact_id])
    entries = [CorpusEntry.model_validate(raw_entry) for raw_entry in raw_entries]
    _require_unique_entry_ids(entries)
    corpus_by_id = {entry.entry_id: entry for entry in entries}

    extraction_fields = [str(field) for field in protocol_body.get("extraction_fields", [])]
    rows = _resolve_corpus_rows(entries, params, extraction_callable, extraction_fields)
    groups = _group_included_rows_by_analysis(rows)

    analyses = []
    for applies_to_analysis, group_rows in sorted(groups.items()):
        result = _classify_analysis(applies_to_analysis, group_rows, params)
        analyses.append(_analysis_result_to_dict(result, corpus_by_id, params))

    corpus_rows_out = [_corpus_row_to_dict(row) for row in rows]

    # MRR-MTH-018 (task-packets/K1-T03b.yaml): re-run ONLY the four
    # classification stages above, once per declared variation, against the
    # SAME already-parsed, already-extracted `rows` — see
    # `_run_sensitivity_variations`'s own docstring.
    base_outcome_by_analysis = {
        analysis["applies_to_analysis"]: analysis["outcome"] for analysis in analyses
    }
    sensitivity_analysis_results = _run_sensitivity_variations(
        protocol_body, rows, base_outcome_by_analysis, params, variation_artifact_ids, inputs
    )

    output: dict[str, Any] = {
        "protocol_id": params.protocol_id,
        "question_id": question_id,
        "corpus_rows": corpus_rows_out,
        "analyses": analyses,
        "sensitivity_analysis_results": sensitivity_analysis_results,
    }
    return canonicalize(output)


def _load_json(data: bytes) -> Any:
    import json

    return json.loads(data.decode("utf-8"))


def _require_unique_entry_ids(entries: Sequence[CorpusEntry]) -> None:
    seen: set[str] = set()
    for entry in entries:
        if entry.entry_id in seen:
            raise ValueError(f"duplicate corpus entry_id {entry.entry_id!r} in corpus snapshot")
        seen.add(entry.entry_id)


def _corpus_row_to_dict(row: _CorpusRowResult) -> dict[str, Any]:
    entry = row.entry
    return {
        "entry_id": entry.entry_id,
        "included": row.included,
        "exclusion_reason": row.exclusion_reason,
        "applies_to_analysis": entry.applies_to_analysis,
        "claim_type": entry.claim_type,
        "evidence_relation": entry.evidence_relation,
        "verification_status": entry.verification_status,
        "unverifiable_reason": entry.unverifiable_reason,
        "claim_relevant_finding": entry.claim_relevant_finding,
        "extraction": row.extraction,
        "source_family_id": entry.source_family_id,
        "source_record": {
            "identifiers": entry.identifiers.model_dump(exclude_none=True),
            "title": entry.title,
            "creators": entry.creators,
            "publication_date": entry.publication_date,
            "version": entry.version,
            "retrieval_timestamp": entry.retrieval_timestamp,
            "retrieval_method": entry.retrieval_method,
            "source_type": entry.source_type,
            "primary_secondary_derived": entry.primary_secondary_derived,
            "derivation_evidence": entry.derivation_evidence,
            "accessibility": entry.accessibility,
            "licensing": entry.licensing,
        },
    }


def _analysis_result_to_dict(
    result: _AnalysisResult, corpus_by_id: Mapping[str, CorpusEntry], params: ProtocolParameters
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "applies_to_analysis": result.applies_to_analysis,
        "included_source_count": result.included_source_count,
        "verified_source_count": result.verified_source_count,
        "distinct_independent_supporting_family_count": (
            result.distinct_independent_supporting_family_count
        ),
        "distinct_independent_contradicting_family_count": (
            result.distinct_independent_contradicting_family_count
        ),
        "outcome": result.outcome,
    }
    if result.outcome == "insufficient_evidence":
        base["claim_candidate"] = None
        base["decision"] = {
            "decision_type": "stop_insufficient_evidence",
            "rationale": result.decision_rationale,
        }
        return base

    base["decision"] = None
    non_applicability_conditions = list(params.non_applicability_conditions) + list(
        result.non_applicability_notes
    )
    base["claim_candidate"] = {
        "claim_type": result.claim_type,
        "status": result.outcome,
        "assertion": _build_assertion(result, corpus_by_id),
        "supporting_entry_ids": list(result.supporting_entry_ids),
        "contradicting_entry_ids": list(result.contradicting_entry_ids),
        "ruled_ceiling": RULED_CEILING,
        "deterministic_rule_reference": DETERMINISTIC_RULE_REFERENCE,
        "non_applicability_conditions": non_applicability_conditions,
    }
    return base


# ---------------------------------------------------------------------------
# The Executor implementation.
# ---------------------------------------------------------------------------


class SystematicEvidenceSynthesisExecutor:
    """The ``systematic_evidence_synthesis`` v1 ``Executor`` implementation.
    See the module docstring for the full design; mirrors
    ``mrr.services.node_runtime.executor.ReferenceTaskExecutor``'s own
    idempotency memo, wall-clock bound, injectable ``policy_gate``/
    ``is_cancelled``, and ``is_deterministic=True`` unconditionally.

    ``extraction_callable`` is the ONE thing this class adds beyond
    ``ReferenceTaskExecutor``'s own constructor shape (derived_decisions
    (m)): ``None`` by default — genuinely model-free — or an injected
    ``ExtractionCallable`` (typically ``build_model_assisted_extraction_callable``'s
    own return value) for the separately-tested model-assisted slice.
    """

    #: Mirrors ``ReferenceTaskExecutor.provides_untrusted_isolation`` — this
    #: executor is for the TRUSTED, deterministic corpus-synthesis
    #: computation only, never a sandbox for untrusted code.
    provides_untrusted_isolation: ClassVar[bool] = False

    def __init__(
        self,
        *,
        extraction_callable: ExtractionCallable | None = None,
        clock: Clock = time.monotonic,
        policy_gate: PolicyGate | None = None,
        is_cancelled: CancellationCheck | None = None,
    ) -> None:
        self._extraction_callable = extraction_callable
        self._clock = clock
        self._policy_gate = policy_gate
        self._is_cancelled = is_cancelled
        self._memo: dict[tuple[str, int, int], ExecutionResult] = {}

    def execute(
        self,
        task_bundle: TaskBundle,
        inputs: Mapping[str, bytes],
        *,
        execution_attempt: int,
    ) -> ExecutionResult:
        """See ``mrr.services.node_runtime.executor.Executor.execute`` for
        the general contract. Never raises for a task-level outcome — see
        the module docstring's "MTH-007 is enforced here first" section for
        how ``ProtocolNotLockedError``/``ProtocolLockViolationError`` (and
        any other pipeline exception) become an explicit ``failed``
        ``ExecutionResult`` instead of propagating.
        """
        if execution_attempt < 1:
            raise ValueError(f"execution_attempt must be >= 1, got {execution_attempt!r}")

        key = (task_bundle.id, task_bundle.revision, execution_attempt)
        memoized = self._memo.get(key)
        if memoized is not None:
            return memoized

        result = self._execute_uncached(task_bundle, inputs, execution_attempt=execution_attempt)
        self._memo[key] = result
        return result

    def _execute_uncached(
        self,
        task_bundle: TaskBundle,
        inputs: Mapping[str, bytes],
        *,
        execution_attempt: int,
    ) -> ExecutionResult:
        def _result(
            outcome: TerminalOutcome,
            *,
            output: bytes | None = None,
            output_hash: str | None = None,
            wall_time_seconds: float = 0.0,
            detail: str | None = None,
        ) -> ExecutionResult:
            return ExecutionResult(
                outcome=outcome,
                output=output,
                output_hash=output_hash,
                is_deterministic=True,
                execution_attempt=execution_attempt,
                task_id=task_bundle.id,
                task_revision=task_bundle.revision,
                resource_usage=ResourceUsage(wall_time_seconds=wall_time_seconds),
                detail=detail,
            )

        if self._policy_gate is not None and not self._policy_gate(task_bundle):
            return _result(
                "policy_denied",
                detail="local policy denied execution of this task bundle",
            )
        if self._is_cancelled is not None and self._is_cancelled():
            return _result(
                "cancelled",
                detail="execution attempt was cancelled before running",
            )

        declared_artifact_ids = {ref.artifact_id for ref in task_bundle.inputs}
        missing_artifact_ids = declared_artifact_ids - set(inputs.keys())
        if missing_artifact_ids:
            return _result(
                "failed",
                detail=(
                    "declared artifact_id(s) not resolved, cannot run the synthesis pipeline: "
                    f"{sorted(missing_artifact_ids)!r}"
                ),
            )

        timeout_seconds = task_bundle.resource_limits.timeout_seconds
        start = self._clock()
        pool: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=1)
        try:
            future: Future[bytes] = pool.submit(
                _run_pipeline, task_bundle, inputs, self._extraction_callable
            )
            try:
                output = future.result(timeout=timeout_seconds)
            except FutureTimeoutError:
                elapsed = self._clock() - start
                return _result(
                    "timed_out",
                    wall_time_seconds=elapsed,
                    detail=(
                        f"execution exceeded the {timeout_seconds}s wall-clock bound "
                        "(resource_limits.timeout_seconds)"
                    ),
                )
            except Exception as exc:
                elapsed = self._clock() - start
                return _result(
                    "failed",
                    wall_time_seconds=elapsed,
                    detail=f"{type(exc).__name__}: {exc}",
                )
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        elapsed = self._clock() - start
        output_hash = content_hash(output)
        return _result(
            "completed",
            output=output,
            output_hash=output_hash,
            wall_time_seconds=elapsed,
        )
