"""Mirrors schemas/method-profile.schema.json (docs/spec/08_RESEARCH_METHOD_KERNEL.md,
status ACCEPTED 2026-07-21, section 2 "The Method Profile interface" and
section 3 "Method-governance objects (Layer 1)"). First task of the K epic
series (task-packets/K0-T01.yaml), numbered separately from the E epics so
the method layer stays visibly distinct from the governance cathedral.

A ``MethodProfile`` is a versioned, local methodological rulebook every
Research Method Profile MUST publish before its executor task family may
run — an executor task family plus a rulebook, NOT a researcher (spec 08
section 1). It is emphatically **not**
``mrr.contracts.model_profile.ModelProfile`` (E4-T01's LLM configuration
record) — the two names are kept rigorously distinct everywhere in this
codebase; a "method profile" governs how a kind of question is
investigated, a "model profile" configures one model call.

--- Identity: a freshly minted id, not `profile_key` (derived_decisions (b)) --

``profile_key`` (spec 08's own worked example: ``"systematic_evidence_synthesis"``,
section 5) and ``version`` (its semantic version) are BODY-ONLY fields, not
the object-repository key. Unlike ``NodeManifest`` (keyed by ``node_id``, a
proper ``urn``-typed field), spec 08 section 2 frames ``profile_key`` as a
plain human-legible slug with no urn shape implied anywhere in the spec
text. Object identity is therefore this object's own freshly minted
``BaseObject.id`` (``urn:mrr:method-profile:<ulid>``), mirroring
``ResearchScore``/``Claim`` — not ``profile_key``. A later semver revision
that supersedes an ACCEPTED profile mints a NEW id whose ``supersedes``
field (inherited from ``BaseObject``) points at the old id's latest
accepted revision; WITHIN one id's own lifecycle, ``draft -> accepted ->
superseded`` (``METHOD_PROFILE_LIFECYCLE``,
``mrr.domain.lifecycles``) tracks that ONE version's own review, exactly
mirroring how ``ResearchScore``'s ``ACTIVE -> SUPERSEDED`` already works.
Flagged for reviewer confirmation in task-packets/K0-T01.yaml
``specification_gaps`` — this is the majority-precedent reading, not the
only possible one.

--- `claim_types` reuses `mrr.contracts.claim.ClaimType` verbatim ----------

Spec 08 section 2 does not cite docs/spec/02_DOMAIN_MODEL.md section 2.11
explicitly, but ``Claim`` already has exactly this closed seven-value enum
and no other "claim type" vocabulary exists anywhere in this repository —
reusing it is the minimal-invention option (task-packets/K0-T01.yaml
derived_decisions (d), flagged for reviewer confirmation in
``specification_gaps``).

--- `ClaimCeiling` / `CLAIM_CEILING_ORDER`: new vocabulary, ordered once ----

The seven-level maximum-claim-ceiling taxonomy (spec 08 section 4:
``insufficient_evidence < mechanism_hypothesis < descriptive <
associational_unadjusted < associational_adjusted < causal_local <
causal_bounded``) is entirely NEW — no existing vocabulary in this
repository names it. ``CLAIM_CEILING_ORDER`` exposes it as an explicit
ordered tuple (weakest-language-permitted first, exactly the spec's own
ordering) for K1-T02's ceiling-enforcement task to import and rank
against, rather than re-deriving the ordering there. This task implements
NO comparison or enforcement function over it (task-packets/K0-T01.yaml
forbidden_changes: actual claim-ceiling ENFORCEMENT —
``CLAIM_CEILING_EXCEEDED`` at claim submission or projection rendering,
MRR-MTH-004/005/006 — is K1-T02's job) — it only declares and orders the
vocabulary once, for one later consumer.

--- `ExecutorStepKind`: distinct from `model_profile`'s `OperationKind` ----

Declaring deterministic vs. model-assisted steps (MRR-MTH-016) uses a NEW,
distinct two-value vocabulary, ``ExecutorStepKind = Literal["deterministic",
"model_assisted"]`` — deliberately NOT
``mrr.contracts.model_profile``'s (imported from ``mrr.domain.model_adapter``)
existing ``OperationKind`` (``Literal["deterministic", "stochastic"]``,
E4-T01), because the two describe different things: ``OperationKind`` is a
property of one MODEL INVOCATION's own configuration; ``ExecutorStepKind``
is a property of one PROFILE STEP in the abstract, declared before any
model is ever called. Conflating them would make a profile's step
declaration accidentally claim something about sampling temperature it has
no business claiming. Unlike ``OperationKind`` (shared between a
domain-layer port and a contracts-layer record, hence living in
``mrr.domain.model_adapter``, see that module's docstring), ``ExecutorStepKind``
has no domain-layer port consumer yet — K0-T02's capability dispatch layer
is explicitly out of this task's scope (forbidden_changes) — so it is
declared directly here, in ``mrr.contracts.method_profile``, exactly like
``CLAIM_CEILING_ORDER`` above. ``executor_steps`` is
``list[ExecutorStepDeclaration]``, each carrying ``name`` (str) and ``kind``
(``ExecutorStepKind``); required non-empty — a profile declaring zero steps
declares nothing about MTH-016 at all, which is not a valid declaration.

--- Every declared field is required unconditionally ----------------------

Every field spec 08 section 2 says a profile MUST declare (``profile_key``,
``version``, ``claim_types``, ``max_claim_ceiling``, ``protocol_form``,
``executor_task_family``, ``executor_steps``, ``inappropriate_uses``) is
schema-REQUIRED unconditionally — present on every revision, including the
very first draft — rather than "required only once accepted"
(task-packets/K0-T01.yaml derived_decisions (h)). This is what makes "a
profile without max-ceiling or protocol-form declaration is rejected" a
schema/contract-level fact, true before ``MethodProfileService`` is ever
called — not a service-level gate a draft could dodge. ``status`` (the
``draft -> accepted -> superseded`` lifecycle field, ``MethodProfileStatus``
below) is likewise always required, mirroring how ``ResearchScore``/``Claim`` each carry their own
schema-required ``status`` alongside ``BaseObject``'s identity/audit
fields, even though spec 08 section 2's own MUST-declare list (about the
method's own content, not its review state) does not separately name it.

``inappropriate_uses`` allows an empty list (task-packets/K0-T01.yaml
specification_gaps: spec 08 section 2 says only that a profile MUST
declare its inappropriate uses, not that the list must be non-empty) —
flagged for reviewer tightening if a non-empty floor is wanted.
"""

