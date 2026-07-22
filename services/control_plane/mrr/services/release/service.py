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

--- task-packets/E8-T05.yaml: ``supersede`` and ``status``, additively -------

E8-T04's own module docstring (above, unedited) says this class "exposes
exactly one public method, ``create``" — true when E8-T04 shipped, and now
superseded BY THIS TASK'S OWN explicit design mandate: reviewer_resolution
(1)/R1 says, verbatim, "ReleaseService gains supersede(...)", and R2 says "A
service method on ReleaseService (read-only path) resolves those inputs and
calls it [the pure ``mrr.domain.release_status.compute_release_banner``]." A
class that closes its own public surface by omission (E8-T04's own stated
design) and a task packet that explicitly reopens it are two facts that
cannot both hold; this docstring resolves the conflict in the task packet's
favor (it is E8-T05's own binding text) and flags it for reviewer scrutiny
here, in this task's own delivery report, and — the one place a REGRESSION
would otherwise silently hide — in ``tests/unit/services/release
/test_service.py``'s own ``test_service_exposes_no_transition_method``,
whose assertion is widened from ``{"create"}`` to
``{"create", "supersede", "status"}`` (one line changed, comment added
explaining why; every OTHER assertion in that E8-T04 file is untouched).
Immutability is now: "release BYTES (the approval block, the bundle
manifest) are never rewritten" — proven by :meth:`supersede` carrying both
forward UNCHANGED into the next revision — not "this class has one method."

:meth:`supersede` writes the release record's OWN next revision (same id,
``status`` -> ``"superseded"``, ``labels["superseded_by"]`` set) plus one
``release.superseded`` event, atomically — mirrors ``mrr.services.claim
.service.ClaimService._transition``'s own "load latest, assert the
lifecycle edge via ``mrr.domain.lifecycles``, mint the next revision,
record atomically" shape, applied here to ``mrr.domain.lifecycles
.RELEASE_RECORD_LIFECYCLE``'s own single ``released -> superseded`` edge.
Unlike ``create``, this method DOES take a separate ``policy_version``/
``correlation_id`` pair for its own event — R1's own signature sketch
(``supersede(release_id, superseded_by, approved_by)``) omits them exactly
as R4's own flag list omitted ``--policy-version``/``--correlation-id`` for
``create`` (E8-T04's own disclosed addition, cited here as direct
precedent): every OTHER event-writing write in this codebase requires an
explicit ``policy_version`` (no default — recording a governance act states
the policy version every time) and accepts a ``correlation_id``, and a
``release.superseded`` event is exactly such a write.

