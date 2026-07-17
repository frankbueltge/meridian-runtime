"""Exception hierarchy for mrr.domain identity and hashing-policy primitives."""

from __future__ import annotations


class DomainError(Exception):
    """Base class for all mrr.domain errors."""


class InvalidEntityError(DomainError):
    """Raised when a URN entity segment does not match ``[a-z0-9-]+``."""


class InvalidUrnError(DomainError):
    """Raised when a value does not match the exact ``$defs.urn`` pattern in
    schemas/common.schema.json (``^urn:mrr:[a-z0-9-]+:[0-9A-HJKMNP-TV-Z]{26}$``).
    """


class InvalidTransitionError(DomainError):
    """Raised by ``mrr.domain.lifecycles.StateMachine.assert_transition`` when
    ``(from_state, to_state)`` is not a declared legal edge for that machine.

    Carries the three fields task-packets/E1-T04.yaml requires on the typed
    error ("the typed error exposes machine, from-state, and to-state"):
    ``machine`` (a state machine's ``name``, e.g. ``"Claim"``), ``from_state``,
    and ``to_state``. Raising always happens before any state is written
    anywhere, so a caller that lets this propagate leaves no partial state
    behind (task-packets/E1-T04.yaml invariant "invalid transitions ...
    create no partial state").
    """

    def __init__(self, machine: str, from_state: str, to_state: str) -> None:
        self.machine = machine
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(f"{machine}: illegal transition {from_state!r} -> {to_state!r}")
