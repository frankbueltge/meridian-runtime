"""Unit tests for ``mrr.services.release.service.ReleaseService.supersede``/
``.status`` (task-packets/E8-T05.yaml R1/R2) — entirely DB-free, against
in-memory fakes of ``mrr.domain.repositories.ObjectRepository``, the
unit-of-work callable, and an event log. A NEW file (not an edit of the
E8-T04 ``tests/unit/services/release/test_service.py``, which must pass
unmodified except for its own single, disclosed, widened-public-surface
assertion — see that file's own updated comment) — mirrors its own
``FakeObjectRepository``/fixture-factory shape closely, extended with a
``_FakeEventLog`` and a richer ``_FakeUnitOfWork`` that records every call
(not just asserting ``expected_current_revision is None``, since
``supersede`` writes revision N+1, never revision 1).

Releases are seeded by calling ``ReleaseService.create`` itself (already
fully tested by E8-T04's own suite) and then inserting its result directly
into the SAME fake repository — this fake's own unit-of-work callable does
not write through to the repository (mirroring E8-T04's own established
"assert on what was recorded, not a full round trip" fake shape), so tests
that need a release to already exist in the repository seed it explicitly.
This also guarantees every seeded ``ReleaseRecord`` body is genuinely
schema-valid (created via the real ``create()`` path), which matters here
specifically because ``supersede`` re-validates the WHOLE next-revision body
via ``ReleaseRecord.model_validate`` before persisting.

Acceptance-test mapping (task-packets/E8-T05.yaml R5, unit tier):

- "supersede revision semantics (approval/bundle carried unchanged; only
  status/labels move)" -> ``test_supersede_writes_next_revision_with_
  superseded_status_and_label``, ``test_supersede_carries_approval_and_
  bundle_unchanged``.
- "refusal matrix" -> the tests under "Refusals" below.
- "banner-model verdicts incl. ... the anomaly" (service-level resolution,
  R2's read-only path) -> the tests under "status (read-only)" below.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from mrr.domain.exceptions import ObjectNotFoundError, RevisionConflictError
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.release.errors import (
    AlreadySupersededError,
    NonPersonApproverError,
    ReleaseRecordKindError,
    SelfSupersessionError,
    SupersedingReleaseNotReleasedError,
)
from mrr.services.release.manifest import BundleFileEntry, BundleManifest, compute_root_hash
from mrr.services.release.service import ReleaseService

_POLICY_VERSION = "policy-e8-t05-supersede-test"


class FakeObjectRepository:
    """Byte-for-byte the same shape as ``tests/unit/services/release
    /test_service.py``'s own fake — duplicated here per this codebase's own
    established per-test-module convention (that class is not exported for
    cross-file import anywhere in this codebase, confirmed by inspection).
    """

    def __init__(self) -> None:
        self._revisions: dict[str, list[StoredObject]] = {}

    def insert_revision(
        self, obj: StoredObject, expected_current_revision: int | None
    ) -> StoredObject:
        current = self._revisions.get(obj.id, [])
        current_max = current[-1].revision if current else None
        if current_max != expected_current_revision:
            raise RevisionConflictError(obj.id, expected_current_revision, current_max)
        self._revisions.setdefault(obj.id, []).append(obj)
        return obj

    def get_latest(self, id: str) -> StoredObject:
        revisions = self._revisions.get(id)
        if not revisions:
            raise ObjectNotFoundError(id)
        return revisions[-1]

    def get_revision(self, id: str, revision: int) -> StoredObject:
        for rev in self._revisions.get(id, []):
            if rev.revision == revision:
                return rev
        raise ObjectNotFoundError(id, revision)

    def list_revisions(self, id: str) -> list[StoredObject]:
        return list(self._revisions.get(id, []))


class _FakeUnitOfWork:
    """Records every call — unlike E8-T04's own fake (which asserts
    ``expected_current_revision is None`` unconditionally, since ``create``
    always writes revision 1), ``supersede`` writes revision N+1, so this
    fake records ``expected_current_revision`` for each call instead of
    hardcoding an assumption about its value.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[StoredObject, int | None, DomainEvent]] = []

    def __call__(
        self, obj: StoredObject, expected_current_revision: int | None, event: DomainEvent
    ) -> tuple[StoredObject, AppendedEvent]:
        self.calls.append((obj, expected_current_revision, event))
        appended = AppendedEvent(
            event=event,
            sequence=len(self.calls),
            content_hash=f"sha256:{'c' * 64}",
            prev_hash=None,
        )
        return obj, appended

    @property
    def stored(self) -> list[StoredObject]:
        return [call[0] for call in self.calls]

    @property
    def events(self) -> list[DomainEvent]:
        return [call[2] for call in self.calls]


