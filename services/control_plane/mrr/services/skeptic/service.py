"""The skeptic role (task-packets/E4-T04.yaml): given a target claim (id,
content hash, assertion, and scope — ALL caller-supplied) and an injected
E4-T01 ``mrr.domain.model_adapter.ModelAdapter``, search for skeptical
challenges against it across the four MRR-FR-074 kinds — one
``generate_structured`` call (E4-T02), bounded-repaired, over a target model
built just for this role. Fourth task of Epic E4; the second AI ROLE.

--- A pure producer: no persistence, no independence computation, no verdict --

docs/spec/01_SYSTEM_SPEC.md section 4.8 ("Stage 8 — Skepticism and
independent verification"), MRR-FR-074: "The skeptic MUST search for
counterevidence, alternative explanations, scope leakage, and hidden
assumptions." This module does exactly that and NOTHING else:

- it PRODUCES an in-memory tuple of schema-valid ``mrr.contracts.
  skeptical_challenge.SkepticalChallenge`` proposals plus a
  structured-generation audit trail;
- it persists NOTHING — no object repository, no event log, no unit of
  work, and no attachment to a claim's ``counterevidence_relations``
  (task-packets/E4-T04.yaml forbidden_changes: that wiring is a LATER task);
- it does not compute independence (``mrr.domain.independence``, E3-T05) or
  run a self-verification gate — it only RECORDS which ``mrr.contracts.
  model_profile.ModelProfile`` produced each challenge (id + hash), the raw
  material a later independence calculation would consume;
- it does not fetch or persist the target claim — the caller supplies its
  id, content hash, assertion, and scope directly (task-packets/E4-T04.yaml:
  "the skeptic takes the target claim's id/hash/statement/scope as input
  (caller-supplied); it does NOT fetch or persist it"); this module does not
  import ``mrr.contracts.claim`` at all;
- it issues no verification decision and changes no claim's status
  (MRR-FR-070/075, E4-T05, out of this task's scope).

--- Reaching a model: solely through the injected ModelAdapter -------------

The ONLY way this module ever reaches a model is
``mrr.adapters.llm.structured_generation.generate_structured``, called with
the caller-injected ``ModelAdapter`` (E4-T01) and this module's own
``SkepticalReviewProposal`` as the target model. This module imports only
the standard library, Pydantic, and the two E4 layers it builds on — no
provider SDK, no network client, anywhere in it (task-packets/E4-T04.yaml
acceptance: "the skeptic package imports no provider SDK and makes no
network call").

--- The LLM-facing target model has NO envelope, target, profile, or verdict field ---

``_ChallengeProposal``/``SkepticalReviewProposal`` (private to this module —
never one of the fifteen registered ``mrr.contracts`` entities, never
schema-registered under schemas/, never checked by
scripts/check_contracts.py) carry ONLY the fields a model can meaningfully
propose for one challenge: ``challenge_type``, ``statement``, ``rationale``,
and ``supporting_source_ids`` — never ``id``, ``content_hash``,
``practice_id``, ``revision``, ``created_at``, ``created_by``,
``target_claim_id``/``target_claim_hash`` (this role sets those from the
CALLER's own inputs, never the model's output), ``producing_model_profile_id``/
``producing_model_profile_hash`` (this role sets those from the CALLER's own
``model_profile_id``/``model_profile_hash``, never the model's output), or
any verdict/decision/verified/resolved/claim_status field of any kind. This
is the STRUCTURAL enforcement of MRR-FR-070 ("The proposer and executor MUST
NOT issue the final verification decision for their own claim"): there is no
field anywhere the model could populate to smuggle a verdict or forge a
target/profile reference, and ``extra="forbid"`` (via ``mrr.contracts.common.
MRRModel``, which both models inherit) rejects any attempt by the model to
inject one into its JSON response outright, before the response is ever
wrapped into a full ``SkepticalChallenge``.

--- Whole-review atomicity: one call, one schema-valid document ------------

``generate_structured`` validates ONE JSON document per call.
``SkepticalReviewProposal`` wraps every challenge into that single document
(``{"challenges": [...]}``), so ONE invalid challenge fails the WHOLE attempt
(consuming a repair attempt, or ultimately yielding ``status ==
"schema_invalid"`` for the entire review) rather than a per-challenge
partial acceptance — the same "no-invalid-passes … inherited from E4-T02"
precedent the planner's own forest already established
(``mrr.services.planner.service``). Unlike the planner's forest (which must
propose at least one branch), ``SkepticalReviewProposal.challenges`` carries
NO minimum length: a skeptic that genuinely finds nothing across all four
types is a valid, recorded outcome (see "Coverage" below) — an empty
``{"challenges": []}`` document is itself schema-valid and yields a
``"proposal"`` result with an empty ``challenges`` tuple and all four types
waived.

--- Coverage: an honest "searched, none found" marker, never invented ------

MRR-FR-074's "MUST search for" all four kinds is a COVERAGE duty, not a find
duty (task-packets/E4-T04.yaml derived_decisions) — mirroring the planner's
own role-coverage/waiver precedent
(``mrr.services.planner.service.RoleWaiver``/``_waivers_for_missing_roles``)
exactly, with ``mrr.contracts.skeptical_challenge.ChallengeType`` standing in
for ``mrr.contracts.hypothesis.BranchRole``. This module NEVER decides that a
challenge type genuinely has nothing to find — that epistemic judgment is
the model's, made inside its own structured response. It only records the
plain, honest FACT that the model's response contained no challenge of a
given type, as a ``ChallengeTypeWaiver`` with a fixed, factual reason string
— never a judgment about whether that absence is correct. Every one of the
four ``CHALLENGE_TYPES`` is covered in every ``"proposal"`` result: either by
at least one produced ``SkepticalChallenge`` of that type, or by exactly one
``ChallengeTypeWaiver`` for that type — never both, never neither, and never
silently omitted (``_waivers_for_missing_types`` below).

--- Determinism: content-deterministic, given caller-injected identity -----

Mirrors ``mrr.services.planner.service.propose_hypothesis_forest``'s own
"Determinism" precedent exactly: ``SkepticalChallenge.id``/``created_at``/
``content_hash`` are minted per challenge, but minting a fresh random ULID
or reading the real wall clock on every call would make two calls with an
IDENTICAL scripted fake adapter and identical inputs produce non-identical
results — only by virtue of freshly-minted identity, never by virtue of
anything the model said. ``propose_skeptical_challenges`` therefore accepts
optional ``id_factory``/``clock`` callables (defaulting to ``mrr.domain.
identity.new_urn``/``datetime.now(UTC)`` in production) so a test can inject
a deterministic sequence/fixed instant and get a byte-for-byte identical
result across two calls; production callers use the defaults and get fresh
identity as normal.

--- Minting the envelope: mirrors _to_hypothesis's own precedent -----------

``_to_challenge`` builds a draft ``SkepticalChallenge`` with a placeholder
``content_hash``, dumps it to JSON, computes the real ``mrr.domain.
hashing_policy.compute_content_hash``, and re-validates — exactly ``mrr.
services.planner.service._to_hypothesis``'s own "placeholder content_hash,
dump, recompute, re-validate" sequence, itself mirroring ``mrr.services.
node_runtime.run_manifest.RunManifestRecorder.record``'s original precedent.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from mrr.adapters.llm.structured_generation import (
    StructuredGenerationResult,
    StructuredGenerationStatus,
    generate_structured,
)
from mrr.contracts.common import ApiVersion, MRRModel, Scope, Sha256, Urn
from mrr.contracts.skeptical_challenge import CHALLENGE_TYPES, ChallengeType, SkepticalChallenge
from mrr.domain.hashing_policy import compute_content_hash
from mrr.domain.identity import new_urn
from mrr.domain.model_adapter import (
    ModelAdapter,
    ModelInvocationOutcome,
    ModelInvocationRequest,
    OperationKind,
    RedactionPolicy,
)
from pydantic import Field

#: Every SkepticalChallenge this module mints uses this fixed, literal
#: api_version — matches every other from-scratch object-assembly precedent
#: in this codebase (e.g. ``mrr.services.planner.service``'s own draft).
_API_VERSION: ApiVersion = "mrr/v1alpha1"

#: A placeholder content_hash, structurally valid (matches
#: ``mrr.contracts.common.Sha256``'s pattern) but never the real one —
#: overwritten by ``_to_challenge`` before the final object is returned.
#: Mirrors ``mrr.services.planner.service._PLACEHOLDER_CONTENT_HASH``'s own
#: precedent.
_PLACEHOLDER_CONTENT_HASH = "sha256:" + "0" * 64

#: The fixed, honest waiver reason recorded for every challenge_type the
#: model's own structured response did not cover — a plain fact, never a
#: judgment that the type SHOULD have been waived (see the module
#: docstring's "Coverage" section).
_MISSING_TYPE_REASON = (
    "searched, none found: the model's structured response contained no "
    "challenge of type {challenge_type!r}"
)


class _ChallengeProposal(MRRModel):
    """One challenge's LLM-authorable content — every field a model may
    propose for one ``SkepticalChallenge``, EXCEPT the BaseObject envelope,
    the target-claim reference, and the producing-ModelProfile reference
    (see the module docstring's "The LLM-facing target model" section).
    ``supporting_source_ids`` defaults to empty so a model proposing a
    challenge that cites no source need not restate it.
    """

    challenge_type: ChallengeType
    statement: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    supporting_source_ids: list[Urn] = Field(default_factory=list)


class SkepticalReviewProposal(MRRModel):
    """The structured-generation target model for one skeptic call: zero or
    more :class:`_ChallengeProposal` entries. See the module docstring's
    "Whole-review atomicity" section for why this carries no minimum
    length. Exported (not underscore-private) so a caller inspecting or
    documenting the exact schema this module asks the model to satisfy can
    reference it directly; :func:`propose_skeptical_challenges` remains the
    intended entry point.
    """

    challenges: list[_ChallengeProposal] = Field(default_factory=list)


@dataclass(frozen=True, slots=True, kw_only=True)
class ChallengeTypeWaiver:
    """A recorded, honest fact: the model's own structured response
    contained no challenge of ``challenge_type`` (MRR-FR-074's own "searched,
    none found" coverage duty — never a judgment that the type SHOULD have
    been waived; see the module docstring's "Coverage" section).
    """

    challenge_type: ChallengeType
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class SkepticalChallengeResult:
    """The result of one :func:`propose_skeptical_challenges` call.

    ``status`` mirrors ``mrr.adapters.llm.structured_generation.
    StructuredGenerationStatus`` verbatim — never relabeled; this module
    introduces no failure kind of its own. ``challenges``/``type_waivers``
    are empty whenever ``status != "proposal"`` — structurally enforced in
    ``__post_init__``, mirroring ``StructuredGenerationResult``'s own
    proposal-iff-status biconditional (E4-T02). Unlike ``mrr.services.
    planner.service.HypothesisForestResult`` (whose forest must carry at
    least one hypothesis), a ``"proposal"`` result here MAY carry an empty
    ``challenges`` tuple: a skeptic that genuinely finds nothing across all
    four types is itself a valid, recorded outcome (every ``ChallengeType``
    then appears in ``type_waivers`` instead). ``attempts`` is always the
    FULL, ordered ``generate_structured`` audit trail regardless of outcome
    (task-packets/E4-T04.yaml invariant: "every underlying model call is
    recorded in the returned audit trail").
    """

    status: StructuredGenerationStatus
    challenges: tuple[SkepticalChallenge, ...]
    type_waivers: tuple[ChallengeTypeWaiver, ...]
    attempts: tuple[ModelInvocationOutcome, ...]
    validation_errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status != "proposal" and (self.challenges or self.type_waivers):
            raise ValueError(
                f"status {self.status!r} must not carry challenges or type_waivers "
                "(only 'proposal' status expresses a schema-valid review)"
            )
        if not self.attempts:
            raise ValueError("attempts must record at least the initial call")


def _default_id_factory() -> str:
    """Production default for ``propose_skeptical_challenges``'s
    ``id_factory``: a fresh, random ``urn:mrr:skeptical-challenge:<ulid>``
    per challenge. See the module docstring's "Determinism" section for why
    a test injects its own deterministic factory instead.
    """
    return new_urn("skeptical-challenge")


def _default_clock() -> datetime:
    """Production default for ``propose_skeptical_challenges``'s ``clock``:
    the current, real, timezone-aware instant. See the module docstring's
    "Determinism" section for why a test injects a fixed instant instead.
    """
    return datetime.now(UTC)


def _build_prompt(*, target_claim_assertion: str, target_claim_scope: Scope) -> str:
    """The initial call's prompt text: states the task, the four challenge
    types, and the coverage/no-fabrication requirement in plain language.
    The required JSON schema itself is appended by ``generate_structured``'s
    own repair-prompt builder only on a REPAIR attempt (task-packets/
    E4-T02.yaml derived_decisions) — this initial prompt states the schema
    once too, so the first attempt is not left guessing at the required
    shape.
    """
    types = ", ".join(CHALLENGE_TYPES)
    schema_json = SkepticalReviewProposal.model_json_schema()
    scope_json = target_claim_scope.model_dump_json(exclude_none=True)
    return (
        "Search for skeptical challenges against the claim below. Search for "
        f"EVERY one of these four challenge types: {types}. Propose a "
        "challenge only for a type where you actually find one after "
        "searching; if you find nothing for a type, simply propose none of "
        "that type — do not fabricate a challenge to fill it.\n\n"
        f"Claim under review:\n{target_claim_assertion}\n\n"
        f"Claim scope:\n{scope_json}\n\n"
        "Each challenge is a PROPOSAL ONLY, never a verification verdict: "
        "state the challenge and its rationale, but do not assert that the "
        "claim is false, contradicted, withdrawn, or otherwise resolved.\n\n"
        "Return ONLY a JSON object that satisfies the schema below — no "
        "explanation, no markdown formatting, no additional text.\n\n"
        f"Required JSON schema:\n{schema_json}"
    )


def _to_challenge(
    challenge: _ChallengeProposal,
    *,
    id_: str,
    practice_id: Urn,
    created_by: Urn,
    created_at: datetime,
    target_claim_id: Urn,
    target_claim_hash: Sha256,
    producing_model_profile_id: Urn,
    producing_model_profile_hash: Sha256,
) -> SkepticalChallenge:
    """Wrap one model-authored :class:`_ChallengeProposal` into a full,
    schema-valid ``SkepticalChallenge`` — minting the BaseObject envelope and
    setting the target-claim and producing-ModelProfile references from the
    CALLER's own inputs (never anything the model itself supplied; see the
    module docstring). Mirrors ``mrr.services.planner.service._to_hypothesis``'s
    own "placeholder content_hash, dump, recompute, re-validate" sequence.
    """
    draft = SkepticalChallenge(
        id=id_,
        api_version=_API_VERSION,
        kind="SkepticalChallenge",
        practice_id=practice_id,
        revision=1,
        created_at=created_at,
        created_by=created_by,
        content_hash=_PLACEHOLDER_CONTENT_HASH,
        challenge_type=challenge.challenge_type,
        target_claim_id=target_claim_id,
        target_claim_hash=target_claim_hash,
        statement=challenge.statement,
        rationale=challenge.rationale,
        supporting_source_ids=list(challenge.supporting_source_ids),
        producing_model_profile_id=producing_model_profile_id,
        producing_model_profile_hash=producing_model_profile_hash,
    )
    body = draft.model_dump(mode="json", exclude_none=True)
    body["content_hash"] = compute_content_hash(body)
    return SkepticalChallenge.model_validate(body)


def _waivers_for_missing_types(
    challenges: tuple[SkepticalChallenge, ...],
) -> tuple[ChallengeTypeWaiver, ...]:
    """Every ``CHALLENGE_TYPES`` entry with no produced challenge gets an
    honest, fixed waiver reason — see the module docstring's "Coverage"
    section. Order follows ``CHALLENGE_TYPES`` (MRR-FR-074's own order), not
    the order challenges happened to arrive in.
    """
    produced_types = {challenge.challenge_type for challenge in challenges}
    return tuple(
        ChallengeTypeWaiver(
            challenge_type=challenge_type,
            reason=_MISSING_TYPE_REASON.format(challenge_type=challenge_type),
        )
        for challenge_type in CHALLENGE_TYPES
        if challenge_type not in produced_types
    )


def propose_skeptical_challenges(
    adapter: ModelAdapter,
    *,
    target_claim_id: Urn,
    target_claim_hash: Sha256,
    target_claim_assertion: str,
    target_claim_scope: Scope,
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
) -> SkepticalChallengeResult:
    """Search for skeptical challenges against a target claim via ``adapter``.

    Builds one ``ModelInvocationRequest`` (see :func:`_build_prompt`) and
    calls ``mrr.adapters.llm.structured_generation.generate_structured`` with
    :class:`SkepticalReviewProposal` as the target model and
    ``max_repair_attempts`` as the bounded-repair budget — the SOLE way this
    function ever reaches ``adapter``.

    When the underlying call yields a schema-valid review
    (``status == "proposal"``), every proposed challenge is wrapped into a
    full ``SkepticalChallenge`` (via :func:`_to_challenge`, minting a fresh
    identity per challenge from ``id_factory``/``clock``/``practice_id``/
    ``created_by``, and pinning ``target_claim_id``/``target_claim_hash``/
    ``producing_model_profile_id``/``producing_model_profile_hash`` from this
    call's OWN inputs) and EVERY ONE is preserved in the returned
    ``challenges`` tuple — no selection, no filtering, no dropping. Any of
    the four ``CHALLENGE_TYPES`` the model's response did not cover is
    recorded as an explicit ``ChallengeTypeWaiver`` (:func:`_waivers_for_missing_types`);
    an entirely empty response (no challenges of any type) is itself a
    valid ``"proposal"`` outcome, with all four types waived.

    When the underlying call does NOT yield a schema-valid review (any
    ``StructuredGenerationStatus`` other than ``"proposal"`` —
    ``"schema_invalid"``, ``"refused"``, ``"content_filtered"``, ``"error"``,
    or ``"timed_out"``), that status is surfaced verbatim, with empty
    ``challenges``/``type_waivers`` and the failure's own
    ``validation_errors`` — no invalid challenge is ever emitted as a
    proposal (no-invalid-passes, inherited from E4-T02).

    ``attempts`` always carries the FULL ``generate_structured`` audit trail
    (every underlying model call made, in order), regardless of outcome.

    Args:
        adapter: the injected, provider-neutral ``ModelAdapter`` (E4-T01) —
            the sole channel through which this function ever reaches a
            model.
        target_claim_id: the ``mrr.contracts.claim.Claim`` this call
            searches for challenges against — caller-supplied; this function
            does not fetch or persist the claim itself.
        target_claim_hash: that claim's own content hash, pinned at call
            time.
        target_claim_assertion: the claim's own assertion text, used to
            build the prompt (never stored on any produced
            ``SkepticalChallenge``, which pins the claim by reference only —
            see the module docstring).
        target_claim_scope: the claim's own declared scope, used to build
            the prompt (also never stored on any produced challenge).
        model_profile_id: the ``ModelProfile`` this call is pinned to (see
            ``mrr.domain.model_adapter.ModelInvocationRequest``); also
            recorded on every produced ``SkepticalChallenge`` as
            ``producing_model_profile_id`` (independence lineage RECORDED,
            never computed here).
        model_profile_hash: that profile's own content hash, pinned at call
            time; also recorded on every produced challenge as
            ``producing_model_profile_hash``.
        operation_kind: this call's own deterministic/stochastic kind
            (MRR-FR-044) — no implicit default; the caller states it.
        redaction_policy: whether raw prompt/response text may be retained
            (MRR-FR-045) — no implicit default; the caller states it
            explicitly (``mrr.domain.model_adapter.DEFAULT_REDACTION_POLICY``
            for the safe choice).
        max_repair_attempts: the bounded-repair budget passed straight
            through to ``generate_structured``.
        practice_id: the practice minting every ``SkepticalChallenge`` this
            call produces.
        created_by: the identity (e.g. this skeptic's own agent URN)
            recorded as ``created_by`` on every ``SkepticalChallenge`` this
            call produces — never the model's own identity, since the model
            authored only the LLM-facing content, not the envelope.
        tool_names_available: forwarded to the underlying
            ``ModelInvocationRequest`` unchanged; empty by default (this
            role asks for structured JSON only, no tool use).
        id_factory: mints one URN per produced challenge; defaults to a
            fresh ``mrr.domain.identity.new_urn("skeptical-challenge")`` per
            call. Inject a deterministic sequence for a reproducible result
            (see the module docstring's "Determinism" section).
        clock: returns the instant recorded as ``created_at`` on every
            challenge produced by this call; defaults to the real current
            instant. Inject a fixed instant for a reproducible result.

    Returns:
        A :class:`SkepticalChallengeResult` carrying either a (possibly
        empty) set of schema-valid ``SkepticalChallenge`` proposals plus
        per-type coverage waivers, or an explicit, non-"proposal" failure —
        plus the full audit trail either way.
    """
    request = ModelInvocationRequest(
        model_profile_id=model_profile_id,
        model_profile_hash=model_profile_hash,
        prompt_text=_build_prompt(
            target_claim_assertion=target_claim_assertion,
            target_claim_scope=target_claim_scope,
        ),
        operation_kind=operation_kind,
        redaction_policy=redaction_policy,
        tool_names_available=tool_names_available,
    )

    result: StructuredGenerationResult[SkepticalReviewProposal] = generate_structured(
        adapter,
        request,
        SkepticalReviewProposal,
        max_repair_attempts=max_repair_attempts,
    )

    if result.status != "proposal":
        return SkepticalChallengeResult(
            status=result.status,
            challenges=(),
            type_waivers=(),
            attempts=result.attempts,
            validation_errors=result.validation_errors,
        )

    if result.proposal is None:  # pragma: no cover — guarded by generate_structured's own invariant
        raise ValueError(
            "generate_structured returned status 'proposal' without a proposal — this "
            "violates its own structural invariant"
        )

    now = clock()
    challenges = tuple(
        _to_challenge(
            challenge,
            id_=id_factory(),
            practice_id=practice_id,
            created_by=created_by,
            created_at=now,
            target_claim_id=target_claim_id,
            target_claim_hash=target_claim_hash,
            producing_model_profile_id=model_profile_id,
            producing_model_profile_hash=model_profile_hash,
        )
        for challenge in result.proposal.challenges
    )

    return SkepticalChallengeResult(
        status="proposal",
        challenges=challenges,
        type_waivers=_waivers_for_missing_types(challenges),
        attempts=result.attempts,
        validation_errors=result.validation_errors,
    )


__all__ = [
    "ChallengeTypeWaiver",
    "SkepticalChallengeResult",
    "SkepticalReviewProposal",
    "propose_skeptical_challenges",
]
