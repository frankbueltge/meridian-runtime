"""``DomainEvent`` and the hash-chain function used to make the domain event
log tamper-evident (task-packets/E1-T06.yaml, MRR-NFR-001 and MRR-NFR-002).

``DomainEvent`` carries exactly the provenance fields
docs/spec/01_SYSTEM_SPEC.md MRR-NFR-001 requires ("Every authoritative state
transition MUST identify actor, timestamp, policy version, causation,
correlation, and object revision"):

- actor            -> ``actor``
- timestamp        -> ``occurred_at``
- policy version   -> ``policy_version``
- causation        -> ``causation_id``
- correlation      -> ``correlation_id``
- object revision  -> ``object_id`` + ``object_revision``

plus ``id`` (the event's own identity) and ``event_type``/``payload`` (what
happened). This is a deliberately reduced field set relative to the fuller
envelope sketched as an example in docs/spec/03_API_AND_EVENTS.md section 5.1
(which also shows ``event_version``, ``recorded_at``, a structured ``actor``
object with type/role, and a separate ``payload_hash``) -
task-packets/E1-T06.yaml's derived_decisions names exactly the ten fields
below, and AGENTS.md rule 3 ("do not invent domain behavior absent from the
specification") means the richer envelope shape is not implemented here. A
future task can widen this under its own packet.

Every field is mandatory except ``causation_id``, which is ``None`` only for
a root event - an event with no logical predecessor in its own causal chain.
That is a caller-observed invariant about *usage*; this module cannot check
it structurally, since nothing about one event in isolation says whether it
is truly a "root" or someone simply forgot to set the field.

This module carries no SQLAlchemy, driver, or framework import -
mrr.provenance stays framework-independent (MRR-NFR-010; enforced by the
import-linter contract in pyproject.toml and by
tests/unit/architecture/test_provenance_boundary.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from mrr.crypto.canonical import JSONValue
from mrr.domain.hashing_policy import compute_content_hash
from mrr.domain.identity import is_valid_urn


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """One not-yet-appended domain event. See the module docstring for the
    MRR-NFR-001 field mapping.

    Validated in ``__post_init__`` so a caller cannot construct a structurally
    invalid event in the first place ("DomainEvent field validation surface",
    task-packets/E1-T06.yaml): every ``Urn``-shaped field must actually match
    the exact ``urn:mrr:<entity>:<ulid>`` pattern
    (``mrr.domain.identity.is_valid_urn``), ``event_type``/``policy_version``
    must be non-empty, ``occurred_at`` must be an aware datetime, and
    ``object_revision`` must be ``>= 1``. ``payload`` is defensively
    shallow-copied so a caller mutating their own dict after constructing the
    event cannot silently change what later gets hashed and persisted.
    """

    id: str
    event_type: str
    occurred_at: datetime
    actor: str
    policy_version: str
    causation_id: str | None
    correlation_id: str
    object_id: str
    object_revision: int
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        _require_urn("id", self.id)
        _require_non_empty("event_type", self.event_type)
        if self.occurred_at.tzinfo is None:
            raise ValueError("DomainEvent.occurred_at must be an aware datetime (tzinfo required)")
        _require_urn("actor", self.actor)
        _require_non_empty("policy_version", self.policy_version)
        if self.causation_id is not None:
            _require_urn("causation_id", self.causation_id)
        _require_urn("correlation_id", self.correlation_id)
        _require_urn("object_id", self.object_id)
        if self.object_revision < 1:
            raise ValueError(
                f"DomainEvent.object_revision must be >= 1, got {self.object_revision!r}"
            )
        if not isinstance(self.payload, dict):
            raise ValueError("DomainEvent.payload must be a dict")
        # Defensive shallow copy: freezes the dataclass's own view of the
        # payload against later mutation of the caller's original dict.
        object.__setattr__(self, "payload", dict(self.payload))


def _require_urn(field_name: str, value: str) -> None:
    if not is_valid_urn(value):
        raise ValueError(f"DomainEvent.{field_name} is not a valid MRR urn: {value!r}")


def _require_non_empty(field_name: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"DomainEvent.{field_name} must not be empty")


def event_to_hashable_dict(event: DomainEvent, prev_hash: str | None) -> dict[str, JSONValue]:
    """Build the canonical dict hashed into one hash-chain entry's
    ``content_hash`` (task-packets/E1-T06.yaml derived_decisions: "every
    event carries a content hash computed with the existing
    mrr.domain.hashing_policy tooling plus the hash of the preceding event in
    the log").

    ``prev_hash`` is included under its own key (never named
    ``content_hash``, which ``mrr.domain.hashing_policy.compute_content_hash``
    would silently exclude) so that chaining actually holds: two events with
    identical own fields but different predecessors hash differently, and an
    event whose stored ``prev_hash`` is edited to point elsewhere no longer
    reproduces its recorded ``content_hash``. ``occurred_at`` is serialized to
    its ISO-8601 string form because the underlying canonicalizer
    (``mrr.crypto.canonical.canonicalize``, RFC 8785) only accepts JSON-safe
    scalars, lists, and string-keyed objects - not ``datetime`` instances.
    """
    return {
        "id": event.id,
        "event_type": event.event_type,
        "occurred_at": event.occurred_at.isoformat(),
        "actor": event.actor,
        "policy_version": event.policy_version,
        "causation_id": event.causation_id,
        "correlation_id": event.correlation_id,
        "object_id": event.object_id,
        "object_revision": event.object_revision,
        "payload": event.payload,
        "prev_hash": prev_hash,
    }


def compute_event_hash(event: DomainEvent, prev_hash: str | None) -> str:
    """Pure hash-chain function: the ``sha256:<hex>`` content hash for one
    log entry, covering the event's own fields AND ``prev_hash`` (chaining -
    each hash covers the predecessor's hash), excluding only ``content_hash``
    itself (which does not exist yet at the point this is computed).

    Exposed as a pure function - no database, no ``EventLog`` instance
    required - so unit and property tests can cover chaining and tamper
    sensitivity directly (task-packets/E1-T06.yaml: "Expose a pure function
    for this so unit tests cover it without a DB").
    """
    return compute_content_hash(event_to_hashable_dict(event, prev_hash))
