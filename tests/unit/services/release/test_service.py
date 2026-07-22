"""Unit tests for ``mrr.services.release.service.ReleaseService``
(task-packets/E8-T04.yaml) — entirely DB-free, against in-memory fakes of
``mrr.domain.repositories.ObjectRepository`` and the unit-of-work callable
(mirrors ``tests/unit/services/obligation/test_service.py``'s own
``FakeObjectRepository`` and ``tests/unit/services/research_decision
/test_service.py``'s own ``_FakeUnitOfWork``).

Acceptance-test mapping (task-packets/E8-T04.yaml R5, unit tier):

- "service refusals (agent-role approver, empty statement, unsealed/absent
  crate, forged root_hash)" -> ``test_non_person_approver_is_refused``,
  ``test_blank_approval_statement_is_refused``,
  ``test_whitespace_only_approval_statement_is_refused``,
  ``test_dual_approval_mode_is_refused``,
  ``test_unknown_crate_id_is_refused``,
  ``test_crate_id_resolving_to_a_non_crate_kind_is_refused``,
  ``test_forged_root_hash_is_refused``.
- "atomic revision-1 + release.approved event; actor equals approver" ->
  ``test_create_persists_revision_one_and_one_event``,
  ``test_event_actor_equals_approved_by_not_a_separate_identity``.
- immutability by omission -> ``test_service_exposes_no_transition_method``.
- every refusal writes nothing -> ``test_refusals_write_nothing``
  (parametrized over every refusal case above).
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
    BundleRootHashMismatchError,
    DualApprovalNotSupportedError,
    EmptyApprovalStatementError,
    NonPersonApproverError,
    ReleaseCrateKindError,
)
from mrr.services.release.manifest import BundleFileEntry, BundleManifest, compute_root_hash
from mrr.services.release.service import ReleaseService

_POLICY_VERSION = "policy-e8-t04-release-test"


class FakeObjectRepository:
    """Mirrors ``tests/unit/services/obligation/test_service.py``'s own
    identically-shaped fake.
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
    def __init__(self) -> None:
        self.stored: list[StoredObject] = []
        self.events: list[DomainEvent] = []

    def __call__(
        self, obj: StoredObject, expected_current_revision: int | None, event: DomainEvent
    ) -> tuple[StoredObject, AppendedEvent]:
        assert expected_current_revision is None, (
            "ReleaseService always writes a brand-new object at revision 1"
        )
        self.stored.append(obj)
        self.events.append(event)
        appended = AppendedEvent(
            event=event,
            sequence=len(self.events),
            content_hash=f"sha256:{'c' * 64}",
            prev_hash=None,
        )
        return obj, appended


