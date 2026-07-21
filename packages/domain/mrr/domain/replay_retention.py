"""Pure retention-horizon rule for the durable processed-id store
(task-packets/E5-T07.yaml) — the instant a recorded replay-detection id
becomes safe to prune, without ever reopening the replay window it exists to
close.

docs/spec/04_SECURITY_AND_POLICY.md section 8.2: "Processed envelope IDs are
retained for replay detection according to policy" — this module fixes the
one part of "according to policy" that is NOT itself a policy choice but a
correctness invariant: an id may be pruned only once no envelope or bundle
carrying it could still be inside its own validity window
(``[sent_at/created_at, expires_at)`` — ``mrr.domain.envelope_validation.
validate_inbound_envelope`` condition 2, ``mrr.domain.offline_bundle.
validate_inbound_bundle`` condition 2, BOTH unchanged by this task). Retention
therefore anchors on the object's own ``expires_at`` (stored on the
processed-id row precisely so this function can be evaluated later without
re-fetching the original object) plus a non-negative ``grace`` period a
deployment may add on top for clock skew or operational margin — never less.
The CONCRETE value of ``grace`` is a policy decision left to the caller/
deployment (task-packets/E5-T07.yaml required_output: "the concrete
retention grace as a policy value" is explicitly deferred); this module only
guarantees that whatever non-negative grace is chosen, the resulting horizon
can never precede the object's own expiry.

Framework-free: no persistence, no I/O — a pure function over already-
in-memory values (``datetime``/``timedelta``), CI-testable with no database.
The durable store built on top of it is
``mrr.persistence.repositories.PostgresProcessedIdStore``, which calls
:func:`processed_id_retention_horizon` per candidate row inside its
``prune_expired`` method (see that method's own docstring for why it is a
per-row Python call rather than an independent SQL expression reimplementing
the same rule) — so there is only ever one place a future change to the
retention rule needs to happen.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

__all__ = [
    "PROCESSED_ID_KINDS",
    "ProcessedIdKind",
    "processed_id_retention_horizon",
]

#: The two identifier namespaces a processed-id row can discriminate between.
#: A bundle_id and a message_id are already distinct URN entity segments
#: (``mrr.domain.identity``'s ``urn:mrr:<entity>:<ulid>`` — "offline-bundle"
#: vs "node-message-envelope"), so they cannot collide even sharing one
#: table; this column exists so a single retention sweep and a single
#: UNIQUE constraint on ``(recipient_node_id, id)`` cover both namespaces at
#: once (task-packets/E5-T07.yaml derived_decisions), rather than
#: maintaining two near-identical tables.
ProcessedIdKind = Literal["envelope", "bundle"]

#: Verbatim vocabulary for the ``processed_ids.id_kind`` CHECK constraint —
#: the single source of truth ``mrr.persistence.tables`` imports from,
#: mirroring ``mrr.domain.repositories.EDGE_VOCABULARY``'s identical
#: drift-prevention pattern (see that table's own module docstring in
#: ``mrr.persistence.tables``).
PROCESSED_ID_KINDS: tuple[ProcessedIdKind, ...] = ("bundle", "envelope")


def processed_id_retention_horizon(expires_at: datetime, *, grace: timedelta) -> datetime:
    """Return the instant a processed-id row for an object whose own
    ``expires_at`` is ``expires_at`` becomes safe to prune.

    ``horizon = expires_at + grace``, so ``horizon >= expires_at`` for any
    non-negative ``grace`` — pruning at or after this instant can never
    reopen the replay window ``validate_inbound_envelope``/
    ``validate_inbound_bundle`` check against, because that window's upper
    bound is exactly this same ``expires_at`` (never later). Once the
    horizon has passed, no envelope or bundle bearing this id could still
    pass its own validity-window check even if resubmitted, so the durable
    replay record is no longer needed to reject it.

    Args:
        expires_at: the processed object's own ``expires_at`` (the
            envelope's or bundle's field of that name) at the time it was
            recorded.
        grace: additional retention margin (e.g. for clock skew or an
            operational buffer) added on top of ``expires_at``. MUST be
            non-negative — this is enforced, not merely documented: a
            caller that could plumb in a negative grace as a permitted
            override would let retention precede expiry and silently
            reopen the replay window, which is exactly the failure mode
            this function exists to make impossible by construction.

    Returns:
        ``expires_at + grace``.

    Raises:
        ValueError: if ``grace`` is negative.
    """
    if grace < timedelta(0):
        raise ValueError(f"grace must be >= timedelta(0), got {grace!r}")
    return expires_at + grace