class _FakeEventLog:
    """The one read operation ``ReleaseService.status`` needs
    (``read_all``) — a plain in-memory list, seeded explicitly per test via
    :meth:`seed`.
    """

    def __init__(self) -> None:
        self._events: list[AppendedEvent] = []

    def seed(self, event: DomainEvent) -> None:
        self._events.append(
            AppendedEvent(
                event=event,
                sequence=len(self._events) + 1,
                content_hash=f"sha256:{'e' * 64}",
                prev_hash=None,
            )
        )

    def read_all(self) -> list[AppendedEvent]:
        return list(self._events)


def _seed_crate(repo: FakeObjectRepository, *, practice_id: str) -> str:
    crate_id = new_urn("evidence-crate")
    now = datetime.now(UTC)
    repo.insert_revision(
        StoredObject(
            id=crate_id,
            api_version="mrr/v1alpha1",
            kind="EvidenceCrate",
            practice_id=practice_id,
            revision=1,
            created_at=now,
            created_by=new_urn("agent"),
            content_hash="sha256:" + "a" * 64,
            supersedes=None,
            labels=None,
            body={"sealed": True},
        ),
        None,
    )
    return crate_id


def _bundle() -> BundleManifest:
    files = (
        BundleFileEntry(path="report.html", sha256="sha256:" + "1" * 64),
        BundleFileEntry(path="report.md", sha256="sha256:" + "2" * 64),
    )
    root_hash = compute_root_hash((f.path, f.sha256) for f in files)
    return BundleManifest(files=files, root_hash=root_hash)


def _service(
    *, with_event_log: bool = False
) -> tuple[ReleaseService, FakeObjectRepository, _FakeUnitOfWork, _FakeEventLog | None]:
    repo = FakeObjectRepository()
    uow = _FakeUnitOfWork()
    event_log = _FakeEventLog() if with_event_log else None
    service = ReleaseService(repo, uow, event_log=event_log)
    return service, repo, uow, event_log


def _create_release(
    service: ReleaseService,
    repo: FakeObjectRepository,
    uow: _FakeUnitOfWork,
    *,
    event_log: _FakeEventLog | None = None,
    approved_by: str | None = None,
    **overrides: Any,
) -> StoredObject:
    """Create a fully valid release via the real ``create()`` path, then
    insert it directly into ``repo`` (this fake's own unit-of-work does not
    write through) — see the module docstring's own rationale.
    """
    crate_id = overrides.pop("crate_id", None) or _seed_crate(repo, practice_id=new_urn("practice"))
    approver = approved_by or new_urn("person")
    kwargs: dict[str, Any] = {
        "crate_id": crate_id,
        "disclosure": "internal",
        "bundle": _bundle(),
        "approved_by": approver,
        "approval_statement": "Approving this release.",
        "approval_mode": "single_human",
        "policy_version": _POLICY_VERSION,
        "correlation_id": new_urn("research-run"),
    }
    kwargs.update(overrides)
    stored = service.create(**kwargs)
    repo.insert_revision(stored, None)
    if event_log is not None:
        event_log.seed(uow.events[-1])
    return stored


