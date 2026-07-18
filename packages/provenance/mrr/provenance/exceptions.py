"""Exception hierarchy for mrr.provenance (task-packets/E1-T06.yaml).

Mirrors the shape of mrr.domain.exceptions.DomainError: one base class per
package, typed subclasses that carry the fields a caller needs to react
programmatically rather than parse a message string.
"""

from __future__ import annotations


class ProvenanceError(Exception):
    """Base class for all mrr.provenance errors."""


class ChainVerificationError(ProvenanceError):
    """Raised by chain-verification logic (``mrr.provenance.log.
    verify_appended_events``, and ``mrr.persistence.repositories.
    PostgresEventLog.verify_chain`` which calls it against a live database)
    the moment a hash-chain link fails to reproduce: either the stored
    ``prev_hash`` does not match the previous entry's actual content hash,
    or recomputing the entry's own hash from its currently-persisted fields
    does not reproduce its stored ``content_hash`` (task-packets/E1-T06.yaml
    invariant "tamper evidence holds - altering any persisted event payload
    breaks the hash chain verification from that point on").

    Carries ``sequence`` - the sequence number of the first entry at which
    the chain breaks. Verification stops at the first break; it does not
    attempt to characterize every subsequent entry, since a single break
    already invalidates everything chained after it.
    """

    def __init__(self, sequence: int) -> None:
        self.sequence = sequence
        super().__init__(f"domain event hash chain verification failed at sequence {sequence!r}")


class EventAppendError(ProvenanceError):
    """Raised by an ``mrr.provenance.log.EventLog`` implementation
    (``mrr.persistence.repositories.PostgresEventLog.append``) when a
    physical append fails for a reason the caller should be able to react to
    programmatically - e.g. a duplicate event id colliding with the
    ``domain_events.id`` uniqueness constraint. There is no boolean-returning
    failure form: a failed append always raises.

    Carries ``event_id`` (the event that failed to append) and ``reason`` (a
    human-readable description of the underlying failure).
    """

    def __init__(self, event_id: str, reason: str) -> None:
        self.event_id = event_id
        self.reason = reason
        super().__init__(f"failed to append domain event {event_id!r}: {reason}")
