"""Unit tests for mrr.domain.lifecycles (E1-T04; extended to
``TRANSFER_LIFECYCLE`` by task-packets/E6-T01.yaml; extended to
``OBLIGATION_LIFECYCLE`` by task-packets/E6-T02.yaml; extended to
``METHOD_PROFILE_LIFECYCLE`` by task-packets/K0-T01.yaml; extended to
``QUESTION_MODEL_LIFECYCLE``/``CONCEPT_CHARTER_LIFECYCLE``/
``METHOD_PROTOCOL_LIFECYCLE``/``EVIDENCE_MATRIX_LIFECYCLE``/
``METHOD_RULING_LIFECYCLE``/``RESEARCH_DECISION_LIFECYCLE`` by
task-packets/K1-T01.yaml; extended to ``RELEASE_RECORD_LIFECYCLE`` by
task-packets/E8-T04.yaml — a plain, additive two-state/one-edge machine
(``released -> superseded``), extended purely by adding entries to the
existing parametrized tuples/dicts, exactly like every prior extension).

Deviation from task-packets/K1-T01.yaml's "no new test bodies required"
framing: ``RESEARCH_DECISION_LIFECYCLE`` is a deliberately one-state,
zero-transition machine (append-only, no lifecycle transitions at all —
see mrr.domain.lifecycles module docstring), which
``test_every_declared_edge_is_accepted``'s original unconditional
``assert machine.transitions`` would otherwise incorrectly flag as a bug.
That one assertion is adjusted below to allowlist this single legitimate
exception; every other test in this module is extended purely by adding
entries to the existing parametrized tuples/dicts, exactly as the packet
describes.

Acceptance-test mapping (task-packets/E1-T04.yaml):

- "every drawn edge is accepted for its machine" ->
  ``test_every_declared_edge_is_accepted``.
- "states without drawn outgoing edges reject every transition" ->
  ``test_terminal_states_reject_every_outgoing_transition``.
- "the typed error exposes machine, from-state, and to-state" ->
  ``test_invalid_transition_error_exposes_machine_from_and_to``.
- state-set-matches-schema-enum invariant -> the
  ``test_*_states_match_contract_status_literal`` group (all seven
  machines, since ADR-0005 gave TaskBundle a schema/contract status enum
  too, and task-packets/E6-T01.yaml/E6-T02.yaml/K0-T01.yaml give
  TransferContract/Obligation/MethodProfile one from the start).

Obligation-specific acceptance-test mapping (task-packets/E6-T02.yaml):

- "the resolve/defer actions each require the Obligation to currently be
  open; an illegal call ... raises InvalidTransitionError and persists/
  appends nothing" -> the ``OBLIGATION_LIFECYCLE``-parametrized cases in
  ``test_every_declared_edge_is_accepted``,
  ``test_representative_undrawn_transitions_are_rejected``, and
  ``test_terminal_states_reject_every_outgoing_transition``.
"""

from __future__ import annotations

from typing import get_args

import pytest
from mrr.contracts import (
    ClaimStatus,
    ConceptCharterStatus,
    CorrectionStatus,
    EvidenceMatrixStatus,
    MethodProfileStatus,
    MethodProtocolStatus,
    MethodRulingStatus,
    ObligationStatus,
    QuestionModelStatus,
    ReleaseStatus,
    ResearchDecisionStatus,
    ResearchScoreStatus,
    TaskBundleStatus,
    TransferStatus,
)
from mrr.domain.exceptions import InvalidTransitionError
from mrr.domain.lifecycles import (
    CLAIM_LIFECYCLE,
    CONCEPT_CHARTER_LIFECYCLE,
    CORRECTION_LIFECYCLE,
    EVIDENCE_MATRIX_LIFECYCLE,
    METHOD_PROFILE_LIFECYCLE,
    METHOD_PROTOCOL_LIFECYCLE,
    METHOD_RULING_LIFECYCLE,
    OBLIGATION_LIFECYCLE,
    QUESTION_MODEL_LIFECYCLE,
    RELEASE_RECORD_LIFECYCLE,
    RESEARCH_DECISION_LIFECYCLE,
    RESEARCH_SCORE_LIFECYCLE,
    TASK_BUNDLE_LIFECYCLE,
    TRANSFER_LIFECYCLE,
    StateMachine,
)