# ---------------------------------------------------------------------------
# supersede — happy path / revision semantics.
# ---------------------------------------------------------------------------


def test_supersede_writes_next_revision_with_superseded_status_and_label() -> None:
    service, repo, uow, _ = _service()
    old = _create_release(service, repo, uow)
    new = _create_release(service, repo, uow, crate_id=old.body["crate_id"])
    approver = new_urn("person")

    stored = service.supersede(
        old.id,
        superseded_by=new.id,
        approved_by=approver,
        policy_version=_POLICY_VERSION,
        correlation_id=new_urn("research-run"),
    )

    assert stored.id == old.id
    assert stored.revision == old.revision + 1
    assert stored.body["status"] == "superseded"
    assert stored.body["labels"]["superseded_by"] == new.id
    assert stored.labels is not None
    assert stored.labels["superseded_by"] == new.id
    assert stored.created_by == approver


def test_supersede_carries_approval_and_bundle_unchanged() -> None:
    service, repo, uow, _ = _service()
    old = _create_release(service, repo, uow)
    new = _create_release(service, repo, uow, crate_id=old.body["crate_id"])

    stored = service.supersede(
        old.id,
        superseded_by=new.id,
        approved_by=new_urn("person"),
        policy_version=_POLICY_VERSION,
        correlation_id=new_urn("research-run"),
    )

    assert stored.body["approval"] == old.body["approval"]
    assert stored.body["bundle"] == old.body["bundle"]
    assert stored.body["crate_id"] == old.body["crate_id"]
    assert stored.body["disclosure"] == old.body["disclosure"]


def test_supersede_writes_exactly_one_release_superseded_event() -> None:
    service, repo, uow, _ = _service()
    old = _create_release(service, repo, uow)
    new = _create_release(service, repo, uow, crate_id=old.body["crate_id"])
    approver = new_urn("person")
    before_event_count = len(uow.events)

    service.supersede(
        old.id,
        superseded_by=new.id,
        approved_by=approver,
        policy_version=_POLICY_VERSION,
        correlation_id=new_urn("research-run"),
    )

    new_events = uow.events[before_event_count:]
    assert len(new_events) == 1
    event = new_events[0]
    assert event.event_type == "release.superseded"
    assert event.actor == approver
    assert event.object_id == old.id
    assert event.object_revision == old.revision + 1
    assert event.payload["superseded_by"] == new.id
    assert event.payload["from_status"] == "released"
    assert event.payload["to_status"] == "superseded"


def test_supersede_passes_the_old_latest_revision_as_expected_current_revision() -> None:
    service, repo, uow, _ = _service()
    old = _create_release(service, repo, uow)
    new = _create_release(service, repo, uow, crate_id=old.body["crate_id"])

    service.supersede(
        old.id,
        superseded_by=new.id,
        approved_by=new_urn("person"),
        policy_version=_POLICY_VERSION,
        correlation_id=new_urn("research-run"),
    )

    expected_current_revision = uow.calls[-1][1]
    assert expected_current_revision == old.revision


# ---------------------------------------------------------------------------
# supersede — refusals.
# ---------------------------------------------------------------------------


def test_supersede_refuses_non_person_approver() -> None:
    service, repo, uow, _ = _service()
    old = _create_release(service, repo, uow)
    new = _create_release(service, repo, uow, crate_id=old.body["crate_id"])

    with pytest.raises(NonPersonApproverError):
        service.supersede(
            old.id,
            superseded_by=new.id,
            approved_by=new_urn("agent-role"),
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )


def test_supersede_refuses_self_supersession() -> None:
    service, repo, uow, _ = _service()
    old = _create_release(service, repo, uow)

    with pytest.raises(SelfSupersessionError):
        service.supersede(
            old.id,
            superseded_by=old.id,
            approved_by=new_urn("person"),
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )


