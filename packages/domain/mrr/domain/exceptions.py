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


class RevisionConflictError(DomainError):
    """Raised by ``mrr.domain.repositories.ObjectRepository.insert_revision``
    when a caller's ``expected_current_revision`` does not match the
    object's actual current revision — whether caught by the pre-insert
    check or by a same-instant concurrent writer colliding on the
    ``(id, revision)`` primary key (see
    ``packages/persistence/mrr/persistence/repositories.py``). Exactly one
    concurrent writer with the same expectation wins; every other loses with
    this error, never a boolean.

    Carries ``id`` (the object identifier), ``expected`` (the caller's
    ``expected_current_revision``, or ``None`` for "must be new"), and
    ``actual`` (the object's real current revision at the time of the
    conflict, or ``None`` if the object still does not exist).
    """

    def __init__(self, id: str, expected: int | None, actual: int | None) -> None:
        self.id = id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"revision conflict for {id!r}: expected current revision {expected!r}, "
            f"actual {actual!r}"
        )


class UnknownEdgeTypeError(DomainError):
    """Raised when an edge type is not one of
    ``mrr.domain.repositories.EDGE_VOCABULARY`` (docs/spec/02_DOMAIN_MODEL.md
    section 3). Fail-closed: checked in code before any database write, in
    addition to the database's own CHECK constraint on the same vocabulary.
    """

    def __init__(self, edge_type: str) -> None:
        self.edge_type = edge_type
        super().__init__(f"unknown edge type: {edge_type!r}")


class ObjectNotFoundError(DomainError):
    """Raised by ``mrr.domain.repositories.ObjectRepository.get_latest`` and
    ``get_revision`` when no matching stored object exists. Carries ``id``
    and, for ``get_revision``, the specific ``revision`` that was requested
    (``None`` when raised from ``get_latest``, which has no specific
    revision to report).
    """

    def __init__(self, id: str, revision: int | None = None) -> None:
        self.id = id
        self.revision = revision
        if revision is None:
            super().__init__(f"no object found for id {id!r}")
        else:
            super().__init__(f"no object found for id {id!r} at revision {revision!r}")