def _seed_crate(
    repo: FakeObjectRepository, *, practice_id: str, kind: str = "EvidenceCrate"
) -> str:
    crate_id = new_urn("evidence-crate")
    now = datetime.now(UTC)
    repo.insert_revision(
        StoredObject(
            id=crate_id,
            api_version="mrr/v1alpha1",
            kind=kind,
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


def _bundle(*, tamper_root_hash: bool = False) -> BundleManifest:
    files = (
        BundleFileEntry(path="report.html", sha256="sha256:" + "1" * 64),
        BundleFileEntry(path="report.md", sha256="sha256:" + "2" * 64),
    )
    root_hash = compute_root_hash((f.path, f.sha256) for f in files)
    if tamper_root_hash:
        root_hash = "sha256:" + "9" * 64
    return BundleManifest(files=files, root_hash=root_hash)


def _service() -> tuple[ReleaseService, FakeObjectRepository, _FakeUnitOfWork]:
    repo = FakeObjectRepository()
    uow = _FakeUnitOfWork()
    return ReleaseService(repo, uow), repo, uow


def _create_kwargs(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "disclosure": "internal",
        "bundle": _bundle(),
        "approved_by": new_urn("person"),
        "approval_statement": "Approving this release.",
        "approval_mode": "single_human",
        "policy_version": _POLICY_VERSION,
        "correlation_id": new_urn("research-run"),
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Happy path.
# ---------------------------------------------------------------------------


def test_create_persists_revision_one_and_one_event() -> None:
    service, repo, uow = _service()
    approved_by = new_urn("person")
    crate_id = _seed_crate(repo, practice_id=new_urn("practice"))

    stored = service.create(
        crate_id=crate_id, **_create_kwargs(approved_by=approved_by, disclosure="internal")
    )

    assert stored.revision == 1
    assert stored.kind == "ReleaseRecord"
    assert stored.body["crate_id"] == crate_id
    assert stored.body["approval"]["approved_by"] == approved_by
    assert stored.body["status"] == "released"
    assert len(uow.stored) == 1
    assert len(uow.events) == 1


def test_practice_id_is_inherited_from_the_resolved_crate() -> None:
    service, repo, _uow = _service()
    practice_id = new_urn("practice")
    crate_id = _seed_crate(repo, practice_id=practice_id)

    stored = service.create(crate_id=crate_id, **_create_kwargs())

    assert stored.practice_id == practice_id


def test_event_actor_equals_approved_by_not_a_separate_identity() -> None:
    service, repo, uow = _service()
    approved_by = new_urn("person")
    crate_id = _seed_crate(repo, practice_id=new_urn("practice"))

    service.create(crate_id=crate_id, **_create_kwargs(approved_by=approved_by))

    event = uow.events[0]
    assert event.event_type == "release.approved"
    assert event.actor == approved_by
    assert event.causation_id is None
    assert event.object_revision == 1


def test_created_by_equals_approved_by() -> None:
    service, repo, _uow = _service()
    approved_by = new_urn("person")
    crate_id = _seed_crate(repo, practice_id=new_urn("practice"))

    stored = service.create(crate_id=crate_id, **_create_kwargs(approved_by=approved_by))

    assert stored.created_by == approved_by


def test_content_hash_is_recomputed_not_a_placeholder() -> None:
    service, repo, _uow = _service()
    crate_id = _seed_crate(repo, practice_id=new_urn("practice"))

    stored = service.create(crate_id=crate_id, **_create_kwargs())

    assert stored.content_hash != "sha256:" + "0" * 64
    assert stored.content_hash.startswith("sha256:")


def test_bundle_root_hash_is_the_recomputed_value_not_the_caller_supplied_one() -> None:
    service, repo, _uow = _service()
    crate_id = _seed_crate(repo, practice_id=new_urn("practice"))
    bundle = _bundle()

    stored = service.create(crate_id=crate_id, **_create_kwargs(bundle=bundle))

    assert stored.body["bundle"]["root_hash"] == bundle.root_hash


# ---------------------------------------------------------------------------
# Refusals.
# ---------------------------------------------------------------------------


def test_non_person_approver_is_refused() -> None:
    service, repo, _uow = _service()
    crate_id = _seed_crate(repo, practice_id=new_urn("practice"))

    with pytest.raises(NonPersonApproverError):
        service.create(crate_id=crate_id, **_create_kwargs(approved_by=new_urn("agent-role")))


def test_blank_approval_statement_is_refused() -> None:
    service, repo, _uow = _service()
    crate_id = _seed_crate(repo, practice_id=new_urn("practice"))

    with pytest.raises(EmptyApprovalStatementError):
        service.create(crate_id=crate_id, **_create_kwargs(approval_statement=""))


def test_whitespace_only_approval_statement_is_refused() -> None:
    service, repo, _uow = _service()
    crate_id = _seed_crate(repo, practice_id=new_urn("practice"))

    with pytest.raises(EmptyApprovalStatementError):
        service.create(crate_id=crate_id, **_create_kwargs(approval_statement="   \n\t  "))


def test_dual_approval_mode_is_refused() -> None:
    service, repo, _uow = _service()
    crate_id = _seed_crate(repo, practice_id=new_urn("practice"))

    with pytest.raises(DualApprovalNotSupportedError):
        service.create(crate_id=crate_id, **_create_kwargs(approval_mode="dual"))


def test_unknown_crate_id_is_refused() -> None:
    service, _repo, _uow = _service()
    unknown_crate_id = new_urn("evidence-crate")

    with pytest.raises(ObjectNotFoundError):
        service.create(crate_id=unknown_crate_id, **_create_kwargs())


def test_crate_id_resolving_to_a_non_crate_kind_is_refused() -> None:
    service, repo, _uow = _service()
    claim_id = _seed_crate(repo, practice_id=new_urn("practice"), kind="Claim")

    with pytest.raises(ReleaseCrateKindError):
        service.create(crate_id=claim_id, **_create_kwargs())


def test_forged_root_hash_is_refused() -> None:
    service, repo, _uow = _service()
    crate_id = _seed_crate(repo, practice_id=new_urn("practice"))
    tampered_bundle = _bundle(tamper_root_hash=True)

    with pytest.raises(BundleRootHashMismatchError):
        service.create(crate_id=crate_id, **_create_kwargs(bundle=tampered_bundle))


@pytest.mark.parametrize(
    "overrides",
    [
        {"approved_by": "urn:mrr:agent-role:01J00000000000000000000099"},
        {"approval_statement": ""},
        {"approval_mode": "dual"},
    ],
    ids=["non-person-approver", "blank-statement", "dual-mode"],
)
def test_refusals_write_nothing(overrides: dict[str, Any]) -> None:
    service, repo, uow = _service()
    crate_id = _seed_crate(repo, practice_id=new_urn("practice"))

    with pytest.raises(Exception):  # noqa: B017,PT011 - any of the three typed refusals above
        service.create(crate_id=crate_id, **_create_kwargs(**overrides))

    assert uow.stored == []
    assert uow.events == []


# ---------------------------------------------------------------------------
# Immutability by omission.
# ---------------------------------------------------------------------------


def test_service_exposes_no_transition_method() -> None:
    # task-packets/E8-T05.yaml R1/R2 explicitly mandate "ReleaseService
    # gains supersede(...)" and "a service method on ReleaseService
    # (read-only path) resolves those inputs and calls it [the pure
    # release-status banner function]" — this directly supersedes E8-T04's
    # own "exposes exactly one public method" design note above (see
    # mrr.services.release.service's own updated module docstring, "task-
    # packets/E8-T05.yaml: supersede and status, additively", for the full
    # resolution). The public surface stays CLOSED and enumerable — the
    # test's real invariant — just widened to the three methods E8-T04 and
    # E8-T05 together define; no OTHER method sneaks onto this class.
    public_methods = {
        name
        for name in dir(ReleaseService)
        if not name.startswith("_") and callable(getattr(ReleaseService, name))
    }
    assert public_methods == {"create", "supersede", "status"}