def test_supersede_refuses_unknown_release_id() -> None:
    service, repo, uow, _ = _service()
    new = _create_release(service, repo, uow)

    with pytest.raises(ObjectNotFoundError):
        service.supersede(
            new_urn("release-record"),
            superseded_by=new.id,
            approved_by=new_urn("person"),
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )


def test_supersede_refuses_unknown_superseding_release() -> None:
    service, repo, uow, _ = _service()
    old = _create_release(service, repo, uow)

    with pytest.raises(ObjectNotFoundError):
        service.supersede(
            old.id,
            superseded_by=new_urn("release-record"),
            approved_by=new_urn("person"),
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )


def test_supersede_refuses_release_id_of_the_wrong_kind() -> None:
    service, repo, uow, _ = _service()
    crate_id = _seed_crate(repo, practice_id=new_urn("practice"))
    new = _create_release(service, repo, uow)

    with pytest.raises(ReleaseRecordKindError):
        service.supersede(
            crate_id,
            superseded_by=new.id,
            approved_by=new_urn("person"),
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )


def test_supersede_refuses_already_superseded_release() -> None:
    service, repo, uow, _ = _service()
    old = _create_release(service, repo, uow)
    first_new = _create_release(service, repo, uow, crate_id=old.body["crate_id"])
    superseded = service.supersede(
        old.id,
        superseded_by=first_new.id,
        approved_by=new_urn("person"),
        policy_version=_POLICY_VERSION,
        correlation_id=new_urn("research-run"),
    )
    repo.insert_revision(superseded, old.revision)
    second_new = _create_release(service, repo, uow, crate_id=old.body["crate_id"])

    with pytest.raises(AlreadySupersededError):
        service.supersede(
            old.id,
            superseded_by=second_new.id,
            approved_by=new_urn("person"),
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )


def test_supersede_refuses_a_superseding_release_that_is_not_released() -> None:
    service, repo, uow, _ = _service()
    old = _create_release(service, repo, uow)
    middle = _create_release(service, repo, uow, crate_id=old.body["crate_id"])
    newest = _create_release(service, repo, uow, crate_id=old.body["crate_id"])
    # Supersede `middle` with `newest` first, so `middle` is itself already
    # superseded — then attempt to supersede `old` naming `middle`, whose
    # own latest status is now "superseded", not "released".
    superseded_middle = service.supersede(
        middle.id,
        superseded_by=newest.id,
        approved_by=new_urn("person"),
        policy_version=_POLICY_VERSION,
        correlation_id=new_urn("research-run"),
    )
    repo.insert_revision(superseded_middle, middle.revision)

    with pytest.raises(SupersedingReleaseNotReleasedError):
        service.supersede(
            old.id,
            superseded_by=middle.id,
            approved_by=new_urn("person"),
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )


@pytest.mark.parametrize(
    "make_kwargs",
    [
        lambda old_id, new_id: {"superseded_by": old_id, "approved_by": new_urn("person")},
        lambda old_id, new_id: {
            "superseded_by": new_id,
            "approved_by": "urn:mrr:agent-role:01J00000000000000000000099",
        },
    ],
    ids=["self-supersede", "non-person-approver"],
)
def test_supersede_refusals_write_nothing(make_kwargs: Any) -> None:
    service, repo, uow, _ = _service()
    old = _create_release(service, repo, uow)
    new = _create_release(service, repo, uow, crate_id=old.body["crate_id"])
    calls_before = len(uow.calls)

    with pytest.raises(Exception):  # noqa: B017,PT011 - either of the two typed refusals above
        service.supersede(
            old.id,
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
            **make_kwargs(old.id, new.id),
        )

    assert len(uow.calls) == calls_before


# ---------------------------------------------------------------------------
# status (read-only).
# ---------------------------------------------------------------------------


def test_status_requires_event_log_at_construction() -> None:
    service, repo, uow, _ = _service(with_event_log=False)
    old = _create_release(service, repo, uow)

    with pytest.raises(RuntimeError):
        service.status(old.id)


