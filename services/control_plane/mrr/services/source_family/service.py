"""``SourceFamilyService`` (task-packets/E3-T03.yaml): persists ``SourceFamily``
objects — an append-only revision-1 write plus one domain event, atomically,
via the existing E1-T06 unit-of-work primitive. Third task of Epic E3
(claim, evidence, correction kernel); the closest templates are
``mrr.services.evidence.service.SourceRecordService``/``EvidenceAnchorService``
(E3-T01, named explicitly as templates by task-packets/E3-T03.yaml) and
``mrr.services.claim.service.ClaimService`` (E3-T02) for how an unsigned,
plain-revision entity is wired.

--- Additive only: no update/mutate method -----------------------------------

``SourceFamily`` never removes or deletes a member source — it only records
dependence (task-packets/E3-T03.yaml invariant: "representation is
additive"). This service reflects that at the API surface: exactly like
``SourceRecordService``/``EvidenceAnchorService``, it exposes exactly one
method, ``create`` — no lifecycle, no update, no delete. There is nothing
here that could touch a referenced ``SourceRecord``'s own object revision at
all; ``member_source_ids`` is read by nothing this service does, only
carried as part of the ``SourceFamily`` body being persisted. A correction
to an already-recorded family would be a new revision written by a caller
who already holds the prior one (E3-T05/T06 territory) — out of this task's
scope, matching ``RunManifestRecorder``'s and the evidence services' own
"no update/mutate beyond append-only revisions" precedent.

--- Caller mints id/content_hash/created_* ------------------------------------

Matches ``SourceRecordService.create``/``EvidenceAnchorService.create``'s own
convention (task-packets/E3-T03.yaml: "persist as revision 1 + a
`source_family.created` event, atomically" — the caller already built a
fully valid, schema-conformant ``SourceFamily``; this service's only job is
to validate ``revision == 1`` and persist it plus its event, atomically).

--- Not wired to the independence calculation here -----------------------------

This module provides the REPRESENTATION only. Nothing here validates that
``member_source_ids`` actually resolve to real, stored ``SourceRecord``
objects (structural URN validation happens at the contract layer, same as
``SourceRecord.source_family_id``'s own "carried unresolved" precedent from
E3-T01), computes any independence weight, or flags a family for review.
The independence CALCULATION that consumes a ``SourceFamily`` is E3-T05 —
this task's own ``forbidden_changes``.

--- No new typed errors --------------------------------------------------------

``create`` raises a plain ``ValueError`` for the one sanity check it
performs (``revision != 1``), exactly matching
``SourceRecordService.create``'s own equivalent check — not a new
``mrr.domain.exceptions`` type, since nothing beyond that one caller-mistake
case needs a typed error here.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from mrr.contracts import SourceFamily, Urn
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject
from mrr.persistence.unit_of_work import (
    RecordRevisionWithEvent as RecordRevisionWithEvent,
)
from mrr.persistence.unit_of_work import (
    bind_unit_of_work as bind_unit_of_work,
)
from mrr.provenance.events import DomainEvent

#: task-packets/E3-T03.yaml invariant: "persisting a family writes exactly
#: one domain event with full NFR-001 provenance, atomically with the
#: revision" — one event type for this entity, dot-separated, matching
#: "source_record.created"/"evidence_anchor.created"'s existing convention.
_EVENT_SOURCE_FAMILY_CREATED = "source_family.created"


def _source_family_to_stored_object(source_family: SourceFamily) -> StoredObject:
    """Convert an already-valid ``SourceFamily`` into the generic
    ``StoredObject`` ``mrr.domain.repositories.ObjectRepository`` persists.
    ``body`` is a plain ``model_dump_json(exclude_none=True)`` round trip —
    no added keys — matching every other service's own
    ``_*_to_stored_object`` helper.
    """
    body: dict[str, Any] = json.loads(source_family.model_dump_json(exclude_none=True))
    return StoredObject(
        id=source_family.id,
        api_version=source_family.api_version,
        kind=source_family.kind,
        practice_id=source_family.practice_id,
        revision=source_family.revision,
        created_at=source_family.created_at,
        created_by=source_family.created_by,
        content_hash=source_family.content_hash,
        supersedes=source_family.supersedes,
        labels=source_family.labels,
        body=body,
    )


class SourceFamilyService:
    """docs/spec/02_DOMAIN_MODEL.md section 2.10, implemented per
    task-packets/E3-T03.yaml. Owns exactly one method, ``create`` — see the
    module docstring's "Additive only: no update/mutate method" section.
    """

    def __init__(self, record: RecordRevisionWithEvent) -> None:
        self._record = record

    def create(
        self,
        source_family: SourceFamily,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """Persist ``source_family`` as revision 1, plus a
        ``source_family.created`` event, atomically.

        ``source_family`` must already be a fully valid ``SourceFamily`` —
        its own ``id``/``content_hash``/``created_at``/``created_by`` are
        minted by the caller (this service does not generate identifiers or
        compute hashes on the caller's behalf, matching
        ``SourceRecordService.create``'s own convention); ``revision`` must
        be ``1``. Neither this method nor anything else on this class ever
        reads or mutates any ``SourceRecord`` referenced by
        ``member_source_ids`` — representation is strictly additive.

        Raises:
            ValueError: ``source_family.revision`` is not ``1``.
        """
        if source_family.revision != 1:
            raise ValueError(
                f"SourceFamily.revision must be 1 for create(), got {source_family.revision!r}"
            )

        obj = _source_family_to_stored_object(source_family)
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=_EVENT_SOURCE_FAMILY_CREATED,
            occurred_at=datetime.now(UTC),
            actor=actor,
            policy_version=policy_version,
            causation_id=None,
            correlation_id=correlation_id,
            object_id=source_family.id,
            object_revision=1,
            payload={
                "relationship_type": source_family.relationship_type,
                "member_source_count": len(source_family.member_source_ids),
            },
        )
        stored, _ = self._record(obj, None, event)
        return stored
