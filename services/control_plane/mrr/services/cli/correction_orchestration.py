"""Composition for ``mrr correction`` (task-packets/I1-T01.yaml) —
the ONLY place the ``CorrectionImpactService`` dependency graph is assembled
for the operator path, mirroring
``mrr.services.cli.verification_orchestration``'s shape exactly (that module
is this one's precedent for every choice below: a small set of module-level
functions over an ``Engine``, one private wiring helper, no argparse import
anywhere in it).

--- Why this module exists at all ---------------------------------------

``CorrectionImpactService`` was complete and tested but reachable from
nothing: ``mrr --help`` listed eleven subcommands and none of them was
``correction``, so the whole correction lifecycle could only be driven from
Python. docs/design/2026-07-26-wegkarte-erster-ecology-austausch.md records
the consequence precisely — ``CorrectionNotification`` is the only
``payload_kind`` that really occurs, and it "auch nur von innerhalb eines
Services, nicht von der Kommandozeile" could be produced. This module and
``correction_main`` close that, and nothing else: the service itself is
untouched.

--- No domain behavior lives here (task-packets/E2-T07.yaml's CLI law) ---

These functions resolve dependencies, call ONE service method, and return
its result. Every rule stays where it already is:
``CORRECTION_LIFECYCLE``'s legal edges, the per-recipient idempotence of
``notify_affected_practices`` (a recipient already recorded ``"sent"`` is
skipped), the ``DELIVERY_PENDING`` hop a failed delivery triggers, and the
revision discipline that makes a correction a NEW revision rather than an
overwrite — all remain ``CorrectionImpactService``'s business.

--- The optional constructor arguments are supplied deliberately --------

``CorrectionImpactService.__init__`` takes ``record_event``,
``record_revision_with_edges`` and ``delivery_pending_store`` as OPTIONAL
(default ``None``) purely so pre-existing call sites outside E6-T03/T04/T06's
allowed paths kept working. An operator path is not such a call site: it
supplies ``record_event`` because ``notify_affected_practices`` raises
``ValueError`` without it as soon as more than one recipient's event must be
recorded. ``record_revision_with_edges``/``delivery_pending_store`` stay
unsupplied here on purpose — they belong to the RECEIVING side
(``record_response``) and to the online delivery-retry path, both of which
task-packets/I1-T01.yaml puts explicitly out of scope.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.contracts import CorrectionEvent
from mrr.domain.envelope_transport import EnvelopeTransport
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject
from mrr.persistence.repositories import (
    PostgresEdgeRepository,
    PostgresEventLog,
    PostgresObjectRepository,
)
from mrr.services.claim.service import ClaimService
from mrr.services.claim.service import bind_edge_unit_of_work as _bind_claim_edge_uow
from mrr.services.claim.service import bind_unit_of_work as _bind_claim_uow
from mrr.services.correction.service import (
    CorrectionImpactService,
    NotificationRecipient,
    bind_event_unit_of_work,
    bind_unit_of_work,
)
from sqlalchemy import Engine

__all__ = [
    "load_correction",
    "notify_correction_recipients",
    "propagate_correction_impact",
    "record_correction",
]

#: The stored ``kind`` a correction resolves to. Used only to refuse a
#: mismatched ``--correction-id`` with a clear message instead of letting a
#: ``model_validate`` failure surface as an opaque error — the same guard
#: ``verification_orchestration`` applies to ``--claim-id``.
_CORRECTION_STORED_KIND = "CorrectionEvent"


def _resolved_correlation_id(correlation_id: str | None) -> str:
    return correlation_id if correlation_id is not None else new_urn("research-run")


def _build_service(engine: Engine) -> tuple[CorrectionImpactService, PostgresObjectRepository]:
    """Assemble the service exactly as tests/integration's own
    ``_services_for_notification`` does — including ``record_event``, for the
    reason the module docstring gives.
    """
    object_repository = PostgresObjectRepository(engine)
    event_log = PostgresEventLog(engine)
    edge_repository = PostgresEdgeRepository(engine)

    claim_service = ClaimService(
        object_repository,
        event_log,
        edge_repository,
        _bind_claim_uow(engine, object_repository, event_log),
        _bind_claim_edge_uow(engine, event_log),
    )

    service = CorrectionImpactService(
        object_repository,
        edge_repository,
        claim_service,
        event_log,
        bind_unit_of_work(engine, object_repository, event_log),
        bind_event_unit_of_work(engine, event_log),
    )
    return service, object_repository


def load_correction(engine: Engine, correction_id: str) -> CorrectionEvent:
    """Read one correction's CURRENT revision, read-only — the whole of
    ``mrr correction status``.

    Raises:
        ObjectNotFoundError: ``correction_id`` resolves to nothing.
        ValueError: it resolves to a stored object that is not a
            ``CorrectionEvent``.
    """
    stored = PostgresObjectRepository(engine).get_latest(correction_id)
    if stored.kind != _CORRECTION_STORED_KIND:
        raise ValueError(
            f"--correction-id {correction_id!r} resolves to a stored object of kind "
            f"{stored.kind!r}, not {_CORRECTION_STORED_KIND!r}"
        )
    return CorrectionEvent.model_validate(stored.body)


def record_correction(
    engine: Engine,
    *,
    correction: CorrectionEvent,
    actor: str,
    policy_version: str,
    correlation_id: str | None = None,
) -> StoredObject:
    """One call to ``CorrectionImpactService.record`` (MRR-FR-090)."""
    service, _ = _build_service(engine)
    return service.record(
        correction,
        actor=actor,
        policy_version=policy_version,
        correlation_id=_resolved_correlation_id(correlation_id),
    )


def propagate_correction_impact(
    engine: Engine,
    *,
    correction_id: str,
    actor: str,
    policy_version: str,
    correlation_id: str | None = None,
) -> StoredObject:
    """One call to ``CorrectionImpactService.propagate_impact``."""
    service, _ = _build_service(engine)
    return service.propagate_impact(
        correction_id,
        actor=actor,
        policy_version=policy_version,
        correlation_id=_resolved_correlation_id(correlation_id),
    )


def notify_correction_recipients(
    engine: Engine,
    *,
    correction_id: str,
    recipients: Sequence[NotificationRecipient],
    transport: EnvelopeTransport,
    sender_node_id: str,
    notifying_practice_id: str,
    signing_key: Ed25519PrivateKey,
    signing_key_id: str,
    sent_at: datetime,
    expires_at: datetime,
    actor: str,
    policy_version: str,
    correlation_id: str | None = None,
) -> StoredObject:
    """One call to ``CorrectionImpactService.notify_affected_practices``.

    ``transport`` is injected by the caller rather than constructed here —
    the port stays the seam, so this function is as usable with an in-test
    fake as with the offline
    ``mrr.adapters.federation.local.LocalFilesystemEnvelopeTransport`` the
    CLI passes in.
    """
    service, _ = _build_service(engine)
    return service.notify_affected_practices(
        correction_id,
        recipients=recipients,
        transport=transport,
        sender_node_id=sender_node_id,
        notifying_practice_id=notifying_practice_id,
        signing_key=signing_key,
        signing_key_id=signing_key_id,
        sent_at=sent_at,
        expires_at=expires_at,
        actor=actor,
        policy_version=policy_version,
        correlation_id=_resolved_correlation_id(correlation_id),
    )
