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


class InvalidContentHashError(DomainError):
    """Raised when a ``content_hash`` argument does not match the exact
    ``$defs.sha256`` pattern (``mrr.crypto.hashing.SHA256_PATTERN``,
    ``sha256:<64 lowercase hex>``).

    Checked before any store lookup happens (``mrr.domain.artifacts.
    require_valid_content_hash``), so a malformed key fails closed instead of
    being silently treated as "not found" — collapsing "malformed input" and
    "absent key" into one generic error is exactly what AGENTS.md's
    prohibited-shortcuts list warns against ("collapsing `unknown`,
    `not_found`, `contradicted`, and `failed` into one generic error").
    """

    def __init__(self, content_hash: str) -> None:
        self.content_hash = content_hash
        super().__init__(f"not a valid sha256: content hash: {content_hash!r}")


class ArtifactNotFoundError(DomainError):
    """Raised by ``mrr.domain.artifacts.ArtifactStore.get`` and ``stat`` when
    no stored artifact exists for the requested (well-formed) content hash.
    Carries ``content_hash``. Never returns ``None`` or a boolean for a
    missing artifact.
    """

    def __init__(self, content_hash: str) -> None:
        self.content_hash = content_hash
        super().__init__(f"no artifact found for content hash {content_hash!r}")


class ScoreNotFoundError(DomainError):
    """Raised by ``mrr.services.research_score.service.ResearchScoreService``
    when a referenced ``ResearchScore`` id resolves to no stored object at
    all (docs/spec/01_SYSTEM_SPEC.md MRR-FR-004: "The system MUST reject
    execution when the referenced score is missing ..."). Carries
    ``score_id``. Never returns ``None`` or a boolean for a missing score.
    """

    def __init__(self, score_id: str) -> None:
        self.score_id = score_id
        super().__init__(f"no ResearchScore found for id {score_id!r}")


class ScoreNotApprovedError(DomainError):
    """Raised by ``ResearchScoreService.ensure_can_start_work`` when the
    latest revision of a ``ResearchScore`` exists but its status is not one
    of ``APPROVED``/``ACTIVE`` (MRR-FR-004: "Only APPROVED and ACTIVE
    revisions may start work", docs/spec/01_SYSTEM_SPEC.md section 6.1).
    Carries ``score_id`` and the ``actual_status`` observed, so a caller can
    react to *why* the score is not eligible without parsing a message
    string.
    """

    def __init__(self, score_id: str, actual_status: str) -> None:
        self.score_id = score_id
        self.actual_status = actual_status
        super().__init__(
            f"ResearchScore {score_id!r} cannot start work: status is "
            f"{actual_status!r}, must be APPROVED or ACTIVE"
        )


class ApprovalRequiredError(DomainError):
    """Raised by ``ResearchScoreService.approve`` when the score being
    approved carries no recorded approval reference (task-packets/
    E2-T01.yaml invariant: "APPROVED requires at least one recorded approval
    reference"). Carries ``score_id``.
    """

    def __init__(self, score_id: str) -> None:
        self.score_id = score_id
        super().__init__(
            f"ResearchScore {score_id!r} cannot be approved: no approval reference recorded"
        )


class ArtifactIntegrityError(DomainError):
    """Raised by ``mrr.domain.artifacts.ArtifactStore.get``/``stat``/``put``
    when a stored artifact's bytes no longer hash to its key — corruption,
    bit rot, or tampering (docs/spec/01_SYSTEM_SPEC.md MRR-FR-056 acceptance,
    "Recomputing a sealed artifact hash yields the stored value"). Carries
    ``expected`` (the requested/claimed key) and ``actual`` (the hash
    actually recomputed from the on-disk bytes). Reads fail closed: this is
    raised instead of returning the mismatched bytes.
    """

    def __init__(self, expected: str, actual: str) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"artifact integrity check failed: expected content hash {expected!r}, "
            f"computed {actual!r} from stored bytes"
        )
