"""Mirrors schemas/hypothesis.schema.json (docs/spec/02_DOMAIN_MODEL.md
section 2.4, "Hypothesis and ResearchBranch": "Hypothesis captures a
falsifiable proposition or an explicit insufficient_evidence branch.").
Third task of Epic E4 (task-packets/E4-T03.yaml); the fourteenth entity
schema/model pair in this repository.

--- One entity, not two (task-packets/E4-T03.yaml derived_decisions) -------

docs/spec/02_DOMAIN_MODEL.md 2.4 lists ONE field set under the single
heading "Hypothesis and ResearchBranch" — branch role, branch budget, and
stop conditions are folded into this one model rather than a separate
``ResearchBranch`` entity, exactly as the task packet's derived_decisions
directs.

--- "dependencies and assumptions": one domain bullet, two fields ----------

Domain 2.4 lists "dependencies and assumptions" as a single bullet, but
MRR-FR-012 names "dependencies" specifically as one of the five required
declarations, distinct from a branch's starting assumptions. This module
therefore carries two separate fields — ``dependencies`` (URNs of other
objects this branch depends on) and ``assumptions`` (free-text epistemic
assumptions) — rather than one combined field, so MRR-FR-012's own
"dependencies" wording maps onto exactly one field.

--- required_capabilities: not a specification-given structure -------------

Domain 2.4 gives no further structure for "required capabilities" beyond
the phrase itself. This module models it as a plain list of capability
NAMES (``mrr.contracts.node_manifest.CapabilityDefinition.name``), not full
``{name, version}`` references (contrast
``mrr.contracts.task_bundle.CapabilityRef``, which names ONE capability a
task bundle executes) — a hypothesis branch may require several
capabilities to exist somewhere in the capability registry, with no
version pinned yet at the proposal stage. Not spec-derived; flagged as an
open specification question in this task's PR body, mirroring
``mrr.contracts.evidence_anchor.RecomputationStatus``'s own "not a
specification-given vocabulary" precedent.

--- A Hypothesis is NOT a claim of result (MRR-FR-014, AGENTS.md rule 7) ---

This model declares no ``verified``/``supported``/``result``/
``authoritative`` field of any kind, and ``status`` is a small, CLOSED
lifecycle enum (``proposed``/``selected``/``deferred``/``rejected``) with
NO "verified" value anywhere — mirroring
``mrr.contracts.model_invocation.ModelInvocation``'s own "there is
structurally no field anywhere on this object a caller could set to mark a
response accepted" precedent. The planner (task-packets/E4-T03.yaml) emits
only ``"proposed"``; nothing on this model lets the planner (or anything
else) mark its own hypothesis verified.

--- Falsifiability, enforced twice ------------------------------------------

MRR-FR-012: "Each branch MUST declare falsifiable expectations ..." For
every ``branch_role`` EXCEPT ``"insufficient_evidence"``,
``predicted_observations`` and ``disconfirming_observations`` must each be
non-empty (a falsifiable proposition needs both a predicted and a
disconfirming observation). ``"insufficient_evidence"`` MAY leave both
empty but MUST record a non-null ``insufficiency_rationale`` instead —
domain 2.4's own "explicit insufficient_evidence branch" escape. This is
not expressible as a plain per-field constraint (it depends on
``branch_role``), so — mirroring ``mrr.contracts.claim.Claim``'s own single
schema conditional, and ``mrr.contracts.evidence_anchor.EvidenceAnchor``'s
and ``mrr.contracts.verification_result.VerificationResult``'s two-branch
conditionals — it is enforced identically in TWO places: the JSON Schema's
two if/then conditionals (checked by ``scripts/check_contracts.py``) and
``check_falsifiability``/``Hypothesis._falsifiability_or_insufficiency_rationale``
below. ``check_falsifiability`` is exported (not merely a private method) so
the planner's own LLM-facing target model
(``mrr.services.planner.service``) can enforce the EXACT SAME conditional on
raw model output before it is ever wrapped into a full ``Hypothesis`` —
one rule, defined once, wired into two separate Pydantic models.

``insufficiency_rationale`` itself is not a domain 2.4 bullet — it is this
task's own minimal, necessary addition (the task packet's own invariant
wording: "must record an insufficiency rationale"), the same kind of
documented addition ``mrr.contracts.evidence_anchor.EvidenceAnchor.
anchor_unavailable_reason`` already is for its entity.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Literal, Self

from mrr.contracts.common import BaseObject, Budget, Scope, Urn
from pydantic import Field, StringConstraints, model_validator

#: MRR-FR-010: exactly these six branch roles, closed — no seventh value.
BranchRole = Literal[
    "confirmatory",
    "falsification",
    "alternative_explanation",
    "replication",
    "method_independent",
    "insufficient_evidence",
]

#: The six roles above, in the exact same order everywhere the vocabulary is
#: enumerated (this tuple, the schema `enum`, and the planner's own
#: role-coverage/waiver check) — single-sourced so the "exactly six, this
#: order" fact cannot silently drift between the contract and the planner.
BRANCH_ROLES: tuple[BranchRole, ...] = (
    "confirmatory",
    "falsification",
    "alternative_explanation",
    "replication",
    "method_independent",
    "insufficient_evidence",
)

#: A small lifecycle enum with NO "verified"/"result" value anywhere — see
#: the module docstring's "A Hypothesis is NOT a claim of result" section.
#: The planner emits only "proposed"; "selected"/"deferred"/"rejected" are
#: written by a later, out-of-scope prioritization step (MRR-FR-013).
HypothesisStatus = Literal["proposed", "selected", "deferred", "rejected"]


def check_falsifiability(
    *,
    branch_role: BranchRole,
    predicted_observations: Sequence[str],
    disconfirming_observations: Sequence[str],
    insufficiency_rationale: str | None,
) -> None:
    """The one falsifiability conditional, defined once and shared by
    ``Hypothesis``'s own model_validator and the planner's LLM-facing target
    model (see the module docstring's "Falsifiability, enforced twice"
    section). Raises ``ValueError`` — never returns a boolean — matching
    every other ``model_validator`` in this codebase, so a caller wiring
    this into its own ``model_validator(mode="after")`` needs no
    translation step.
    """
    if branch_role == "insufficient_evidence":
        if insufficiency_rationale is None:
            raise ValueError(
                "a Hypothesis with branch_role 'insufficient_evidence' must record a "
                "non-null insufficiency_rationale"
            )
        return
    if not predicted_observations or not disconfirming_observations:
        raise ValueError(
            f"a Hypothesis with branch_role {branch_role!r} must have at least one "
            "predicted_observations entry and at least one disconfirming_observations "
            "entry (only branch_role 'insufficient_evidence' may leave both empty)"
        )


class Hypothesis(BaseObject):
    """Mirrors schemas/hypothesis.schema.json.

    Every property is in the schema's top-level `required` list except
    ``insufficiency_rationale`` — the one field with a Python default of
    ``None`` here, non-null exactly when ``branch_role ==
    "insufficient_evidence"`` (see ``check_falsifiability``).
    """

    kind: Literal["Hypothesis"]
    statement: str = Field(min_length=1)
    branch_role: BranchRole
    predicted_observations: list[str]
    disconfirming_observations: list[str]
    insufficiency_rationale: Annotated[str, StringConstraints(min_length=1)] | None = None
    scope: Scope
    dependencies: list[Urn]
    assumptions: list[str]
    methods: list[str]
    required_capabilities: list[str]
    budget: Budget
    stop_conditions: list[str]
    priority_rationale: str = Field(min_length=1)
    status: HypothesisStatus

    @model_validator(mode="after")
    def _falsifiability_or_insufficiency_rationale(self) -> Self:
        check_falsifiability(
            branch_role=self.branch_role,
            predicted_observations=self.predicted_observations,
            disconfirming_observations=self.disconfirming_observations,
            insufficiency_rationale=self.insufficiency_rationale,
        )
        return self


__all__ = [
    "BRANCH_ROLES",
    "BranchRole",
    "Hypothesis",
    "HypothesisStatus",
    "check_falsifiability",
]