def test_status_reports_current_for_a_freshly_created_release() -> None:
    service, repo, uow, event_log = _service(with_event_log=True)
    stored = _create_release(service, repo, uow, event_log=event_log)

    banner = service.status(stored.id)

    assert banner.verdict == "current"
    assert banner.release_id == stored.id
    assert banner.crate_id == stored.body["crate_id"]
    assert banner.superseded_by is None
    assert banner.affecting_corrections == ()
    assert banner.duplicate_unsuperseded_releases is False


def test_status_reports_superseded_naming_the_superseding_release() -> None:
    service, repo, uow, event_log = _service(with_event_log=True)
    old = _create_release(service, repo, uow, event_log=event_log)
    new = _create_release(service, repo, uow, event_log=event_log, crate_id=old.body["crate_id"])
    superseded = service.supersede(
        old.id,
        superseded_by=new.id,
        approved_by=new_urn("person"),
        policy_version=_POLICY_VERSION,
        correlation_id=new_urn("research-run"),
    )
    repo.insert_revision(superseded, old.revision)

    banner = service.status(old.id)

    assert banner.verdict == "superseded"
    assert banner.superseded_by == new.id

    new_banner = service.status(new.id)
    assert new_banner.verdict == "current"


def test_status_refuses_unknown_release_id() -> None:
    service, _repo, _uow, _event_log = _service(with_event_log=True)

    with pytest.raises(ObjectNotFoundError):
        service.status(new_urn("release-record"))


def test_status_refuses_release_id_of_the_wrong_kind() -> None:
    service, repo, uow, event_log = _service(with_event_log=True)
    crate_id = _seed_crate(repo, practice_id=new_urn("practice"))

    with pytest.raises(ReleaseRecordKindError):
        service.status(crate_id)


def test_status_flags_duplicate_unsuperseded_releases_for_the_same_crate() -> None:
    service, repo, uow, event_log = _service(with_event_log=True)
    first = _create_release(service, repo, uow, event_log=event_log)
    second = _create_release(
        service, repo, uow, event_log=event_log, crate_id=first.body["crate_id"]
    )

    first_banner = service.status(first.id)
    second_banner = service.status(second.id)

    assert first_banner.duplicate_unsuperseded_releases is True
    assert second_banner.duplicate_unsuperseded_releases is True
    # Both are still "current" — the anomaly rides alongside, never instead.
    assert first_banner.verdict == "current"
    assert second_banner.verdict == "current"


def test_status_does_not_flag_a_single_release_for_its_own_crate() -> None:
    service, repo, uow, event_log = _service(with_event_log=True)
    stored = _create_release(service, repo, uow, event_log=event_log)

    banner = service.status(stored.id)

    assert banner.duplicate_unsuperseded_releases is False


def test_status_does_not_flag_when_one_of_two_is_already_superseded() -> None:
    service, repo, uow, event_log = _service(with_event_log=True)
    old = _create_release(service, repo, uow, event_log=event_log)
    new = _create_release(service, repo, uow, event_log=event_log, crate_id=old.body["crate_id"])
    superseded = service.supersede(
        old.id,
        superseded_by=new.id,
        approved_by=new_urn("person"),
        policy_version=_POLICY_VERSION,
        correlation_id=new_urn("research-run"),
    )
    repo.insert_revision(superseded, old.revision)

    banner = service.status(new.id)
    assert banner.duplicate_unsuperseded_releases is False


# ---------------------------------------------------------------------------
# Public surface (mirrors E8-T04's own "immutability by omission" check).
# ---------------------------------------------------------------------------


def test_supersede_and_status_are_the_only_new_public_methods() -> None:
    public_methods = {
        name
        for name in dir(ReleaseService)
        if not name.startswith("_") and callable(getattr(ReleaseService, name))
    }
    assert public_methods == {"create", "supersede", "status"}
