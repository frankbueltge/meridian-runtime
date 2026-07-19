"""The planner/proposer role (task-packets/E4-T03.yaml): given a research
question and an injected E4-T01 ``mrr.domain.model_adapter.ModelAdapter``,
propose a FOREST of ``mrr.contracts.hypothesis.Hypothesis`` branches — one
``generate_structured`` call (E4-T02), bounded-repaired, over a target model
built just for this role. Third task of Epic E4; the first AI ROLE.

--- A pure producer: no persistence, no selection, no verification ---------

docs/spec/01_SYSTEM_SPEC.md section 4.2 (Stage 2 — Hypothesis Forest): "The
planner generates multiple research branches rather than one linear plan."
This module does exactly that and NOTHING else:

- it PRODUCES an in-memory forest of schema-valid ``Hypothesis`` proposals
  plus a structured-generation audit trail;
- it persists NOTHING — no object repository, no event log, no unit of
  work (task-packets/E4-T03.yaml forbidden_changes: "the planner PRODUCES
  an in-memory hypothesis forest ... and writes NOTHING");
- it does not SELECT or PRIORITIZE branches (MRR-FR-013) or run a
  score-driven WAIVER policy (MRR-FR-011) — every branch the model
  proposes is preserved, unfiltered, in the returned forest; a role the
  model's response happens not to cover is recorded with a fixed, honest
  waiver reason (``_waivers_for_missing_roles`` below), never a scored
  judgment about whether that role SHOULD have been waived;
- it does not verify anything (E4-T04/T05, out of scope).

--- Reaching a model: solely through the injected ModelAdapter -------------

The ONLY way this module ever reaches a model is
``mrr.adapters.llm.structured_generation.generate_structured``, called with
the caller-injected ``ModelAdapter`` (E4-T01) and this module's own
``HypothesisForestProposal`` as the target model. This module imports only
the standard library, Pydantic, and the two E4 layers it builds on — no
provider SDK, no network client, anywhere in it (task-packets/E4-T03.yaml
acceptance: "the planner package imports no provider SDK and makes no
network call").

--- The LLM-facing target model has NO envelope, identity, or status field -

``_BranchProposal``/``HypothesisForestProposal`` (private to this module —
never one of the fourteen registered ``mrr.contracts`` entities, never
schema-registered under schemas/, never checked by
scripts/check_contracts.py) carry ONLY the fields a model can meaningfully
propose: statement, branch_role, predicted/disconfirming observations,
scope, dependencies, assumptions, methods, required capabilities, budget,
stop conditions, priority rationale, and insufficiency_rationale (the
insufficient_evidence escape) — never ``id``, ``content_hash``,
``practice_id``, ``revision``, ``created_at``, ``created_by``, or
``status``. This is the STRUCTURAL enforcement of MRR-FR-014 ("The planner
MUST NOT mark its own hypothesis as verified"): ``status`` is not even an
attribute the model CAN populate — ``_to_hypothesis`` below always sets it
to ``"proposed"`` — and ``extra="forbid"`` (via ``mrr.contracts.common.
MRRModel``, which both models inherit) rejects any attempt by the model to
inject a ``status``/``verified``/``result`` field into its JSON response
outright, before the response is ever wrapped into a full ``Hypothesis``.

``_BranchProposal`` reuses ``mrr.contracts.hypothesis.check_falsifiability``
verbatim — the EXACT SAME falsifiability conditional ``Hypothesis`` itself
enforces — via its own ``model_validator``, so a branch that would fail the
final contract's own validator already fails Pydantic validation inside
``generate_structured``'s bounded-repair loop (consuming a repair attempt,
or, once the budget is exhausted, surfacing as ``status ==
"schema_invalid"``) rather than silently reaching ``_to_hypothesis``.

--- Whole-forest atomicity: one call, one schema-valid document ------------

``generate_structured`` validates ONE JSON document per call.
``HypothesisForestProposal`` wraps every branch into that single document
(``{"branches": [...]}``), so ONE invalid branch fails the WHOLE attempt
(consuming a repair attempt, or ultimately yielding ``status ==
"schema_invalid"`` for the entire forest) rather than a per-branch partial
acceptance — task-packets/E4-T03.yaml's own "no-invalid-passes ... inherited
from E4-T02" acceptance, taken at its word: E4-T02 has no notion of a
partially valid response, so this layer introduces none either. Flagged as
an open specification question in this task's PR body: a future
per-branch-repair policy, if ever wanted, is a separate design decision.

--- Role coverage: an honest waiver, never a scored policy decision --------

MRR-FR-010/011: the planner "MUST support at least" the six branch roles;
"a score MAY waive a branch role only with a recorded reason." This module
NEVER decides that a role SHOULD be waived (that scoring/policy engine is
explicitly out of scope). It only records the plain, honest FACT that the
model's own response did not include a branch for a given role, as a
``RoleWaiver`` with a fixed, factual ``reason`` string — never a judgment
about whether omitting that role was correct.

--- Determinism: content-deterministic, given caller-injected identity -----

``Hypothesis.id``/``created_at``/``content_hash`` are minted per branch
(every first-class MRR object requires them — ``mrr.contracts.common.
BaseObject``), but minting a fresh random ULID
(``mrr.domain.identity.new_urn``) or reading the current wall-clock time on
every call would make two calls with an IDENTICAL scripted fake adapter and
identical inputs produce two non-identical forests — only by virtue of
these freshly-minted identity fields, never by virtue of anything the model
said — failing task-packets/E4-T03.yaml's own determinism acceptance test
literally ("the same scripted fake adapter and inputs yield an identical
forest"). ``propose_hypothesis_forest`` therefore accepts optional
``id_factory``/``clock`` callables (defaulting to ``mrr.domain.identity.
new_urn``/``datetime.now(UTC)`` in production) so a test can inject a
deterministic sequence/fixed instant and get a byte-for-byte identical
forest across two calls; production callers use the defaults and get fresh
identity as normal — exactly the same dependency-injection shape this
codebase already uses for its write callables (e.g.
``mrr.services.claim.service.RecordRevisionWithEvent``), applied here to
identity minting instead of persistence.

--- Minting the envelope: mirrors RunManifestRecorder's own precedent ------

``_to_hypothesis`` builds a draft ``Hypothesis`` with a placeholder
``content_hash``, dumps it to JSON, computes the real
``mrr.domain.hashing_policy.compute_content_hash``, and re-validates —
exactly the pattern ``mrr.services.node_runtime.run_manifest.
RunManifestRecorder.record`` already uses for its own from-scratch object
assembly (as opposed to ``ClaimService.create()``'s "caller already minted
id/content_hash" precedent, which does not apply here: there is no external
caller who already built a full ``Hypothesis`` — this module assembles one
from LLM content for the first time).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Self

from mrr.adapters.llm.structured_generation import (
    StructuredGenerationResult,
    StructuredGenerationStatus,
    generate_structured,
)
from mrr.contracts.common import ApiVersion, Budget, MRRModel, Scope, Urn
from mrr.contracts.hypothesis import BRANCH_ROLES, BranchRole, Hypothesis, check_falsifiability
from mrr.domain.hashing_policy import compute_content_hash
from mrr.domain.identity import new_urn
from mrr.domain.model_adapter import (
    ModelAdapter,
    ModelInvocationOutcome,
    ModelInvocationRequest,
    OperationKind,
    RedactionPolicy,
)
from pydantic import Field, StringConstraints, model_validator

#: Every Hypothesis this module mints uses this fixed, literal api_version —
#: matches every other from-scratch object-assembly precedent in this
#: codebase (e.g. ``mrr.services.node_runtime.run_manifest``'s own draft).
_API_VERSION: ApiVersion = "mrr/v1alpha1"

#: A placeholder content_hash, structurally valid (matches
#: ``mrr.contracts.common.Sha256``'s pattern) but never the real one —
#: overwritten by ``_to_hypothesis`` before the final object is returned.
#: Mirrors ``mrr.services.node_runtime.run_manifest.RunManifestRecorder``'s
#: own "placeholder; recomputed below" precedent.
_PLACEHOLDER_CONTENT_HASH = "sha256:" + "0" * 64

#: The fixed, honest waiver reason recorded for every branch_role the
#: model's own structured response did not cover — a plain fact, never a
#: scored policy decision (see the module docstring's "Role coverage"
#: section).
_MISSING_ROLE_REASON = "the model's structured response contained no branch for role {role!r}"


class _BranchProposal(MRRModel):
    """One branch's LLM-authorable content — every field a model may
    propose for one ``Hypothesis``, EXCEPT the BaseObject envelope
    (``id``/``content_hash``/``practice_id``/``revision``/``created_at``/
    ``created_by``) and ``status`` (see the module docstring's "The
    LLM-facing target model has NO envelope, identity, or status field"
    section). List/object fields default to empty so a model proposing an
    ``insufficient_evidence`` branch need not restate them; ``statement``,
    ``branch_role``, and ``priority_rationale`` carry no default — every
    branch states them regardless of role.
    """

    statement: str = Field(min_length=1)
    branch_role: BranchRole
    predicted_observations: list[str] = Field(default_factory=list)
    disconfirming_observations: list[str] = Field(default_factory=list)
    insufficiency_rationale: Annotated[str, StringConstraints(min_length=1)] | None = None
    scope: Scope = Field(default_factory=Scope)
    dependencies: list[Urn] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    budget: Budget = Field(default_factory=Budget)
    stop_conditions: list[str] = Field(default_factory=list)
    priority_rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def _falsifiability(self) -> Self:
        """The EXACT SAME conditional ``mrr.contracts.hypothesis.Hypothesis``
        itself enforces (see ``check_falsifiability``) — checked here too,
        so an invalid branch fails INSIDE ``generate_structured``'s own
        bounded-repair loop rather than only at ``_to_hypothesis`` time.
        """
        check_falsifiability(
            branch_role=self.branch_role,
            predicted_observations=self.predicted_observations,
            disconfirming_observations=self.disconfirming_observations,
            insufficiency_rationale=self.insufficiency_rationale,
        )
        return self


class HypothesisForestProposal(MRRModel):
    """The structured-generation target model for one planner call: a
    non-empty forest of :class:`_BranchProposal` entries. Exported (not
    underscore-private) so a caller inspecting or documenting the exact
    schema this module asks the model to satisfy can reference it directly;
    :func:`propose_hypothesis_forest` remains the intended entry point.
    """

    branches: list[_BranchProposal] = Field(min_length=1)


@dataclass(frozen=True, slots=True, kw_only=True)
class RoleWaiver:
    """A recorded, honest fact: the model's own structured response
    contained no branch for ``branch_role`` (MRR-FR-011's "recorded
    reason" — never a scored decision that the role SHOULD be waived; see
    the module docstring's "Role coverage" section).
    """

    branch_role: BranchRole
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class HypothesisForestResult:
    """The result of one :func:`propose_hypothesis_forest` call.

    ``status`` mirrors ``mrr.adapters.llm.structured_generation.
    StructuredGenerationStatus`` verbatim — never relabeled; this module
    introduces no failure kind of its own. ``hypotheses``/``role_waivers``
    are present if and only if ``status == "proposal"`` — structurally
    enforced in ``__post_init__``, mirroring ``StructuredGenerationResult``'s
    own proposal-iff-status biconditional (E4-T02). ``attempts`` is always
    the FULL, ordered ``generate_structured`` audit trail regardless of
    outcome (task-packets/E4-T03.yaml invariant: "every underlying model
    call is recorded in the returned audit trail").
    """

    status: StructuredGenerationStatus
    hypotheses: tuple[Hypothesis, ...]
    role_waivers: tuple[RoleWaiver, ...]
    attempts: tuple[ModelInvocationOutcome, ...]
    validation_errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status == "proposal" and not self.hypotheses:
            raise ValueError("status 'proposal' must carry at least one hypothesis")
        if self.status != "proposal" and (self.hypotheses or self.role_waivers):
            raise ValueError(
                f"status {self.status!r} must not carry hypotheses or role_waivers "
                "(only 'proposal' status expresses a schema-valid forest)"
            )
        if not self.attempts:
            raise ValueError("attempts must record at least the initial call")


def _default_id_factory() -> str:
    """Production default for ``propose_hypothesis_forest``'s
    ``id_factory``: a fresh, random ``urn:mrr:hypothesis:<ulid>`` per
    branch. See the module docstring's "Determinism" section for why a test
    injects its own deterministic factory instead.
    """
    return new_urn("hypothesis")


def _default_clock() -> datetime:
    """Production default for ``propose_hypothesis_forest``'s ``clock``:
    the current, real, timezone-aware instant. See the module docstring's
    "Determinism" section for why a test injects a fixed instant instead.
    """
    return datetime.now(UTC)


def _build_prompt(research_question: str) -> str:
    """The initial call's prompt text: states the task, the six branch
    roles, and the falsifiability requirement in plain language. The
    required JSON schema itself is appended by ``generate_structured``'s own
    repair-prompt builder only on a REPAIR attempt (task-packets/E4-T02.yaml
    derived_decisions) — this initial prompt states the schema once too, so
    the first attempt is not left guessing at the required shape.
    """
    roles = ", ".join(BRANCH_ROLES)
    schema_json = HypothesisForestProposal.model_json_schema()
    return (
        "Propose a forest of research branch hypotheses for the research question "
        "below. Propose one branch per applicable branch_role, drawn from exactly "
        f"these six roles: {roles}.\n\n"
        f"Research question:\n{research_question}\n\n"
        "Every branch is a PROPOSAL ONLY, never a claim of result: do not assert "
        "that any branch is verified, confirmed, supported, or authoritative.\n\n"
        "For every branch_role except insufficient_evidence, state at least one "
        "predicted_observations entry and at least one disconfirming_observations "
        "entry (what would be observed if the hypothesis holds, and what would be "
        "observed if it does not — a falsifiable proposition needs both). If "
        "evidence is currently insufficient to state a falsifiable proposition for "
        "a role, propose branch_role 'insufficient_evidence' instead, leave the "
        "observation lists empty, and record why in insufficiency_rationale.\n\n"
        "Return ONLY a JSON object that satisfies the schema below — no "
        "explanation, no markdown formatting, no additional text.\n\n"
        f"Required JSON schema:\n{schema_json}"
    )


def _to_hypothesis(
    branch: _BranchProposal,
    *,
    id_: str,
    practice_id: Urn,
    created_by: Urn,
    created_at: datetime,
) -> Hypothesis:
    """Wrap one model-authored :class:`_BranchProposal` into a full,
    schema-valid ``Hypothesis`` — minting the BaseObject envelope and
    setting ``status="proposed"`` (never anything the model itself
    supplied; see the module docstring). Mirrors ``mrr.services.
    node_runtime.run_manifest.RunManifestRecorder.record``'s own
    "placeholder content_hash, dump, recompute, re-validate" sequence.
    """
    draft = Hypothesis(
        id=id_,
        api_version=_API_VERSION,
        kind="Hypothesis",
        practice_id=practice_id,
        revision=1,
        created_at=created_at,
        created_by=created_by,
        content_hash=_PLACEHOLDER_CONTENT_HASH,
        statement=branch.statement,
        branch_role=branch.branch_role,
        predicted_observations=list(branch.predicted_observations),
        disconfirming_observations=list(branch.disconfirming_observations),
        insufficiency_rationale=branch.insufficiency_rationale,
        scope=branch.scope,
        dependencies=list(branch.dependencies),
        assumptions=list(branch.assumptions),
        methods=list(branch.methods),
        required_capabilities=list(branch.required_capabilities),
        budget=branch.budget,
        stop_conditions=list(branch.stop_conditions),
        priority_rationale=branch.priority_rationale,
        status="proposed",
    )
    body = draft.model_dump(mode="json", exclude_none=True)
    body["content_hash"] = compute_content_hash(body)
    return Hypothesis.model_validate(body)


def _waivers_for_missing_roles(hypotheses: tuple[Hypothesis, ...]) -> tuple[RoleWaiver, ...]:
    """Every ``BRANCH_ROLES`` entry with no produced branch gets an honest,
    fixed waiver reason — see the module docstring's "Role coverage"
    section. Order follows ``BRANCH_ROLES`` (MRR-FR-010's own order), not
    the order branches happened to arrive in.
    """
    produced_roles = {hypothesis.branch_role for hypothesis in hypotheses}
    return tuple(
        RoleWaiver(branch_role=role, reason=_MISSING_ROLE_REASON.format(role=role))
        for role in BRANCH_ROLES
        if role not in produced_roles
    )


def propose_hypothesis_forest(
    adapter: ModelAdapter,
    *,
    research_question: str,
    model_profile_id: str,
    model_profile_hash: str,
    operation_kind: OperationKind,
    redaction_policy: RedactionPolicy,
    max_repair_attempts: int,
    practice_id: Urn,
    created_by: Urn,
    tool_names_available: tuple[str, ...] = (),
    id_factory: Callable[[], str] = _default_id_factory,
    clock: Callable[[], datetime] = _default_clock,
) -> HypothesisForestResult:
    """Propose a hypothesis forest for ``research_question`` via ``adapter``.

    Builds one ``ModelInvocationRequest`` (see :func:`_build_prompt`) and
    calls ``mrr.adapters.llm.structured_generation.generate_structured``
    with :class:`HypothesisForestProposal` as the target model and
    ``max_repair_attempts`` as the bounded-repair budget — the SOLE way this
    function ever reaches ``adapter``.

    When the underlying call yields a schema-valid forest
    (``status == "proposal"``), every proposed branch is wrapped into a full
    ``Hypothesis`` (via :func:`_to_hypothesis`, minting a fresh identity per
    branch from ``id_factory``/``clock``/``practice_id``/``created_by``) and
    EVERY ONE is preserved in the returned ``hypotheses`` tuple — no
    selection, no filtering, no dropping. Any of the six ``BRANCH_ROLES`` the
    model's response did not cover is recorded as an explicit ``RoleWaiver``
    (:func:`_waivers_for_missing_roles`).

    When the underlying call does NOT yield a schema-valid forest (any
    ``StructuredGenerationStatus`` other than ``"proposal"`` —
    ``"schema_invalid"``, ``"refused"``, ``"content_filtered"``, ``"error"``,
    or ``"timed_out"``), that status is surfaced verbatim, with an empty
    ``hypotheses``/``role_waivers`` and the failure's own
    ``validation_errors`` — no invalid branch is ever emitted as a proposal
    (no-invalid-passes, inherited from E4-T02).

    ``attempts`` always carries the FULL ``generate_structured`` audit trail
    (every underlying model call made, in order), regardless of outcome.

    Args:
        adapter: the injected, provider-neutral ``ModelAdapter`` (E4-T01) —
            the sole channel through which this function ever reaches a
            model.
        research_question: the question this forest proposes branches for.
        model_profile_id: the ``ModelProfile`` this call is pinned to (see
            ``mrr.domain.model_adapter.ModelInvocationRequest``).
        model_profile_hash: that profile's own content hash, pinned at call
            time.
        operation_kind: this call's own deterministic/stochastic kind
            (MRR-FR-044) — no implicit default; the caller states it.
        redaction_policy: whether raw prompt/response text may be retained
            (MRR-FR-045) — no implicit default; the caller states it
            explicitly (``mrr.domain.model_adapter.DEFAULT_REDACTION_POLICY``
            for the safe choice).
        max_repair_attempts: the bounded-repair budget passed straight
            through to ``generate_structured``.
        practice_id: the practice minting every ``Hypothesis`` in this
            forest.
        created_by: the identity (e.g. this planner's own agent URN)
            recorded as ``created_by`` on every ``Hypothesis`` in this
            forest — never the model's own identity, since the model
            authored only the LLM-facing content, not the envelope.
        tool_names_available: forwarded to the underlying
            ``ModelInvocationRequest`` unchanged; empty by default (this
            role asks for structured JSON only, no tool use).
        id_factory: mints one URN per produced branch; defaults to a fresh
            ``mrr.domain.identity.new_urn("hypothesis")`` per call. Inject a
            deterministic sequence for a reproducible forest (see the module
            docstring's "Determinism" section).
        clock: returns the instant recorded as ``created_at`` on every
            branch produced by this call; defaults to the real current
            instant. Inject a fixed instant for a reproducible forest.

    Returns:
        A :class:`HypothesisForestResult` carrying either a forest of
        schema-valid ``Hypothesis`` proposals plus role waivers, or an
        explicit, non-"proposal" failure — plus the full audit trail either
        way.
    """
    request = ModelInvocationRequest(
        model_profile_id=model_profile_id,
        model_profile_hash=model_profile_hash,
        prompt_text=_build_prompt(research_question),
        operation_kind=operation_kind,
        redaction_policy=redaction_policy,
        tool_names_available=tool_names_available,
    )

    result: StructuredGenerationResult[HypothesisForestProposal] = generate_structured(
        adapter,
        request,
        HypothesisForestProposal,
        max_repair_attempts=max_repair_attempts,
    )

    if result.status != "proposal":
        return HypothesisForestResult(
            status=result.status,
            hypotheses=(),
            role_waivers=(),
            attempts=result.attempts,
            validation_errors=result.validation_errors,
        )

    if result.proposal is None:  # pragma: no cover — guarded by generate_structured's own invariant
        raise ValueError(
            "generate_structured returned status 'proposal' without a proposal — this "
            "violates its own structural invariant"
        )

    now = clock()
    hypotheses = tuple(
        _to_hypothesis(
            branch,
            id_=id_factory(),
            practice_id=practice_id,
            created_by=created_by,
            created_at=now,
        )
        for branch in result.proposal.branches
    )

    return HypothesisForestResult(
        status="proposal",
        hypotheses=hypotheses,
        role_waivers=_waivers_for_missing_roles(hypotheses),
        attempts=result.attempts,
        validation_errors=result.validation_errors,
    )


__all__ = [
    "HypothesisForestProposal",
    "HypothesisForestResult",
    "RoleWaiver",
    "propose_hypothesis_forest",
]