from __future__ import annotations

from typing import Annotated, Literal

from mrr.contracts.claim import ClaimType
from mrr.contracts.common import BaseObject, MRRModel
from pydantic import Field, StringConstraints

__all__ = [
    "CLAIM_CEILING_ORDER",
    "ClaimCeiling",
    "ExecutorStepDeclaration",
    "ExecutorStepKind",
    "MethodProfile",
    "MethodProfileStatus",
]

#: Mirrors schemas/method-profile.schema.json's `max_claim_ceiling` enum —
#: docs/spec/08_RESEARCH_METHOD_KERNEL.md section 4's seven-level taxonomy,
#: verbatim. Nothing added, nothing dropped, nothing reordered.
ClaimCeiling = Literal[
    "insufficient_evidence",
    "mechanism_hypothesis",
    "descriptive",
    "associational_unadjusted",
    "associational_adjusted",
    "causal_local",
    "causal_bounded",
]

#: The same seven values as `ClaimCeiling`, exposed as an explicit ordered
#: tuple — weakest-language-permitted first, exactly spec 08 section 4's own
#: ordering — for K1-T02's ceiling-enforcement task to import and rank
#: against rather than re-deriving the ordering there. This module declares
#: and orders the vocabulary only; it implements no comparison or
#: enforcement function over it (see the module docstring).
CLAIM_CEILING_ORDER: tuple[ClaimCeiling, ...] = (
    "insufficient_evidence",
    "mechanism_hypothesis",
    "descriptive",
    "associational_unadjusted",
    "associational_adjusted",
    "causal_local",
    "causal_bounded",
)

#: MRR-MTH-016: whether one executor step, declared in the abstract before
#: any model is ever called, is deterministic or model-assisted. Distinct
#: from `mrr.contracts.model_profile`'s `OperationKind` — see the module
#: docstring for why the two vocabularies are deliberately not shared.
ExecutorStepKind = Literal["deterministic", "model_assisted"]

#: Mirrors schemas/method-profile.schema.json's `status` enum — spec 08
#: section 3's table: "MethodProfile | ... | draft -> accepted -> superseded".
MethodProfileStatus = Literal["draft", "accepted", "superseded"]


class ExecutorStepDeclaration(MRRModel):
    """Mirrors one entry of `executor_steps`: a named step plus its explicit
    MRR-MTH-016 deterministic/model_assisted kind. Never inferred or
    silently defaulted — `kind` has no Python default, matching how
    `mrr.contracts.model_profile.ModelProfile.determinism` is never
    defaulted either (MRR-FR-044's "explicit, REQUIRED field" precedent,
    the same discipline MRR-MTH-016 asks for here).
    """

    name: str = Field(min_length=1)
    kind: ExecutorStepKind


class MethodProfile(BaseObject):
    """Mirrors schemas/method-profile.schema.json.

    Every property in the schema's top-level `required` list is required
    here too — this model defines no Python default for any of
    `profile_key`, `version`, `claim_types`, `max_claim_ceiling`,
    `protocol_form`, `executor_task_family`, `executor_steps`,
    `inappropriate_uses`, or `status` (see the module docstring's
    "Every declared field is required unconditionally" section).
    """

    kind: Literal["MethodProfile"]
    profile_key: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]
    version: Annotated[str, StringConstraints(pattern=r"^\d+\.\d+\.\d+$")]
    claim_types: list[ClaimType]
    max_claim_ceiling: ClaimCeiling
    protocol_form: str = Field(min_length=1)
    executor_task_family: list[Annotated[str, StringConstraints(min_length=1)]] = Field(
        min_length=1
    )
    executor_steps: list[ExecutorStepDeclaration] = Field(min_length=1)
    inappropriate_uses: list[str]
    status: MethodProfileStatus
