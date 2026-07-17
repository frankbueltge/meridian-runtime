"""Property tests for mrr.domain.lifecycles (E1-T04).

Acceptance-test mapping (task-packets/E1-T04.yaml): "property test - randomly
generated undrawn transitions never succeed", plus the packet's plan-time
guidance that ``can_transition`` and ``assert_transition`` must agree with
the declared edge set for arbitrary ``(from, to)`` pairs — including names
that are not one of the machine's declared states at all — and that a random
walk starting from the initial state never crosses an undrawn edge silently.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from mrr.domain.exceptions import InvalidTransitionError
from mrr.domain.lifecycles import (
    CLAIM_LIFECYCLE,
    CORRECTION_LIFECYCLE,
    RESEARCH_SCORE_LIFECYCLE,
    TASK_BUNDLE_LIFECYCLE,
    StateMachine,
)

_ALL_MACHINES = (
    RESEARCH_SCORE_LIFECYCLE,
    TASK_BUNDLE_LIFECYCLE,
    CLAIM_LIFECYCLE,
    CORRECTION_LIFECYCLE,
)


def _state_or_bogus(machine: StateMachine) -> st.SearchStrategy[str]:
    """Either one of the machine's declared states, or an arbitrary string
    that (almost certainly) is not one — exercising both "known but illegal
    pair" and "unknown state name" in the same strategy.
    """
    bogus = st.text(min_size=1, max_size=16).filter(lambda value: value not in machine.states)
    return st.one_of(st.sampled_from(sorted(machine.states)), bogus)


@given(machine=st.sampled_from(_ALL_MACHINES), data=st.data())
def test_can_transition_matches_the_declared_edge_set_exactly(
    machine: StateMachine, data: st.DataObject
) -> None:
    from_state = data.draw(_state_or_bogus(machine))
    to_state = data.draw(_state_or_bogus(machine))

    assert machine.can_transition(from_state, to_state) == (
        (from_state, to_state) in machine.transitions
    )


@given(machine=st.sampled_from(_ALL_MACHINES), data=st.data())
def test_assert_transition_never_silently_passes_an_illegal_pair(
    machine: StateMachine, data: st.DataObject
) -> None:
    from_state = data.draw(_state_or_bogus(machine))
    to_state = data.draw(_state_or_bogus(machine))

    if (from_state, to_state) in machine.transitions:
        machine.assert_transition(from_state, to_state)  # must not raise
    else:
        with pytest.raises(InvalidTransitionError) as exc_info:
            machine.assert_transition(from_state, to_state)
        assert exc_info.value.machine == machine.name
        assert exc_info.value.from_state == from_state
        assert exc_info.value.to_state == to_state


@given(
    machine=st.sampled_from(_ALL_MACHINES),
    data=st.data(),
)
def test_random_transition_sequences_never_cross_an_undrawn_edge(
    machine: StateMachine, data: st.DataObject
) -> None:
    """Walk from the machine's initial state through a random sequence of
    candidate next-states. Every step either lands on a declared edge (and
    the walk advances) or raises InvalidTransitionError (and the walk stays
    put) — there is no third outcome, in particular no silent no-op success
    on an undrawn edge.
    """
    candidates = data.draw(st.lists(_state_or_bogus(machine), min_size=1, max_size=25))

    current = machine.initial_state
    for candidate in candidates:
        edge_is_legal = machine.can_transition(current, candidate)

        if edge_is_legal:
            machine.assert_transition(current, candidate)  # must not raise
            current = candidate
        else:
            with pytest.raises(InvalidTransitionError):
                machine.assert_transition(current, candidate)
            # An illegal step must not move the walk.

        assert current in machine.states
