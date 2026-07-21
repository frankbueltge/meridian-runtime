"""The durable revocation FACT's own value object, and the pure
``trust_revoked_after_creation`` annotation predicate — task-packets/
E5-T07b.yaml.

docs/spec/04_SECURITY_AND_POLICY.md section 8.4: "A practice can revoke a
node or key. New objects are rejected after revocation. Existing objects
remain historically attributable and may receive a
``trust_revoked_after_creation`` annotation." Every E5-T02..T06 resolver
already enforces the FIRST sentence at intake (``mrr.domain.key_management.
KeyRing.is_valid_at`` fails closed on a revoked key) — that is unchanged and
out of this module's scope. This module answers the THIRD sentence only:
given a durably recorded revocation instant and an already-accepted object's
own ``signed_at`` (docs/spec/02_DOMAIN_MODEL.md section 1.3), was that object
signed before or after its key's recorded revocation?

Framework-free: no persistence, no I/O — pure functions/value objects over
already-in-memory values, CI-testable with no database. The durable store
this module's ``RevocationRecord`` is read FROM is ``mrr.persistence.
repositories.PostgresKeyRevocationStore``; this module knows nothing about
that store, exactly as ``mrr.domain.replay_retention`` knows nothing about
``PostgresProcessedIdStore``.

This module deliberately does NOT decide who calls
:func:`trust_revoked_after_creation`, or on what surfaced object/response
shape its result is attached — that is a future surfacing/projection
service's job (see task-packets/E5-T07b.yaml specification_gaps). It also
does NOT feed into ``mrr.domain.key_management.KeyRing`` or re-run any
E5-T02..T06 resolver: at-instant re-verification of an already-accepted
object is precisely what "existing objects remain historically
attributable" forbids. See that module's own docstring for the intake-time
enforcement this module never touches.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

__all__ = [
    "TRUST_REVOKED_AFTER_CREATION_ANNOTATION",
    "RevocationRecord",
    "trust_revoked_after_creation",
]

#: The exact docs/spec/04_SECURITY_AND_POLICY.md section 8.4 annotation
#: string, named once here so a future surfacing/projection service never
#: has to spell it out as an inline literal.
TRUST_REVOKED_AFTER_CREATION_ANNOTATION = "trust_revoked_after_creation"


@dataclass(frozen=True, slots=True, kw_only=True)
class RevocationRecord:
    """One durably recorded key revocation fact — the value object
    ``mrr.persistence.repositories.PostgresKeyRevocationStore.get_revocation``
    returns.

    Validates its own invariants in ``__post_init__`` rather than trusting
    the caller, mirroring ``mrr.domain.key_management.PublicKeyDescriptor``'s
    identical discipline: ``kid`` and ``practice_id`` must be non-empty, and
    ``revoked_at`` must be an aware datetime — a naive datetime silently
    compared against another naive or aware datetime is exactly the class of
    bug :func:`trust_revoked_after_creation`'s own correctness depends on
    avoiding.
    """

    kid: str
    practice_id: str
    revoked_at: datetime
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.kid:
            raise ValueError("kid must not be empty")
        if not self.practice_id:
            raise ValueError("practice_id must not be empty")
        if self.revoked_at.tzinfo is None:
            raise ValueError("revoked_at must be an aware datetime")


def trust_revoked_after_creation(signed_at: datetime, record: RevocationRecord | None) -> bool:
    """``True`` iff ``record`` is not ``None`` and ``signed_at`` is strictly
    BEFORE ``record.revoked_at`` — an object signed before its key's
    recorded revocation instant is annotated when surfaced.

    Never raises: a ``record`` of ``None`` (no durably recorded revocation
    for this object's signing key) yields ``False``, mirroring ``mrr.domain.
    key_management.KeyRing.is_valid_at``'s own "never raises, fails closed
    on None" shape — this function's own "fail closed" direction is simply
    "do not annotate" rather than "reject", since annotating an
    already-accepted object is advisory, not a gate.

    The boundary is exclusive on both ends by construction:
    ``signed_at < record.revoked_at`` is the ONLY case that annotates; a
    ``signed_at`` equal to or after ``revoked_at`` is not — that instant is
    already "objects signed after revocation fail closed" territory, owned
    by the unchanged E5-T02..T06 resolvers, not this function's concern. The
    two checks are therefore never redundant and never double-count the same
    instant.

    Args:
        signed_at: the already-accepted object's own signature ``signed_at``
            (docs/spec/02_DOMAIN_MODEL.md section 1.3).
        record: the durably recorded revocation fact for the object's
            signing key (``mrr.persistence.repositories.
            PostgresKeyRevocationStore.get_revocation``), or ``None`` if
            that key has never been recorded as revoked.

    Returns:
        Whether the annotation applies.
    """
    return record is not None and signed_at < record.revoked_at