_ALL_MACHINES = [
    RESEARCH_SCORE_LIFECYCLE,
    TASK_BUNDLE_LIFECYCLE,
    CLAIM_LIFECYCLE,
    CORRECTION_LIFECYCLE,
    TRANSFER_LIFECYCLE,
    OBLIGATION_LIFECYCLE,
    METHOD_PROFILE_LIFECYCLE,
    QUESTION_MODEL_LIFECYCLE,
    CONCEPT_CHARTER_LIFECYCLE,
    METHOD_PROTOCOL_LIFECYCLE,
    EVIDENCE_MATRIX_LIFECYCLE,
    METHOD_RULING_LIFECYCLE,
    RESEARCH_DECISION_LIFECYCLE,
    RELEASE_RECORD_LIFECYCLE,
]

#: The one legitimate zero-transition machine (task-packets/K1-T01.yaml) —
#: see the module docstring's "Deviation" note.
_ZERO_TRANSITION_MACHINE_NAMES = frozenset({"ResearchDecision"})

#: Terminal states per machine, i.e. states that are never a transition
#: source in the declared edge set. Independently enumerated here (rather
#: than derived from the module under test) so the test actually checks the
#: module's edge list, not just its own internals.
_TERMINAL_STATES: dict[str, frozenset[str]] = {
    "ResearchScore": frozenset({"REJECTED", "SUSPENDED", "ARCHIVED"}),
    "TaskBundle": frozenset(
        {"DEFERRED", "REJECTED", "EXPIRED", "CANCELLED", "FAILED", "SEALED", "INVALID_RESULT"}
    ),
    "Claim": frozenset({"withdrawn", "superseded"}),
    "CorrectionEvent": frozenset(
        {"DELIVERY_PENDING", "RESOLVED", "PARTIALLY_RESOLVED", "REJECTED_BY_RECIPIENT"}
    ),
    "TransferContract": frozenset({"accepted", "adapted", "rejected", "deferred", "unresolved"}),
    "Obligation": frozenset({"resolved", "deferred"}),
    "MethodProfile": frozenset({"superseded"}),
    "QuestionModel": frozenset({"superseded"}),
    "ConceptCharter": frozenset({"superseded"}),
    # Only `executed` is terminal here — `amended` re-enters review under
    # the spec-08 amendment (commit 1d453bf) and is NOT a dead end, unlike
    # the literal pre-amendment table reading task-packets/K1-T01.yaml's own
    # derivation agent flagged.
    "MethodProtocol": frozenset({"executed"}),
    "EvidenceMatrix": frozenset({"superseded"}),
    "MethodRuling": frozenset({"superseded"}),
    "ResearchDecision": frozenset({"issued"}),
    "ReleaseRecord": frozenset({"superseded"}),
}


# ---------------------------------------------------------------------------
# Every drawn edge is accepted.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("machine", _ALL_MACHINES, ids=lambda m: m.name)
def test_every_declared_edge_is_accepted(machine: StateMachine) -> None:
    if machine.name not in _ZERO_TRANSITION_MACHINE_NAMES:
        assert machine.transitions, f"{machine.name}: expected at least one declared edge"
    for from_state, to_state in machine.transitions:
        assert machine.can_transition(from_state, to_state)
        machine.assert_transition(from_state, to_state)  # must not raise


