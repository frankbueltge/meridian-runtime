"""Mirrors schemas/research-decision.schema.json (docs/spec/08_RESEARCH_METHOD_KERNEL.md,
status ACCEPTED 2026-07-21, section 3 "Method-governance objects (Layer 1)").
Sixth and final of the six task-packets/K1-T01.yaml entities.

``decision_type`` is the exact seven-value closed vocabulary spec 08
section 3 names verbatim (``continue``, ``revise``, ``narrow_scope``,
``kill_branch``, ``replicate``, ``escalate_human_review``,
``stop_insufficient_evidence``) — nothing added, nothing dropped.
MRR-MTH-011's "``stop_insufficient_evidence`` decisions are successful
terminal results ... MUST NOT be silently omitted" is satisfied at the
CONTRACT level by validating ``stop_insufficient_evidence`` IDENTICALLY to
every other ``decision_type`` value — no extra required field, no
distinguishing ``is_error``/``is_terminal`` flag anywhere on this model,
since special-casing it would itself be the kind of silent differential
treatment MTH-011 warns against.

"Append-only (no lifecycle transitions — one revision, ever)" is realized
as: (a) ``status`` is a ``Literal["issued"]`` — the only value that has ever
existed for this kind; (b)
``mrr.domain.lifecycles.RESEARCH_DECISION_LIFECYCLE`` is declared with
exactly one state and an EMPTY transitions set, so any future service
driving it via ``StateMachine.assert_transition`` structurally can never
find a legal transition, including ``("issued", "issued")``.

This module does NOT add a ``decided_by`` field distinct from
``BaseObject.created_by``, unlike ``MethodRuling``'s explicit ``issued_by``
— flagged in task-packets/K1-T01.yaml specification_gaps as an asymmetry,
not resolved unilaterally. ``rationale`` is this task's own minimal addition
(not itself named by spec 08's one-line table entry) — see the same file's
specification_gaps for the disclosure.
"""

from __future__ import annotations

from typing import Literal

from mrr.contracts.common import BaseObject, Urn
from pydantic import Field

__all__ = ["ResearchDecision", "ResearchDecisionStatus", "ResearchDecisionType"]

#: Mirrors schemas/research-decision.schema.json's `decision_type` enum —
#: spec 08 section 3's exact seven values, verbatim.
ResearchDecisionType = Literal[
    "continue",
    "revise",
    "narrow_scope",
    "kill_branch",
    "replicate",
    "escalate_human_review",
    "stop_insufficient_evidence",
]

#: Mirrors schemas/research-decision.schema.json's `status` — a single-value
#: literal (spec 08 section 3's table: "ResearchDecision | ... | issued
#: (append-only)"). See mrr.domain.lifecycles.RESEARCH_DECISION_LIFECYCLE.
ResearchDecisionStatus = Literal["issued"]


class ResearchDecision(BaseObject):
    """Mirrors schemas/research-decision.schema.json.

    Every property in the schema's top-level `required` list is required
    here too. `decision_type` accepts every value identically — no
    conditional validator branches on it (see the module docstring).
    """

    kind: Literal["ResearchDecision"]
    decision_type: ResearchDecisionType
    protocol_id: Urn
    applies_to_analysis: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    status: ResearchDecisionStatus
