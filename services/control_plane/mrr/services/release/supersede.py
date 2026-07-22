"""``mrr release supersede`` / ``mrr release status``'s own CLI-facing
composition functions (task-packets/E8-T05.yaml R1/R2/R4): the two-
transaction orchestration ``create_and_supersede`` and the read-only wrapper
``resolve_release_status``, mirroring ``mrr.services.release.bundle``'s own
"the CLI never touches ``mrr.services.release.service.ReleaseService``
directly" law (``tests/unit/architecture/test_release_cli_boundary.py``, an
E8-T04 test file that forbids ``mrr.services.cli.release_main`` from
importing ``mrr.services.release.service`` at all — left byte-for-byte
UNMODIFIED by this task; every one of this module's own two functions gives
``release_main.py`` exactly what it needs without ever naming
``ReleaseService`` itself).

--- ``create_and_supersede``: the two-transaction fixed order --------------

Reviewer_resolution (2): "creates the new release first, then transitions
the old one naming the new id — two unit-of-work transactions in a fixed
order (the ``VerificationService`` record-then-transition precedent)" — see
``mrr.services.verification.service.VerificationService.record``'s own
module docstring, "The failed-verification-to-claim-status policy", for the
identical shape this mirrors: step 1 (an already-independently-atomic write)
runs to completion, THEN step 2 (a separate, independently-atomic write)
runs as a SEQUENTIAL follow-up, never one combined transaction spanning
both.

Step 1 is ``mrr.services.release.bundle.assemble_and_release`` — completely
UNCHANGED, the exact same function ``mrr release create`` already calls,
producing the NEW release (the full create flag set R4 names). Step 2 is
``ReleaseService.supersede`` on the OLD release, naming the NEW release's own
freshly-minted id as ``superseded_by``. If step 1 fails, nothing is
persisted anywhere (unchanged from ``mrr release create``'s own existing
guarantee) and this function's own exception propagates unchanged. If step 1
succeeds and step 2 fails, THE ONE NAMED INCONSISTENT STATE
(reviewer_resolution (2)) exists: a fully valid, independently-approved NEW
release with no corresponding supersession of the old one.
``mrr.services.release.errors.SupersessionIntermediateStateError`` — raised
here, wrapping whatever step 2 raised as its own ``cause`` — carries both
release ids so ``mrr.services.cli.release_main`` can print the exact state
verbatim (its own message already does — see that error's own docstring).

No SECOND inconsistent state exists (stop_condition (2)'s own check,
resolved here rather than discovered mid-implementation): step 1 is a single
already-proven-atomic unit (task-packets/E8-T04.yaml's own "Atomicity
analysis" — assemble-content -> persist -> finalize, its own ONE named
state already handled by ``assemble_and_release`` itself, which this
function does not re-wrap since that error already names ITS OWN exact
state distinctly); step 2 (``ReleaseService.supersede``) is a single atomic
database transaction by construction (one revision write plus one event,
via the same ``record_object_revision_with_event`` primitive ``create``
itself uses). There is no third step and no shared mutable state between the
two.

--- ``resolve_release_status``: the read-only wrapper -----------------------

A thin composition, mirroring ``mrr.services.release.verify
.resolve_release_record``'s own "just enough wiring, zero new domain logic"
shape: constructs a ``ReleaseService`` bound to a NEVER-INVOKED ``record``
callable (this path never writes — mirrors ``mrr.services.report.service
._NeverInvokedArtifactStore``'s own identical "construct the dependency this
constructor requires, but this call path never actually reaches it"
precedent) and calls its ``status`` method. Exists so
``mrr.services.cli.release_main`` never imports ``mrr.services.release
.service``/``mrr.domain.release_status`` directly either.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from mrr.domain.artifacts import ArtifactStore, Classification
from mrr.domain.release_status import ReleaseBanner
from mrr.domain.repositories import EdgeRepository, ObjectRepository, StoredObject
from mrr.domain.research_report import Disclosure
from mrr.persistence.unit_of_work import RecordRevisionWithEvent
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.release.bundle import ReleaseBundleResult, assemble_and_release
from mrr.services.release.errors import SupersessionIntermediateStateError
from mrr.services.release.service import ReleaseService

__all__ = ["create_and_supersede", "resolve_release_status"]


class _EventJournal(Protocol):
    """Identical in spirit to ``mrr.services.release.bundle._EventJournal``/
    ``mrr.services.release.service._EventJournal`` — declared independently
    per this codebase's own established per-module Protocol convention.
    """

    def read_all(self) -> list[AppendedEvent]: ...


def _never_invoked_record(
    obj: StoredObject, expected_current_revision: int | None, event: DomainEvent
) -> tuple[StoredObject, AppendedEvent]:
    """A ``RecordRevisionWithEvent`` stand-in that always raises —
    ``resolve_release_status`` only ever calls ``ReleaseService.status``,
    which never writes, so this should never actually be invoked. Mirrors
    ``mrr.services.report.service._NeverInvokedArtifactStore``'s identical
    "satisfy a required constructor parameter for a read-only call path"
    precedent.
    """
    raise AssertionError(
        "resolve_release_status's internal ReleaseService never writes "
        "(status() is read-only) — this stand-in should never actually be invoked"
    )


def create_and_supersede(
    *,
    object_repository: ObjectRepository,
    edge_repository: EdgeRepository,
    event_log: _EventJournal,
    artifact_store: ArtifactStore,
    record: RecordRevisionWithEvent,
    crate_id: str,
    disclosure: Disclosure,
    classification_by_object_id: Mapping[str, Classification] | None,
    approved_by: str,
    approval_statement: str,
    approval_mode: str,
    policy_version: str,
    correlation_id: str,
    output_dir: Path,
    supersedes: str,
) -> tuple[ReleaseBundleResult, StoredObject]:
    """Run the two-transaction fixed order described in the module
    docstring: (1) ``assemble_and_release`` for the NEW release — the exact
    same full create flag set ``mrr release create`` itself uses; (2)
    ``ReleaseService.supersede`` on ``supersedes`` (the OLD release), naming
    the new release's own freshly-minted id.

    Raises:
        Whatever ``assemble_and_release`` itself raises (step 1) — nothing
            was persisted, exactly as ``mrr release create``'s own identical
            call already guarantees.
        mrr.services.release.errors.SupersessionIntermediateStateError: step
            1 succeeded but step 2 failed — see the module docstring's own
            "the ONE named inconsistent state" section. The new release
            (``result.release_id`` inside the wrapped error) is durably
            persisted; ``supersedes`` was NOT transitioned.
    """
    result = assemble_and_release(
        object_repository=object_repository,
        edge_repository=edge_repository,
        event_log=event_log,
        artifact_store=artifact_store,
        record=record,
        crate_id=crate_id,
        disclosure=disclosure,
        classification_by_object_id=classification_by_object_id,
        approved_by=approved_by,
        approval_statement=approval_statement,
        approval_mode=approval_mode,
        policy_version=policy_version,
        correlation_id=correlation_id,
        output_dir=output_dir,
    )

    release_service = ReleaseService(object_repository, record)
    try:
        old_stored = release_service.supersede(
            supersedes,
            superseded_by=result.release_id,
            approved_by=approved_by,
            policy_version=policy_version,
            correlation_id=correlation_id,
        )
    except BaseException as exc:
        raise SupersessionIntermediateStateError(
            new_release_id=result.release_id, old_release_id=supersedes, cause=exc
        ) from exc

    return result, old_stored


def resolve_release_status(
    object_repository: ObjectRepository, event_log: _EventJournal, release_id: str
) -> ReleaseBanner:
    """See the module docstring's "the read-only wrapper" section.

    Raises:
        mrr.domain.exceptions.ObjectNotFoundError: ``release_id`` does not
            resolve to any stored object.
        mrr.services.release.errors.ReleaseRecordKindError: ``release_id``
            resolves to a stored object whose ``kind`` is not
            ``"ReleaseRecord"``.
    """
    release_service = ReleaseService(object_repository, _never_invoked_record, event_log=event_log)
    return release_service.status(release_id)