# ---------------------------------------------------------------------------
# Representative undrawn transitions are rejected.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("machine", "from_state", "to_state"),
    [
        (RESEARCH_SCORE_LIFECYCLE, "SUSPENDED", "ACTIVE"),
        (RESEARCH_SCORE_LIFECYCLE, "SUSPENDED", "APPROVED"),
        (RESEARCH_SCORE_LIFECYCLE, "DRAFT", "ACTIVE"),  # skips a step
        (RESEARCH_SCORE_LIFECYCLE, "ARCHIVED", "DRAFT"),
        (CLAIM_LIFECYCLE, "review_required", "supported"),
        (CLAIM_LIFECYCLE, "review_required", "under_review"),
        (CLAIM_LIFECYCLE, "legacy_unverified", "under_review"),  # the flagged open question
        (CORRECTION_LIFECYCLE, "DELIVERY_PENDING", "RESOLVED"),
        (CORRECTION_LIFECYCLE, "DELIVERY_PENDING", "PARTIALLY_RESOLVED"),
        (TASK_BUNDLE_LIFECYCLE, "SEALED", "CREATED"),
        (TASK_BUNDLE_LIFECYCLE, "SEALED", "OFFERED"),
        (TASK_BUNDLE_LIFECYCLE, "SEALED", "RUNNING"),
        (TRANSFER_LIFECYCLE, "created", "accepted"),  # skips offered
        (TRANSFER_LIFECYCLE, "offered", "offered"),  # no self-transition
        (TRANSFER_LIFECYCLE, "accepted", "offered"),  # terminal, no way back
        (TRANSFER_LIFECYCLE, "deferred", "accepted"),  # no drawn revisit edge
        (OBLIGATION_LIFECYCLE, "open", "open"),  # no self-transition
        (OBLIGATION_LIFECYCLE, "resolved", "deferred"),  # terminal, no way back
        (OBLIGATION_LIFECYCLE, "deferred", "resolved"),  # terminal, no way back
        (OBLIGATION_LIFECYCLE, "deferred", "open"),  # no drawn revisit edge
        (METHOD_PROFILE_LIFECYCLE, "draft", "superseded"),  # skips accepted
        (METHOD_PROFILE_LIFECYCLE, "superseded", "accepted"),  # terminal, no way back
        (METHOD_PROFILE_LIFECYCLE, "accepted", "draft"),  # no drawn revisit edge
        (METHOD_PROFILE_LIFECYCLE, "draft", "draft"),  # no self-transition
        (QUESTION_MODEL_LIFECYCLE, "draft", "superseded"),  # skips accepted
        (QUESTION_MODEL_LIFECYCLE, "superseded", "accepted"),  # terminal, no way back
        (QUESTION_MODEL_LIFECYCLE, "draft", "draft"),  # no self-transition
        (CONCEPT_CHARTER_LIFECYCLE, "draft", "superseded"),  # skips accepted
        (CONCEPT_CHARTER_LIFECYCLE, "superseded", "accepted"),  # terminal, no way back
        (CONCEPT_CHARTER_LIFECYCLE, "draft", "draft"),  # no self-transition
        (METHOD_PROTOCOL_LIFECYCLE, "locked", "locked"),  # no self-transition
        (METHOD_PROTOCOL_LIFECYCLE, "amended", "executed"),  # still undrawn post-amendment
        (METHOD_PROTOCOL_LIFECYCLE, "amended", "amended"),  # no self-transition
        (METHOD_PROTOCOL_LIFECYCLE, "amended", "locked"),  # must re-review first
        (METHOD_PROTOCOL_LIFECYCLE, "executed", "amended"),  # terminal, no way back
        (METHOD_PROTOCOL_LIFECYCLE, "executed", "reviewed"),  # terminal, no way back
        (METHOD_PROTOCOL_LIFECYCLE, "draft", "locked"),  # skips reviewed
        (METHOD_PROTOCOL_LIFECYCLE, "reviewed", "executed"),  # skips locked
        (METHOD_PROTOCOL_LIFECYCLE, "reviewed", "amended"),  # skips locked
        (EVIDENCE_MATRIX_LIFECYCLE, "draft", "frozen"),  # skips active
        (EVIDENCE_MATRIX_LIFECYCLE, "superseded", "draft"),  # terminal, no way back
        (EVIDENCE_MATRIX_LIFECYCLE, "draft", "draft"),  # no self-transition
        (METHOD_RULING_LIFECYCLE, "pending", "superseded"),  # skips issued
        (METHOD_RULING_LIFECYCLE, "superseded", "pending"),  # terminal, no way back
        (METHOD_RULING_LIFECYCLE, "pending", "pending"),  # no self-transition
        (RESEARCH_DECISION_LIFECYCLE, "issued", "issued"),  # append-only, no self-transition
        (RELEASE_RECORD_LIFECYCLE, "released", "released"),  # no self-transition
        (RELEASE_RECORD_LIFECYCLE, "superseded", "released"),  # terminal, no way back
    ],
    ids=[
        "research-score-suspended-to-active",
        "research-score-suspended-to-approved",
        "research-score-draft-to-active-skips-step",
        "research-score-archived-to-draft",
        "claim-review-required-to-supported",
        "claim-review-required-to-under-review",
        "claim-legacy-unverified-to-under-review",
        "correction-delivery-pending-to-resolved",
        "correction-delivery-pending-to-partially-resolved",
        "task-bundle-sealed-to-created",
        "task-bundle-sealed-to-offered",
        "task-bundle-sealed-to-running",
        "transfer-created-to-accepted-skips-offered",
        "transfer-offered-to-offered-no-self-transition",
        "transfer-accepted-to-offered-terminal",
        "transfer-deferred-to-accepted-no-revisit",
        "obligation-open-to-open-no-self-transition",
        "obligation-resolved-to-deferred-terminal",
        "obligation-deferred-to-resolved-terminal",
        "obligation-deferred-to-open-no-revisit",
        "method-profile-draft-to-superseded-skips-accepted",
        "method-profile-superseded-to-accepted-terminal",
        "method-profile-accepted-to-draft-no-revisit",
        "method-profile-draft-to-draft-no-self-transition",
        "question-model-draft-to-superseded-skips-accepted",
        "question-model-superseded-to-accepted-terminal",
        "question-model-draft-to-draft-no-self-transition",
        "concept-charter-draft-to-superseded-skips-accepted",
        "concept-charter-superseded-to-accepted-terminal",
        "concept-charter-draft-to-draft-no-self-transition",
        "method-protocol-locked-to-locked-no-self-transition",
        "method-protocol-amended-to-executed-still-undrawn",
        "method-protocol-amended-to-amended-no-self-transition",
        "method-protocol-amended-to-locked-must-re-review-first",
        "method-protocol-executed-to-amended-terminal",
        "method-protocol-executed-to-reviewed-terminal",
        "method-protocol-draft-to-locked-skips-reviewed",
        "method-protocol-reviewed-to-executed-skips-locked",
        "method-protocol-reviewed-to-amended-skips-locked",
        "evidence-matrix-draft-to-frozen-skips-active",
        "evidence-matrix-superseded-to-draft-terminal",
        "evidence-matrix-draft-to-draft-no-self-transition",
        "method-ruling-pending-to-superseded-skips-issued",
        "method-ruling-superseded-to-pending-terminal",
        "method-ruling-pending-to-pending-no-self-transition",
        "research-decision-issued-to-issued-append-only",
        "release-record-released-to-released-no-self-transition",
        "release-record-superseded-to-released-terminal",
    ],
)
def test_representative_undrawn_transitions_are_rejected(
    machine: StateMachine, from_state: str, to_state: str
) -> None:
    assert not machine.can_transition(from_state, to_state)
    with pytest.raises(InvalidTransitionError):
        machine.assert_transition(from_state, to_state)