:meth:`status` is the READ-ONLY companion (R2): resolves a ``ReleaseRecord``
by id, discovers every ``CorrectionEvent`` this practice has ever recorded
(mirroring ``mrr.services.projection.service.ProjectionService``'s own
"scan the event log for genesis events" discovery pattern — task-packets/
E3-T07.yaml — restated here as a small, disclosed, minimal duplication
rather than composed via ``ProjectionService`` itself, which would need an
``EdgeRepository`` this service has no other use for, and whose own
``_read_correction_bodies`` is module-private), scans for sibling
``ReleaseRecord``s sharing this one's own ``crate_id`` (the
``duplicate_unsuperseded_releases`` anomaly, reviewer_resolution (2)'s own
detection duty), and calls the pure ``mrr.domain.release_status
.compute_release_banner`` with the results. Never calls ``self._record``
anywhere — a pure read, like every sibling "read-only path" method this
codebase already has (``ProjectionService``'s own two builders,
``mrr.services.release.verify``'s two functions). Needs an event log the
ORIGINAL ``create``-only constructor never required — see ``event_log``'s
own new, OPTIONAL constructor parameter below: adding it as a REQUIRED
positional/keyword argument would break ``mrr.services.release.bundle
.assemble_and_release``'s own existing ``ReleaseService(object_repository,
record)`` call site AND ``tests/unit/services/release/test_service.py``'s
own identical two-argument construction (an E8-T04 test file this task must
not edit) — ``event_log: _EventJournal | None = None`` keeps both
call sites byte-identical, valid, and untouched; only NEW callers that need
:meth:`status` (``mrr.services.release.supersede.resolve_release_status``,
and this task's own new unit tests) supply it.

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
from typing import Any, Protocol

from mrr.contracts import Approval, Bundle, BundleFile, ReleaseRecord, Urn
from mrr.contracts.release_record import Disclosure
from mrr.domain.exceptions import ObjectNotFoundError
from mrr.domain.hashing_policy import compute_content_hash
from mrr.domain.identity import URN_PATTERN, new_urn
from mrr.domain.lifecycles import RELEASE_RECORD_LIFECYCLE
from mrr.domain.release_status import ReleaseBanner, compute_release_banner
from mrr.domain.repositories import ObjectRepository, StoredObject
from mrr.persistence.unit_of_work import (
    RecordRevisionWithEvent as RecordRevisionWithEvent,
)
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.release.errors import (
    AlreadySupersededError,
    BundleRootHashMismatchError,
    DualApprovalNotSupportedError,
    EmptyApprovalStatementError,
    NonPersonApproverError,
    ReleaseCrateKindError,
    ReleaseRecordKindError,
    SelfSupersessionError,
    SupersedingReleaseNotReleasedError,
)
from mrr.services.release.manifest import BundleManifest, compute_root_hash

#: ADR-0011 decision 2: the A4 approval event, written atomically with the
#: ReleaseRecord revision-1 insert.
_EVENT_RELEASE_APPROVED = "release.approved"

#: task-packets/E8-T05.yaml R1: the ``release.superseded`` event ``supersede``
#: writes atomically with the record's next revision.
_EVENT_RELEASE_SUPERSEDED = "release.superseded"

#: task-packets/E8-T04.yaml R2a: the ONLY kind --crate-id may resolve to.
_EVIDENCE_CRATE_KIND = "EvidenceCrate"

#: The one kind ``--release-id``/``--supersedes`` may resolve to (R1/R2).
_RELEASE_RECORD_KIND = "ReleaseRecord"

#: mrr.domain.lifecycles.RELEASE_RECORD_LIFECYCLE's two states.
_RELEASED_STATUS = "released"
_SUPERSEDED_STATUS = "superseded"

#: derived_decisions (a): the open ``labels`` string-map slot ``supersede``
#: carries the superseding release's own urn in.
_SUPERSEDED_BY_LABEL = "superseded_by"

#: mrr.domain.identity.URN_PATTERN's own named group, matched against the
#: literal 'person' — see the module docstring's "Deliberate deviation"
#: section for why this is checked here, not just at the Pydantic layer.
_PERSON_ENTITY = "person"

#: task-packets/E8-T04.yaml derived_decisions (b): the only approval_mode
#: this practice actually implements.
_SUPPORTED_APPROVAL_MODE = "single_human"

#: task-packets/E3-T07.yaml's own genesis-event discovery convention,
#: applied here to ReleaseRecord/CorrectionEvent — see :meth:`ReleaseService
#: .status`'s own docstring, "the READ-ONLY companion", for the full
#: rationale for why this is a small, disclosed, local duplication of
#: ``mrr.services.projection.service.ProjectionService``'s own identical
#: pattern rather than a composed dependency on that class.
_CORRECTION_RECORDED_EVENT = "correction.recorded"
_CORRECTION_KIND = "CorrectionEvent"

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


class _EventJournal(Protocol):
    """The one read operation :meth:`ReleaseService.status` needs from an
    event log — identical in spirit to every other service module's own
    independently-declared, narrower-than-``mrr.provenance.log.EventLog[TTx]``
    Protocol (see e.g. ``mrr.services.projection.service._EventJournal``'s
    own docstring for why this codebase declares this Protocol independently
    per consuming module rather than sharing one).
    """

    def read_all(self) -> list[AppendedEvent]: ...


class ReleaseService:
    """docs/spec/adr/ADR-0011-RELEASE-RECORD-AND-A4-APPROVAL-EVENT.md,
    implemented per task-packets/E8-T04.yaml/E8-T05.yaml. See the module
    docstring for the full design rationale, above all the four ``create``
    typed refusals, why ``create`` takes raw approval inputs rather than a
    pre-built contract object, and — E8-T05's own addition — the
    ``supersede``/``status`` rationale under "task-packets/E8-T05.yaml:
    supersede and status, additively".
    """

    def __init__(
        self,
        object_repository: ObjectRepository,
        record: RecordRevisionWithEvent,
        *,
        event_log: _EventJournal | None = None,
    ) -> None:
        self._object_repository = object_repository
        self._record = record
        self._event_log = event_log

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

    # ------------------------------------------------------------------
    # task-packets/E8-T05.yaml R1 — supersede.
    # ------------------------------------------------------------------

    def supersede(
        self,
        release_id: Urn,
        *,
        superseded_by: Urn,
        approved_by: str,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """Write the NEXT revision of ``release_id`` with ``status``
        ``"superseded"`` and ``labels["superseded_by"] == superseded_by``,
        atomically with one ``release.superseded`` event whose ``actor`` is
        ``approved_by`` — R1, verbatim. ``approval``/``bundle`` are carried
        into the new revision UNCHANGED (only ``status``/``labels`` move; see
        the module docstring's "supersede and status, additively" section).

        Checks run cheapest-first, entirely before any database write
        (mirrors ``create``'s own discipline): the person-URN pattern check
        and the self-supersession check need no I/O at all; resolving
        ``release_id`` is the first database read; resolving
        ``superseded_by`` is the second.

        Args:
            release_id: the OLD ``ReleaseRecord`` being superseded.
            superseded_by: the urn of the NEW ``ReleaseRecord`` that
                supersedes it — must already resolve to a ``ReleaseRecord``
                with ``status == "released"``.
            approved_by: the raw, caller-supplied approver URN string for
                the superseding release — validated here to be a
                person-segment URN, exactly as ``create`` validates its own
                ``approved_by`` (ADR-0011 decision 2: no service/CLI default
                can stand in for the human act; see this method's own
                docstring for why this is the SAME person who approved
                ``superseded_by``, not a separate identity).
            policy_version: recorded on the ``release.superseded`` event.
            correlation_id: recorded on the ``release.superseded`` event.

        Raises:
            mrr.services.release.errors.NonPersonApproverError: ``approved_by``
                is not a person-segment URN.
            mrr.services.release.errors.SelfSupersessionError:
                ``superseded_by == release_id``.
            mrr.domain.exceptions.ObjectNotFoundError: ``release_id`` or
                ``superseded_by`` does not resolve to any stored object.
            mrr.services.release.errors.ReleaseRecordKindError:
                ``release_id`` or ``superseded_by`` resolves to a stored
                object whose ``kind`` is not ``"ReleaseRecord"``.
            mrr.services.release.errors.AlreadySupersededError:
                ``release_id``'s latest revision already has
                ``status == "superseded"``.
            mrr.services.release.errors.SupersedingReleaseNotReleasedError:
                ``superseded_by`` resolves to a ``ReleaseRecord`` whose
                latest revision's ``status`` is not ``"released"``.
        """
        match = URN_PATTERN.match(approved_by)
        if match is None or match.group("entity") != _PERSON_ENTITY:
            raise NonPersonApproverError(approved_by)

        if superseded_by == release_id:
            raise SelfSupersessionError(release_id)

        latest = self._object_repository.get_latest(release_id)
        if latest.kind != _RELEASE_RECORD_KIND:
            raise ReleaseRecordKindError(release_id, latest.kind)
        if latest.body["status"] == _SUPERSEDED_STATUS:
            raise AlreadySupersededError(release_id)

        superseding = self._object_repository.get_latest(superseded_by)
        if superseding.kind != _RELEASE_RECORD_KIND:
            raise ReleaseRecordKindError(superseded_by, superseding.kind)
        if superseding.body["status"] != _RELEASED_STATUS:
            raise SupersedingReleaseNotReleasedError(superseded_by, str(superseding.body["status"]))

        # RELEASE_RECORD_LIFECYCLE's own single drawn edge — the authoritative
        # source for "released -> superseded is legal", rather than trusting
        # the two checks above to have already proven it by elimination
        # (they do, since ReleaseStatus is a closed two-value enum, but
        # driving the transition through the state machine itself is what
        # ADR-0011 decision 1's own "E8-T05 drives it" text calls for).
        RELEASE_RECORD_LIFECYCLE.assert_transition(str(latest.body["status"]), _SUPERSEDED_STATUS)

        now = datetime.now(UTC)
        new_revision = latest.revision + 1
        new_labels = dict(latest.labels or {})
        new_labels[_SUPERSEDED_BY_LABEL] = superseded_by

        new_body = dict(latest.body)
        new_body["status"] = _SUPERSEDED_STATUS
        new_body["revision"] = new_revision
        new_body["created_at"] = now.isoformat()
        new_body["created_by"] = approved_by
        new_body["labels"] = new_labels
        # approval/bundle are NOT touched — they remain the same dict values
        # already present on `latest.body` (R1: "carried into the new
        # revision UNCHANGED").
        new_content_hash = compute_content_hash(new_body)
        new_body["content_hash"] = new_content_hash

        # Re-run the ReleaseRecord contract's own validator against the
        # EXACT revision body about to be persisted — mirrors ClaimService
        # ._transition's identical "re-validate the body, not just trust the
        # field-by-field edit above" discipline for its own supported
        # transition.
        ReleaseRecord.model_validate(new_body)

        obj = StoredObject(
            id=latest.id,
            api_version=latest.api_version,
            kind=latest.kind,
            practice_id=latest.practice_id,
            revision=new_revision,
            created_at=now,
            created_by=approved_by,
            content_hash=new_content_hash,
            supersedes=latest.supersedes,
            labels=new_labels,
            body=new_body,
        )
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=_EVENT_RELEASE_SUPERSEDED,
            occurred_at=now,
            actor=approved_by,
            policy_version=policy_version,
            causation_id=None,
            correlation_id=correlation_id,
            object_id=latest.id,
            object_revision=new_revision,
            payload={
                "crate_id": latest.body["crate_id"],
                "superseded_by": superseded_by,
                "from_status": latest.body["status"],
                "to_status": _SUPERSEDED_STATUS,
            },
        )
        stored, _ = self._record(obj, latest.revision, event)
        return stored

    # ------------------------------------------------------------------
    # task-packets/E8-T05.yaml R2 — status (read-only).
    # ------------------------------------------------------------------

    def status(self, release_id: Urn) -> ReleaseBanner:
        """Resolve every input ``mrr.domain.release_status
        .compute_release_banner`` needs for ``release_id`` and call it — see
        the module docstring's "the READ-ONLY companion" section. Never
        writes anything (no ``self._record`` call anywhere in this method).

        Raises:
            mrr.domain.exceptions.ObjectNotFoundError: ``release_id`` does
                not resolve to any stored object.
            mrr.services.release.errors.ReleaseRecordKindError:
                ``release_id`` resolves to a stored object whose ``kind`` is
                not ``"ReleaseRecord"``.
            RuntimeError: this ``ReleaseService`` was constructed without an
                ``event_log`` — a wiring error (every production/test caller
                of :meth:`status` supplies one), not a user-facing refusal.
        """
        if self._event_log is None:
            raise RuntimeError(
                "ReleaseService.status requires event_log to be supplied at construction "
                "(the read-only status path needs it to discover corrections and sibling "
                "releases sharing this release's own crate_id) — this is a wiring error, "
                "not a user-facing refusal"
            )

        stored = self._object_repository.get_latest(release_id)
        if stored.kind != _RELEASE_RECORD_KIND:
            raise ReleaseRecordKindError(release_id, stored.kind)

        correction_bodies = self._read_correction_bodies()
        duplicate = self._has_duplicate_unsuperseded_releases(crate_id=str(stored.body["crate_id"]))

        return compute_release_banner(
            release_body=stored.body,
            correction_bodies=correction_bodies,
            duplicate_unsuperseded_releases=duplicate,
        )

    # ------------------------------------------------------------------
    # Internal helpers for :meth:`status`.
    # ------------------------------------------------------------------

    def _discover_ids_by_event_type(self, event_type: str) -> set[str]:
        if self._event_log is None:  # guarded by status()'s own check; plain
            # if/raise (not a bare assert) so the guard survives Python's
            # optimized (-O) bytecode mode — mrr.domain.ro_crate's own
            # documented convention, and bandit B101 (CI security-check).
            raise ValueError(
                "_discover_ids_by_event_type requires an event log; status() refuses "
                "before reaching this helper when none was supplied"
            )
        return {
            appended.event.object_id
            for appended in self._event_log.read_all()
            if appended.event.event_type == event_type
        }

    def _read_correction_bodies(self) -> list[dict[str, Any]]:
        """Every ``CorrectionEvent`` this practice has ever recorded,
        discovered by scanning the event log for ``correction.recorded``
        genesis events — see the module docstring's "the READ-ONLY
        companion" section for why this small pattern is restated here
        rather than composed via ``mrr.services.projection.service
        .ProjectionService``.
        """
        correction_ids = self._discover_ids_by_event_type(_CORRECTION_RECORDED_EVENT)
        bodies: list[dict[str, Any]] = []
        for correction_id in sorted(correction_ids):
            try:
                obj = self._object_repository.get_latest(correction_id)
            except ObjectNotFoundError:
                continue
            if obj.kind == _CORRECTION_KIND:
                bodies.append(obj.body)
        return bodies

    def _has_duplicate_unsuperseded_releases(self, *, crate_id: str) -> bool:
        """``True`` iff more than one ``ReleaseRecord`` sharing ``crate_id``
        currently has ``status != "superseded"`` — reviewer_resolution (2)'s
        own detection duty for the ONE known
        ``mrr.services.release.supersede.create_and_supersede`` intermediate
        state (a newly-created release left unlinked when transitioning the
        old one failed).
        """
        release_ids = self._discover_ids_by_event_type(_EVENT_RELEASE_APPROVED)
        unsuperseded_count = 0
        for candidate_id in release_ids:
            try:
                candidate = self._object_repository.get_latest(candidate_id)
            except ObjectNotFoundError:
                continue
            if candidate.kind != _RELEASE_RECORD_KIND:
                continue
            if str(candidate.body.get("crate_id")) != crate_id:
                continue
            if str(candidate.body.get("status")) != _SUPERSEDED_STATUS:
                unsuperseded_count += 1
        return unsuperseded_count > 1
