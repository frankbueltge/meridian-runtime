"""``ReleaseService`` (task-packets/E8-T04.yaml, docs/spec/adr/ADR-0011-
RELEASE-RECORD-AND-A4-APPROVAL-EVENT.md decision 2): ``create`` persists
revision 1 of a ``ReleaseRecord`` plus exactly one ``release.approved``
domain event, atomically, via the existing E1-T06 one-revision-one-event
unit-of-work primitive — mirroring ``mrr.services.research_decision.service
.ResearchDecisionService``'s/``mrr.services.source_family.service
.SourceFamilyService``'s own "create only, revision 1" shape.
``RELEASE_RECORD_LIFECYCLE``'s own drawn transition (``released ->
superseded``, ``mrr.domain.lifecycles``) is a future E8-T05's to drive, not
this packet's — this class exposes exactly one public method, ``create``, no
update/delete/transition method anywhere: immutability by omission,
mirroring ``SourceFamilyService``'s own identical precedent (ADR-0011
decision 1: "release records are never edited, deleted, or re-released").

--- Deliberate deviation: create() takes RAW approval inputs, not a
    pre-built ReleaseRecord --------------------------------------------------

Unlike ``ResearchDecisionService.create``/``mrr.services.verification
.service.VerificationService.record`` (both of which take an ALREADY-VALID
contract object, minted by the caller — this service's own ``id``/
``content_hash``/``created_at`` are NOT caller-supplied, by contrast),
``create`` here takes the raw components — ``crate_id``, ``disclosure``, an
already-computed :class:`mrr.services.release.manifest.BundleManifest`, and
the RAW ``approved_by``/``approval_statement``/``approval_mode`` strings —
and constructs the ``ReleaseRecord`` itself, only after every check below
passes.

This is a deliberate departure from the sibling precedent, made for one
reason: task-packets/E8-T04.yaml R2 explicitly requires FOUR service-level
"typed errors" (a non-person approver, an empty statement, an unresolved or
wrong-kind crate, and a forged root_hash) — MRR-FR-102's own point is that
"the A4 act cannot be defaulted" by ANY layer, including a caller that might
otherwise already hold an already-schema-valid ``ReleaseRecord`` object and
hand it in unchecked (e.g. one built via ``ReleaseRecord.model_construct``,
which bypasses Pydantic validation entirely, or received from an untrusted
transport). By validating the raw human-supplied strings itself, before any
Pydantic object is even constructed, this service is the one place a caller
CANNOT route around: ``mrr.contracts.release_record.ReleaseRecord``/
``Approval`` still independently pattern-enforce the SAME person-URN shape
at the schema/Pydantic layer too (task-packets/E8-T04.yaml R1's own "pattern
-enforced in the schema AND contract validator") — the checks below are
intentionally REDUNDANT with that layer for exactly this reason, exercised
directly here via ``mrr.domain.identity.URN_PATTERN`` rather than relying on
a ``pydantic.ValidationError`` bubbling up, so this service always raises
its OWN typed, MRR-FR-102-naming error regardless of which layer would also
have caught the same problem. ``mrr.services.cli.release_main`` calls this
service with the CLI's raw, unvalidated ``--approved-by``/
``--approval-statement-file`` contents/``--approval-mode`` strings — see
that module's own docstring.

--- practice_id is inherited from the resolved crate, never a separate arg --

A release is fundamentally scoped to the crate (and therefore the practice)
being released; ``ReleaseRecord.practice_id`` is set to the RESOLVED crate's
own ``practice_id`` (``mrr.domain.repositories.StoredObject.practice_id``)
rather than a sixth caller-supplied kwarg — one less place for a caller to
supply a value that could disagree with the crate actually being released.

--- No separate `actor` parameter: the event's actor IS approved_by --------

Unlike every other one-revision-one-event service in this codebase (which
all take a caller-supplied ``actor: Urn`` kwarg for the domain event), this
service does NOT — ADR-0011 decision 2 is explicit: "Its actor is the
approving person URN... no service, model, or CLI default can stand in for
it." Threading a separate ``actor`` kwarg through would reintroduce exactly
the possibility ADR-0011 forecloses: a service/CLI actor distinct from the
human who actually approved. ``created_by`` on the record itself is set to
the same ``approved_by`` value, for the identical reason — the human's act
IS what created this record.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from mrr.contracts import Approval, Bundle, BundleFile, ReleaseRecord, Urn
from mrr.contracts.release_record import Disclosure
from mrr.domain.hashing_policy import compute_content_hash
from mrr.domain.identity import URN_PATTERN, new_urn
from mrr.domain.repositories import ObjectRepository, StoredObject
from mrr.persistence.unit_of_work import (
    RecordRevisionWithEvent as RecordRevisionWithEvent,
)
from mrr.provenance.events import DomainEvent
from mrr.services.release.errors import (
    BundleRootHashMismatchError,
    DualApprovalNotSupportedError,
    EmptyApprovalStatementError,
    NonPersonApproverError,
    ReleaseCrateKindError,
)
from mrr.services.release.manifest import BundleManifest, compute_root_hash

#: ADR-0011 decision 2: the A4 approval event, written atomically with the
#: ReleaseRecord revision-1 insert.
_EVENT_RELEASE_APPROVED = "release.approved"

#: task-packets/E8-T04.yaml R2a: the ONLY kind --crate-id may resolve to.
_EVIDENCE_CRATE_KIND = "EvidenceCrate"

#: mrr.domain.identity.URN_PATTERN's own named group, matched against the
#: literal 'person' — see the module docstring's "Deliberate deviation"
#: section for why this is checked here, not just at the Pydantic layer.
_PERSON_ENTITY = "person"

#: task-packets/E8-T04.yaml derived_decisions (b): the only approval_mode
#: this practice actually implements.
_SUPPORTED_APPROVAL_MODE = "single_human"

#: A placeholder later overwritten by the real, recomputed content hash
#: (mrr.domain.hashing_policy.compute_content_hash's own `content_hash`/
#: `signature` exclusion means the placeholder's actual value never affects
#: the computed hash) — mirrors this codebase's own established
#: "draft, then recompute" fixture convention (see e.g.
#: tests/integration/services/test_export_cli_ro_crate.py's own
#: `_with_real_content_hash`).
_PLACEHOLDER_CONTENT_HASH = "sha256:" + "0" * 64


def _release_record_to_stored_object(record: ReleaseRecord) -> StoredObject:
    """Convert an already-valid ``ReleaseRecord`` into the generic
    ``StoredObject`` ``mrr.domain.repositories.ObjectRepository`` persists.
    """
    body: dict[str, Any] = json.loads(record.model_dump_json(exclude_none=True))
    return StoredObject(
        id=record.id,
        api_version=record.api_version,
        kind=record.kind,
        practice_id=record.practice_id,
        revision=record.revision,
        created_at=record.created_at,
        created_by=record.created_by,
        content_hash=record.content_hash,
        supersedes=record.supersedes,
        labels=record.labels,
        body=body,
    )


class ReleaseService:
    """docs/spec/adr/ADR-0011-RELEASE-RECORD-AND-A4-APPROVAL-EVENT.md,
    implemented per task-packets/E8-T04.yaml. See the module docstring for
    the full design rationale, above all the four typed refusals and why
    ``create`` takes raw approval inputs rather than a pre-built contract
    object.
    """

    def __init__(
        self, object_repository: ObjectRepository, record: RecordRevisionWithEvent
    ) -> None:
        self._object_repository = object_repository
        self._record = record

    def create(
        self,
        *,
        crate_id: Urn,
        disclosure: Disclosure,
        bundle: BundleManifest,
        approved_by: str,
        approval_statement: str,
        approval_mode: str,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """Persist a brand-new ``ReleaseRecord`` at revision 1, plus a
        ``release.approved`` event whose ``actor`` is ``approved_by``,
        atomically. Checks run cheapest-first, entirely before any database
        write (mirrors this codebase's own MRR-NFR-012 discipline, applied
        here at the service layer rather than only the CLI's): the
        pattern/blank/mode checks need no I/O at all; the crate lookup is
        the one database read; the root_hash recomputation is pure CPU over
        an already-in-memory ``bundle``.

        Args:
            crate_id: the sealed ``EvidenceCrate`` this release is rooted
                on.
            disclosure: ``"internal"`` or ``"public"`` — the report
                projection actually rendered into the bundle.
            bundle: the already-computed manifest of the bundle's own
                content files (``mrr.services.release.manifest
                .compute_bundle_manifest``'s result) — its ``root_hash`` is
                recomputed here, never trusted.
            approved_by: the raw, caller-supplied approver URN string —
                validated here to be a person-segment URN.
            approval_statement: the raw, caller-supplied statement text —
                validated here to be non-blank.
            approval_mode: the raw, caller-supplied mode string — validated
                here to equal ``"single_human"``.
            policy_version: recorded on the ``release.approved`` event.
            correlation_id: recorded on the ``release.approved`` event.

        Raises:
            NonPersonApproverError: ``approved_by`` is not a person-segment
                URN.
            EmptyApprovalStatementError: ``approval_statement`` is blank
                (after stripping whitespace).
            DualApprovalNotSupportedError: ``approval_mode`` is not
                ``"single_human"``.
            mrr.domain.exceptions.ObjectNotFoundError: ``crate_id`` does not
                resolve to any stored object.
            ReleaseCrateKindError: ``crate_id`` resolves to a stored object
                whose ``kind`` is not ``"EvidenceCrate"``.
            BundleRootHashMismatchError: ``bundle.root_hash`` does not equal
                the value recomputed from ``bundle.files``.
        """
        match = URN_PATTERN.match(approved_by)
        if match is None or match.group("entity") != _PERSON_ENTITY:
            raise NonPersonApproverError(approved_by)

        if not approval_statement.strip():
            raise EmptyApprovalStatementError()

        if approval_mode != _SUPPORTED_APPROVAL_MODE:
            raise DualApprovalNotSupportedError(approval_mode)

        crate = self._object_repository.get_latest(crate_id)
        if crate.kind != _EVIDENCE_CRATE_KIND:
            raise ReleaseCrateKindError(crate_id, crate.kind)

        recomputed_root_hash = compute_root_hash((f.path, f.sha256) for f in bundle.files)
        if recomputed_root_hash != bundle.root_hash:
            raise BundleRootHashMismatchError(bundle.root_hash, recomputed_root_hash)

        now = datetime.now(UTC)
        release_id = new_urn("release-record")
        # Literal fields (api_version/kind/status/approval_mode) are passed
        # as literal string expressions, not the module-level `str`
        # constants above (mypy strict would otherwise widen a module-level
        # `_API_VERSION = "mrr/v1alpha1"` to plain `str`, failing
        # ReleaseRecord's own `Literal["mrr/v1alpha1"]` field type) —
        # `approval_mode="single_human"` in particular is deliberately the
        # LITERAL, not the caller's own `approval_mode` variable: by this
        # point `_SUPPORTED_APPROVAL_MODE` has already been checked above,
        # so the only value that can still be flowing through is that one.
        draft = ReleaseRecord(
            id=release_id,
            api_version="mrr/v1alpha1",
            kind="ReleaseRecord",
            practice_id=crate.practice_id,
            revision=1,
            created_at=now,
            created_by=approved_by,
            content_hash=_PLACEHOLDER_CONTENT_HASH,
            crate_id=crate_id,
            disclosure=disclosure,
            bundle=Bundle(
                files=[BundleFile(path=f.path, sha256=f.sha256) for f in bundle.files],
                root_hash=recomputed_root_hash,
            ),
            approval=Approval(
                approved_by=approved_by,
                approval_statement=approval_statement,
                approval_mode="single_human",
            ),
            status="released",
        )
        real_content_hash = compute_content_hash(
            json.loads(draft.model_dump_json(exclude_none=True))
        )
        record = draft.model_copy(update={"content_hash": real_content_hash})

        obj = _release_record_to_stored_object(record)
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=_EVENT_RELEASE_APPROVED,
            occurred_at=now,
            actor=approved_by,
            policy_version=policy_version,
            causation_id=None,
            correlation_id=correlation_id,
            object_id=record.id,
            object_revision=1,
            payload={
                "crate_id": crate_id,
                "disclosure": disclosure,
                "root_hash": recomputed_root_hash,
                "approval_mode": approval_mode,
            },
        )
        stored, _ = self._record(obj, None, event)
        return stored