# ---------------------------------------------------------------------------
# Terminal states reject every outgoing transition.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("machine", _ALL_MACHINES, ids=lambda m: m.name)
def test_terminal_states_reject_every_outgoing_transition(machine: StateMachine) -> None:
    terminal_states = _TERMINAL_STATES[machine.name]
    assert terminal_states <= machine.states

    for terminal_state in terminal_states:
        for candidate_target in machine.states:
            assert not machine.can_transition(terminal_state, candidate_target)
            with pytest.raises(InvalidTransitionError):
                machine.assert_transition(terminal_state, candidate_target)


# ---------------------------------------------------------------------------
# Unknown state names fail closed.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("machine", _ALL_MACHINES, ids=lambda m: m.name)
def test_unknown_from_state_is_rejected(machine: StateMachine) -> None:
    assert not machine.can_transition("bogus", machine.initial_state)
    with pytest.raises(InvalidTransitionError):
        machine.assert_transition("bogus", machine.initial_state)


@pytest.mark.parametrize("machine", _ALL_MACHINES, ids=lambda m: m.name)
def test_unknown_to_state_is_rejected(machine: StateMachine) -> None:
    assert not machine.can_transition(machine.initial_state, "bogus")
    with pytest.raises(InvalidTransitionError):
        machine.assert_transition(machine.initial_state, "bogus")


