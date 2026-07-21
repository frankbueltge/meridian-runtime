"""The kill-condition licensing check (task-packets/K1-T02.yaml, MRR-MTH-010):
a pure, framework-free precondition guarding
``mrr.services.claim.service.ClaimService.apply_kill_condition``. No
persistence, no I/O, no provider import (MRR-NFR-010).

--- Scope: licensing precondition only, never the kill DECISION itself ------

This module does NOT parse or evaluate a ``MethodProtocol.kill_conditions``
free-text entry against real ``EvidenceMatrix`` data to decide WHETHER a
specific condition is currently satisfied — that is explicitly
task-packets/K1-T03.yaml's/K1-T04.yaml's job, once real evidence exists to
evaluate against (task-packets/K1-T02.yaml forbidden_changes). This module
answers a narrower, already-decided question: given a caller-supplied
``ResearchDecision`` id that has ALREADY been resolved by the caller (via the
generic ``ObjectRepository``), does that resolved object actually license a
kill — i.e. is it genuinely a ``ResearchDecision`` whose
``decision_type == "kill_branch"``, not some other object kind or some other
``ResearchDecision`` decision type (``continue``/``revise``/``narrow_scope``/
``replicate``/``escalate_human_review``/``stop_insufficient_evidence``).

--- Reusing the existing `withdrawn` state, not inventing a new one --------

``ClaimService.apply_kill_condition`` reuses the EXISTING ``CLAIM_LIFECYCLE``
"any nonterminal status -> withdrawn" edge (no new ``Claim`` status is
invented) — MRR-MTH-010's "killed branches remain addressable and
inspectable" is exactly ``withdraw()``'s own already-documented behavior.
This module's only job is the licensing check above; the actual state
transition, the distinctly-typed ``claim.kill_condition_triggered`` event
(carrying the canonical ``KILL_CONDITION_TRIGGERED`` string in its own
payload), and the new ``decided_by`` edge are all wired at the service layer
— see ``ClaimService.apply_kill_condition``'s own docstring.
"""

from __future__ import annotations

from mrr.domain.exceptions import InvalidKillDecisionError

#: The one first-class object kind a kill decision must resolve to.
_RESEARCH_DECISION_KIND = "ResearchDecision"

#: The one ``ResearchDecision.decision_type`` value that licenses a kill.
_KILL_BRANCH_DECISION_TYPE = "kill_branch"


def assert_licenses_kill(
    *, research_decision_id: str, decision_kind: str, decision_type: str | None
) -> None:
    """Raise ``InvalidKillDecisionError`` unless the already-resolved object
    named by ``research_decision_id`` is a ``ResearchDecision``
    (``decision_kind == "ResearchDecision"``) whose own
    ``decision_type == "kill_branch"``.

    The caller (``ClaimService.apply_kill_condition``) has already resolved
    ``research_decision_id`` via ``ObjectRepository.get_latest`` and passes
    in that object's own ``kind``/``body.get("decision_type")`` — this
    function performs no lookup of its own (pure, no I/O), but still raises
    the fully informative typed error itself (carrying
    ``research_decision_id``/``actual_kind``/``actual_decision_type``)
    rather than returning a bare verdict for the caller to re-wrap, so the
    error is raised with complete context at the earliest possible point.

    Checked FIRST by ``apply_kill_condition``, before any write — a caught
    instance always means nothing was persisted.
    """
    if decision_kind != _RESEARCH_DECISION_KIND or decision_type != _KILL_BRANCH_DECISION_TYPE:
        raise InvalidKillDecisionError(
            research_decision_id,
            actual_kind=decision_kind,
            actual_decision_type=decision_type,
        )