@pytest.mark.parametrize("machine", _ALL_MACHINES, ids=lambda m: m.name)
def test_two_unknown_state_names_are_rejected(machine: StateMachine) -> None:
    with pytest.raises(InvalidTransitionError):
        machine.assert_transition("bogus", "also-bogus")


# ---------------------------------------------------------------------------
# The typed error carries machine, from-state, and to-state.
# ---------------------------------------------------------------------------


def test_invalid_transition_error_exposes_machine_from_and_to() -> None:
    with pytest.raises(InvalidTransitionError) as exc_info:
        RESEARCH_SCORE_LIFECYCLE.assert_transition("SUSPENDED", "ACTIVE")

    error = exc_info.value
    assert error.machine == "ResearchScore"
    assert error.from_state == "SUSPENDED"
    assert error.to_state == "ACTIVE"


def test_invalid_transition_error_is_a_domain_error() -> None:
    from mrr.domain.exceptions import DomainError

    assert issubclass(InvalidTransitionError, DomainError)


# ---------------------------------------------------------------------------
# Claim universal rules.
# ---------------------------------------------------------------------------


def test_claim_nonterminal_states_can_reach_review_required() -> None:
    nonterminal_states = CLAIM_LIFECYCLE.states - {"withdrawn", "superseded", "review_required"}
    assert nonterminal_states, "expected at least one nonterminal, non-review_required state"

    for state in nonterminal_states:
        assert CLAIM_LIFECYCLE.can_transition(state, "review_required")


def test_claim_review_required_self_transition_is_not_legal() -> None:
    """The universal rule is 'any nonterminal status -> review_required', but
    self-transitions are never drawn/legal (task-packets/E1-T04.yaml), so
    review_required does not gain a loop back to itself.
    """
    assert not CLAIM_LIFECYCLE.can_transition("review_required", "review_required")


@pytest.mark.parametrize("target", ["withdrawn", "superseded"])
def test_claim_every_nonterminal_state_can_reach_withdrawn_and_superseded(target: str) -> None:
    nonterminal_states = CLAIM_LIFECYCLE.states - {"withdrawn", "superseded"}

    for state in nonterminal_states:
        assert CLAIM_LIFECYCLE.can_transition(state, target)


def test_claim_terminal_states_do_not_transition_into_each_other() -> None:
    """Documents the module's chosen reading of the unqualified 'Any status ->
    WITHDRAWN' / 'Any status -> SUPERSEDED' wording: this module treats
    'any status' as 'any status other than the two terminal sinks
    themselves', since the same diagram block calls both terminal with no
    drawn outgoing edges. See the open-question note in
    mrr.domain.lifecycles for the alternative, fully literal reading.
    """
    assert not CLAIM_LIFECYCLE.can_transition("withdrawn", "superseded")
    assert not CLAIM_LIFECYCLE.can_transition("superseded", "withdrawn")


def test_claim_legacy_unverified_participates_only_in_universal_rules() -> None:
    assert CLAIM_LIFECYCLE.can_transition("legacy_unverified", "review_required")
    assert CLAIM_LIFECYCLE.can_transition("legacy_unverified", "withdrawn")
    assert CLAIM_LIFECYCLE.can_transition("legacy_unverified", "superseded")
    # No drawn incoming edge, and no drawn edge to under_review (open question
    # in mrr.domain.lifecycles).
    assert not CLAIM_LIFECYCLE.can_transition("legacy_unverified", "under_review")
    for state in CLAIM_LIFECYCLE.states:
        assert not CLAIM_LIFECYCLE.can_transition(state, "legacy_unverified")


# ---------------------------------------------------------------------------
# Drift protection: machine state sets match the owning schema/contract enum.
# ---------------------------------------------------------------------------


def test_research_score_states_match_contract_status_literal() -> None:
    assert RESEARCH_SCORE_LIFECYCLE.states == set(get_args(ResearchScoreStatus))


def test_claim_states_match_contract_status_literal() -> None:
    assert CLAIM_LIFECYCLE.states == set(get_args(ClaimStatus))


def test_correction_states_match_contract_status_literal() -> None:
    assert CORRECTION_LIFECYCLE.states == set(get_args(CorrectionStatus))


def test_task_bundle_states_match_contract_status_literal() -> None:
    assert TASK_BUNDLE_LIFECYCLE.states == set(get_args(TaskBundleStatus))


def test_transfer_states_match_contract_status_literal() -> None:
    assert TRANSFER_LIFECYCLE.states == set(get_args(TransferStatus))


def test_obligation_states_match_contract_status_literal() -> None:
    assert OBLIGATION_LIFECYCLE.states == set(get_args(ObligationStatus))


def test_method_profile_states_match_contract_status_literal() -> None:
    assert METHOD_PROFILE_LIFECYCLE.states == set(get_args(MethodProfileStatus))


def test_question_model_states_match_contract_status_literal() -> None:
    assert QUESTION_MODEL_LIFECYCLE.states == set(get_args(QuestionModelStatus))


def test_concept_charter_states_match_contract_status_literal() -> None:
    assert CONCEPT_CHARTER_LIFECYCLE.states == set(get_args(ConceptCharterStatus))


def test_method_protocol_states_match_contract_status_literal() -> None:
    assert METHOD_PROTOCOL_LIFECYCLE.states == set(get_args(MethodProtocolStatus))


def test_evidence_matrix_states_match_contract_status_literal() -> None:
    assert EVIDENCE_MATRIX_LIFECYCLE.states == set(get_args(EvidenceMatrixStatus))


def test_method_ruling_states_match_contract_status_literal() -> None:
    assert METHOD_RULING_LIFECYCLE.states == set(get_args(MethodRulingStatus))


def test_research_decision_states_match_contract_status_literal() -> None:
    assert RESEARCH_DECISION_LIFECYCLE.states == set(get_args(ResearchDecisionStatus))


def test_release_record_states_match_contract_status_literal() -> None:
    assert RELEASE_RECORD_LIFECYCLE.states == set(get_args(ReleaseStatus))


# ---------------------------------------------------------------------------
# Construction-time validation (defends the edge lists above against typos).
# ---------------------------------------------------------------------------


def test_state_machine_rejects_a_declared_self_transition() -> None:
    with pytest.raises(ValueError, match="self-transition"):
        StateMachine(
            name="Broken",
            states=frozenset({"A", "B"}),
            transitions=frozenset({("A", "A")}),
            initial_state="A",
        )


def test_state_machine_rejects_a_transition_with_an_undeclared_source() -> None:
    with pytest.raises(ValueError, match="not a declared state"):
        StateMachine(
            name="Broken",
            states=frozenset({"A", "B"}),
            transitions=frozenset({("C", "B")}),
            initial_state="A",
        )


def test_state_machine_rejects_a_transition_with_an_undeclared_target() -> None:
    with pytest.raises(ValueError, match="not a declared state"):
        StateMachine(
            name="Broken",
            states=frozenset({"A", "B"}),
            transitions=frozenset({("A", "C")}),
            initial_state="A",
        )


def test_state_machine_rejects_an_undeclared_initial_state() -> None:
    with pytest.raises(ValueError, match="initial_state"):
        StateMachine(
            name="Broken",
            states=frozenset({"A", "B"}),
            transitions=frozenset({("A", "B")}),
            initial_state="C",
        )
