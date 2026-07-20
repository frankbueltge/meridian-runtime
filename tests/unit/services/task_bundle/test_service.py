"""Unit tests for ``mrr.services.task_bundle.service.TaskBundleService`` and
``NodeTaskDecisionService`` (task-packets/E2-T03.yaml, reworked per ADR-0007
— docs/spec/adr/ADR-0007-TASK-BUNDLE-TRANSITIONS-ARE-EVENTS.md), run entirely
DB-free against in-memory fakes of ``mrr.domain.repositories.ObjectRepository``
and the event-log read/append surface — no PostgreSQL, no
``sqlalchemy.Engine``. Same "lightweight fake unit-of-work" pattern
``tests/unit/services/research_score/test_service.py`` and
``tests/unit/services/capability_registry/test_service.py`` use.

One shared fake object store/event log backs ``ResearchScoreService``,
``CapabilityRegistry``, ``TaskBundleService``, and
``NodeTaskDecisionService`` in every test — mirroring how one real
PostgreSQL ``objects``/``domain_events`` pair backs all of them in
production. An approved ``ResearchScore`` is seeded directly into the fake
repository, and a ``NodeManifest`` declaring the bundle's capability is
registered for real through ``CapabilityRegistry.register`` with a real
Ed25519 keypair — genuine reuse of E2-T01/T02.

ADR-0007 in one sentence, for orientation while reading these tests: a
lifecycle transition (``offer``/``accept``/``defer``/``reject``) is now an
append-only domain event, NOT a new content revision — the content record
(``ObjectRepository.get_latest``) stays at revision 1 for the whole
CREATED -> OFFERED -> ACCEPTED/DEFERRED/REJECTED path, and the origin's
signature — produced exactly once, at ``create`` — always verifies directly
against it, with no historical-revision scan. Only ``propose_modification``
(a genuine content change) advances the revision and re-signs. The
authoritative CURRENT lifecycle status therefore diverges from
``content.body["status"]`` (that field is a creation-time snapshot) and is
instead read off the event log — every transition method below returns a
``TaskBundleTransition`` (``content``, ``status``, ``appended_event``)
rather than a bare ``StoredObject``.

Acceptance-test mapping (task-packets/E2-T03.yaml, ADR-0007 rework):

- "create against an unapproved score fails closed" ->
  ``test_create_fails_closed_on_unapproved_score``.
- "create for a capability the target node does not declare fails closed" ->
  ``test_create_fails_closed_on_undeclared_capability``.
- "offer then node-accept moves CREATED->OFFERED->ACCEPTED and persists
  events; the origin has no path to accept on the node's behalf; the content
  record's revision never advances" ->
  ``test_offer_then_node_accept_moves_through_full_lifecycle``,
  ``test_origin_service_has_no_accept_method``.
- "a non-target identity calling accept/reject fails closed, nothing
  persisted" ->
  ``test_non_target_identity_calling_accept_fails_closed_and_persists_nothing``,
  ``test_non_target_identity_calling_reject_fails_closed_and_persists_nothing``.
- "node reject records a refusal event with a reason category" ->
  ``test_reject_records_reason_category_event``.
- "node propose_modification creates a new signed revision (new hash and
  signature) back in OFFERED; the prior revision is intact" ->
  ``test_propose_modification_creates_new_signed_revision_prior_intact``.
- CRITICAL (ADR-0007's reason to exist): "the origin signature still
  verifies directly against the current content record after an arbitrary
  sequence of lifecycle transitions" ->
  ``test_signature_still_verifies_after_offer_and_accept``,
  ``test_signature_still_verifies_after_offer_and_defer_or_reject``.
- CRITICAL: "a bundle whose stored content no longer matches its signature
  is refused on the node side, before any decision" ->
  ``test_content_tampered_after_signing_is_rejected_on_node_side``.
- "the current lifecycle status is correctly event-derived after a sequence
  of transitions" -> ``test_current_status_is_event_derived``,
  ``test_content_snapshot_status_diverges_from_live_status_after_transition``.
- "illegal lifecycle transitions persist nothing" (unit-level; the packet's
  own integration-tier duplicate covers real PostgreSQL) ->
  ``test_accept_on_a_not_yet_offered_bundle_raises_and_persists_nothing``.
- the round-trip invariant ADR-0005 exists to restore (a stray body key
  used to make ``TaskBundle.model_validate(stored.body)`` fail) ->
  ``test_created_body_round_trips_through_task_bundle_model_validate``.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.contracts import NodeManifest, ResearchScore, TaskBundle
from mrr.crypto.exceptions import SignatureVerificationError
from mrr.domain.exceptions import (
    CapabilityNotDeclaredError,
    InvalidTransitionError,
    NodeAuthorityError,
    NodeManifestNotFoundError,
    ObjectNotFoundError,
    RevisionConflictError,
    ScoreNotApprovedError,
    ScoreNotFoundError,
    TaskBundleNotFoundError,
)
from mrr.domain.hashing_policy import sign_object, verify_object_signature
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.capability_registry.service import CapabilityRegistry
from mrr.services.research_score.service import ResearchScoreService
from mrr.services.task_bundle.service import (
    NodeTaskDecisionService,
    RecordEvent,
    RecordRevisionWithEvent,
    TaskBundleService,
    _current_status,
)

# ---------------------------------------------------------------------------
# In-memory fakes (ObjectRepository protocol conformance + a minimal event
# journal that also supports append), and fake "unit of work" callables for
# both the content-revision path and the ADR-0007 event-only path.
# Deliberate local duplicate of the E2-T01/T02 test modules' own fakes.
# ---------------------------------------------------------------------------


class FakeObjectRepository:
    """In-memory stand-in for ``mrr.domain.repositories.ObjectRepository``.
    Enforces the same optimistic-concurrency contract
    ``PostgresObjectRepository`` does, so a service bug that computes the
    wrong expected/next revision fails the test loudly.
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
        expected_new_revision = (
            1 if expected_current_revision is None else expected_current_revision + 1
        )
        if obj.revision != expected_new_revision:
            raise ValueError(
                f"obj.revision ({obj.revision!r}) does not match the revision implied by "
                f"expected_current_revision ({expected_current_revision!r}): expected "
                f"{expected_new_revision!r}"
            )
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

    def overwrite_latest_body_for_test(self, id: str, body: dict[str, Any]) -> None:
        """Test-only: directly replace the CURRENT revision's stored ``body``
        without going through any service or the revision-conflict checks
        above — simulating a row tampered with (or corrupted) after it was
        written, bypassing every service entirely. No legitimate write path
        anywhere in this codebase can produce this; it exists only so
        ``test_content_tampered_after_signing_is_rejected_on_node_side`` can
        prove that signature verification catches it.
        """
        revisions = self._revisions[id]
        revisions[-1] = replace(revisions[-1], body=body)


class FakeEventLog:
    """In-memory stand-in for the full event-log surface this module's
    services need: ``read_all`` (causation-chain lookups, ``_current_status``)
    and an append entry point used by both fake unit-of-work callables below.
    """

    def __init__(self) -> None:
        self.appended: list[AppendedEvent] = []

    def append_for_test(self, event: DomainEvent) -> AppendedEvent:
        appended = AppendedEvent(
            event=event,
            sequence=len(self.appended) + 1,
            content_hash=f"sha256:{'b' * 64}",
            prev_hash=self.appended[-1].content_hash if self.appended else None,
        )
        self.appended.append(appended)
        return appended

    def read_all(self) -> list[AppendedEvent]:
        return list(self.appended)


def _fake_record(
    object_repository: FakeObjectRepository, event_log: FakeEventLog
) -> RecordRevisionWithEvent:
    def _record(
        obj: StoredObject,
        expected_current_revision: int | None,
        event: DomainEvent,
    ) -> tuple[StoredObject, AppendedEvent]:
        stored = object_repository.insert_revision(obj, expected_current_revision)
        appended = event_log.append_for_test(event)
        return stored, appended

    return _record


def _fake_record_event(event_log: FakeEventLog) -> RecordEvent:
    def _record_event(event: DomainEvent) -> AppendedEvent:
        return event_log.append_for_test(event)

    return _record_event


class Harness:
    """Everything one test needs: the shared fake store/event log, the two
    reused E2-T01/T02 services, and the two services under test here.
    """

    def __init__(self) -> None:
        self.object_repository = FakeObjectRepository()
        self.event_log = FakeEventLog()
        record = _fake_record(self.object_repository, self.event_log)
        record_event = _fake_record_event(self.event_log)
        self.research_score_service = ResearchScoreService(
            self.object_repository, self.event_log, record
        )
        self.capability_registry = CapabilityRegistry(
            self.object_repository, self.event_log, record
        )
        self.task_bundle_service = TaskBundleService(
            self.object_repository,
            self.event_log,
            record,
            record_event,
            self.research_score_service,
            self.capability_registry,
        )
        self.node_decision_service = NodeTaskDecisionService(
            self.object_repository, self.event_log, record, record_event
        )


def _harness() -> Harness:
    return Harness()


_ACTOR = new_urn("agent-role")
_POLICY_VERSION = "policy-2026-07-01"
_CAPABILITY_NAME = "statistics.recompute"
_CAPABILITY_VERSION = "1.0.0"


def _correlation_id() -> str:
    return new_urn("research-run")


# ---------------------------------------------------------------------------
# Seeding an approved ResearchScore directly (mirrors
# tests/unit/services/research_score/test_service.py's own ``_seed`` helper
# — status is data the test controls directly, not driven through the full
# submit/approve lifecycle).
# ---------------------------------------------------------------------------


def _approved_score(*, id: str | None = None) -> ResearchScore:
    return ResearchScore.model_validate(
        {
            "id": id or new_urn("research-score"),
            "api_version": "mrr/v1alpha1",
            "kind": "ResearchScore",
            "practice_id": new_urn("practice"),
            "revision": 1,
            "created_at": datetime.now(UTC),
            "created_by": new_urn("agent-role"),
            "content_hash": "sha256:" + "a" * 64,
            "question": "Does this fixture question satisfy the schema's minimum length?",
            "objectives": ["Measure something falsifiable."],
            "non_goals": [],
            "scope": {},
            "methods": {"allowed": [], "prohibited": []},
            "data_classes": ["PUBLIC"],
            "autonomy": {},
            "budgets": {},
            "quality_gates": ["No unsupported claim without independent verification."],
            "stop_conditions": ["Budget exhausted."],
            "publication_policy": {
                "max_disclosure": "INTERNAL",
                "external_publication_requires_approval": True,
            },
            "status": "APPROVED",
            "approvals": [new_urn("approval")],
        }
    )


def _draft_score(*, id: str | None = None) -> ResearchScore:
    return _approved_score(id=id).model_copy(update={"status": "DRAFT", "approvals": []})


def _score_to_stored_object(score: ResearchScore) -> StoredObject:
    body: dict[str, Any] = json.loads(score.model_dump_json(exclude_none=True))
    return StoredObject(
        id=score.id,
        api_version=score.api_version,
        kind=score.kind,
        practice_id=score.practice_id,
        revision=score.revision,
        created_at=score.created_at,
        created_by=score.created_by,
        content_hash=score.content_hash,
        supersedes=score.supersedes,
        labels=score.labels,
        body=body,
    )


def _seed_score(object_repository: FakeObjectRepository, score: ResearchScore) -> None:
    object_repository.insert_revision(
        _score_to_stored_object(score), expected_current_revision=None
    )


# ---------------------------------------------------------------------------
# Registering a NodeManifest declaring the test capability, for real, through
# CapabilityRegistry.register (genuine E2-T02 reuse).
# ---------------------------------------------------------------------------


def _node_manifest(*, node_id: str, **overrides: Any) -> NodeManifest:
    now = datetime.now(UTC)
    data: dict[str, Any] = {
        "id": new_urn("node-manifest"),
        "api_version": "mrr/v1alpha1",
        "kind": "NodeManifest",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": now,
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "node_id": node_id,
        "capabilities": [
            {
                "name": _CAPABILITY_NAME,
                "version": _CAPABILITY_VERSION,
                "input_schema": "urn:mrr:schema:numeric-check:1",
                "output_schema": "urn:mrr:schema:evidence-crate:1",
                "max_autonomy": "A2",
                "approval": "automatic",
                "network_profile": "none",
            }
        ],
        "restrictions": [],
        "accepted_classifications": ["PUBLIC"],
        "transport_modes": ["online"],
        "valid_from": now - timedelta(days=1),
        "valid_until": now + timedelta(days=365),
        "public_keys": ["did:key:zTestKey"],
        "signature": {
            "signer_practice_id": new_urn("practice"),
            "key_id": "key-test",
            "algorithm": "Ed25519",
            "signed_at": now,
            "value": "0" * 44,
        },
    }
    data.update(overrides)
    return NodeManifest.model_validate(data)


def _register_node(
    capability_registry: CapabilityRegistry, node_id: str, private_key: Ed25519PrivateKey
) -> None:
    """Sign over the ``exclude_none=True`` form (ADR-0004,
    task-packets/E5-T00.yaml) — the same canonical body
    ``CapabilityRegistry.register`` verifies against.
    """
    manifest = _node_manifest(node_id=node_id)
    signature_value = sign_object(
        private_key, json.loads(manifest.model_dump_json(exclude_none=True))
    )
    signed = manifest.model_copy(
        update={"signature": manifest.signature.model_copy(update={"value": signature_value})}
    )
    capability_registry.register(
        signed,
        private_key.public_key(),
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )


# ---------------------------------------------------------------------------
# TaskBundle fixture factory and signing helper.
# ---------------------------------------------------------------------------


def _bundle(
    *,
    origin_practice_id: str | None = None,
    target_node_id: str | None = None,
    research_score_id: str | None = None,
    bundle_id: str | None = None,
    **overrides: Any,
) -> TaskBundle:
    now = datetime.now(UTC)
    data: dict[str, Any] = {
        "id": bundle_id or new_urn("task-bundle"),
        "api_version": "mrr/v1alpha1",
        "kind": "TaskBundle",
        "practice_id": origin_practice_id or new_urn("practice"),
        "revision": 1,
        "created_at": now,
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "origin_practice_id": origin_practice_id or new_urn("practice"),
        "target_node_id": target_node_id or new_urn("node"),
        "research_score_id": research_score_id or new_urn("research-score"),
        "research_score_revision": 1,
        "branch_id": new_urn("branch"),
        "capability": {"name": _CAPABILITY_NAME, "version": _CAPABILITY_VERSION},
        "purpose": "Recompute the summary statistics for the confirmatory branch.",
        "instructions": {"note": "run the standard pipeline"},
        "inputs": [],
        "data_access_mode": "read_local",
        "execution": {
            "image_digest": "sha256:" + "c" * 64,
            "entrypoint": ["run.sh"],
        },
        "resource_limits": {"cpu": 1.0, "memory_mb": 512, "disk_mb": 100, "timeout_seconds": 60},
        "network_policy": {"mode": "deny_all", "allowlist": []},
        "output_schema": "urn:mrr:schema:evidence-crate:1",
        "classification": "PUBLIC",
        "approval_requirement": "automatic",
        "expires_at": now + timedelta(days=1),
        "nonce": "n" * 16,
        "signature": {
            "signer_practice_id": origin_practice_id or new_urn("practice"),
            "key_id": "origin-key",
            "algorithm": "Ed25519",
            "signed_at": now,
            "value": "0" * 44,
        },
        "status": "CREATED",
    }
    data.update(overrides)
    return TaskBundle.model_validate(data)


def _sign(bundle: TaskBundle, private_key: Ed25519PrivateKey) -> TaskBundle:
    """Sign ``bundle`` for real with ``private_key`` (E1-T02 ``sign_object``)
    over the ``exclude_none=True`` form (ADR-0004, task-packets/E5-T00.yaml)
    — the same canonical body ``TaskBundleService``/``NodeTaskDecisionService``
    verify against (directly off the persisted ``StoredObject.body`` — see
    ``_authorize_and_verify``).
    """
    signature_value = sign_object(
        private_key, json.loads(bundle.model_dump_json(exclude_none=True))
    )
    return bundle.model_copy(
        update={"signature": bundle.signature.model_copy(update={"value": signature_value})}
    )


def _signed_bundle(private_key: Ed25519PrivateKey, **overrides: Any) -> TaskBundle:
    return _sign(_bundle(**overrides), private_key)


def _fully_wired_bundle(
    harness: Harness, *, origin_key: Ed25519PrivateKey, node_key: Ed25519PrivateKey
) -> tuple[TaskBundle, str, str]:
    """Seed an approved score, register a node declaring the test capability,
    build and sign a matching bundle. Returns ``(bundle, node_id, score_id)``.
    """
    score = _approved_score()
    _seed_score(harness.object_repository, score)
    node_id = new_urn("node")
    _register_node(harness.capability_registry, node_id, node_key)
    bundle = _signed_bundle(origin_key, target_node_id=node_id, research_score_id=score.id)
    return bundle, node_id, score.id


# ---------------------------------------------------------------------------
# create(): fail-closed gates (MRR-FR-004 reuse, MRR-FR-021 declaration
# check). Unaffected by ADR-0007 — create() still writes the one-time
# content revision.
# ---------------------------------------------------------------------------


def test_create_fails_closed_on_unapproved_score() -> None:
    harness = _harness()
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    score = _draft_score()
    _seed_score(harness.object_repository, score)
    node_id = new_urn("node")
    _register_node(harness.capability_registry, node_id, node_key)
    bundle = _signed_bundle(origin_key, target_node_id=node_id, research_score_id=score.id)

    with pytest.raises(ScoreNotApprovedError):
        harness.task_bundle_service.create(
            bundle, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
        )

    with pytest.raises(ObjectNotFoundError):
        harness.object_repository.get_latest(bundle.id)
    assert [e for e in harness.event_log.read_all() if e.event.object_id == bundle.id] == []


def test_create_fails_closed_on_missing_score() -> None:
    harness = _harness()
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    node_id = new_urn("node")
    _register_node(harness.capability_registry, node_id, node_key)
    bundle = _signed_bundle(origin_key, target_node_id=node_id)

    with pytest.raises(ScoreNotFoundError):
        harness.task_bundle_service.create(
            bundle, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
        )
    assert [e for e in harness.event_log.read_all() if e.event.object_id == bundle.id] == []


def test_create_fails_closed_on_undeclared_capability() -> None:
    harness = _harness()
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    score = _approved_score()
    _seed_score(harness.object_repository, score)
    node_id = new_urn("node")
    _register_node(harness.capability_registry, node_id, node_key)
    bundle = _signed_bundle(
        origin_key,
        target_node_id=node_id,
        research_score_id=score.id,
        capability={"name": "no-such.capability", "version": "9.9.9"},
    )

    with pytest.raises(CapabilityNotDeclaredError) as excinfo:
        harness.task_bundle_service.create(
            bundle, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
        )
    assert excinfo.value.node_id == node_id

    with pytest.raises(ObjectNotFoundError):
        harness.object_repository.get_latest(bundle.id)
    assert [e for e in harness.event_log.read_all() if e.event.object_id == bundle.id] == []


def test_create_fails_closed_when_target_node_has_no_manifest() -> None:
    harness = _harness()
    origin_key = Ed25519PrivateKey.generate()
    score = _approved_score()
    _seed_score(harness.object_repository, score)
    bundle = _signed_bundle(
        origin_key, research_score_id=score.id
    )  # target_node_id never registered

    with pytest.raises(NodeManifestNotFoundError):
        harness.task_bundle_service.create(
            bundle, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
        )
    assert harness.event_log.read_all() == []


def test_create_persists_revision_1_created_status_and_event() -> None:
    harness = _harness()
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    bundle, node_id, score_id = _fully_wired_bundle(
        harness, origin_key=origin_key, node_key=node_key
    )

    stored = harness.task_bundle_service.create(
        bundle, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    assert stored.revision == 1
    assert stored.body["status"] == "CREATED"
    events = harness.event_log.read_all()
    bundle_events = [e for e in events if e.event.object_id == bundle.id]
    assert len(bundle_events) == 1
    assert bundle_events[0].event.event_type == "task_bundle.created"
    assert bundle_events[0].event.causation_id is None


# ---------------------------------------------------------------------------
# offer() then node accept(): CREATED -> OFFERED -> ACCEPTED. ADR-0007: NO
# new content revision at any point — the content record stays at revision 1
# throughout.
# ---------------------------------------------------------------------------


def test_origin_service_has_no_accept_method() -> None:
    """MRR-FR-022: the origin API structurally cannot move a bundle to
    ACCEPTED — assert against TaskBundleService's own public API surface,
    not just against behavior. ``accept_modification_trusted`` (task-packets/
    E5-T04.yaml) is the trust-anchored counterpart of the pre-existing
    ``accept_modification`` — additive, not a new path to ACCEPTED — so it
    is expected in the surface alongside it; see
    tests/unit/services/task_bundle/test_service_trust.py for its own tests.
    """
    public_methods = {name for name in dir(TaskBundleService) if not name.startswith("_")}
    assert "accept" not in public_methods
    assert "reject" not in public_methods
    assert "defer" not in public_methods
    assert "propose_modification" not in public_methods
    assert public_methods == {
        "create",
        "offer",
        "accept_modification",
        "accept_modification_trusted",
    }


def test_offer_then_node_accept_moves_through_full_lifecycle() -> None:
    harness = _harness()
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    bundle, node_id, _ = _fully_wired_bundle(harness, origin_key=origin_key, node_key=node_key)
    harness.task_bundle_service.create(
        bundle, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    offered = harness.task_bundle_service.offer(
        bundle.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    assert offered.status == "OFFERED"
    # ADR-0007: no new content revision for a pure lifecycle transition.
    assert offered.content.revision == 1
    assert offered.content.body["status"] == "CREATED"  # the creation snapshot, unchanged

    accepted = harness.node_decision_service.accept(
        bundle.id,
        node_id,
        origin_key.public_key(),
        actor=node_id,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    assert accepted.status == "ACCEPTED"
    assert accepted.content.revision == 1
    assert accepted.content.body["status"] == "CREATED"

    # The content record itself never moved off revision 1.
    assert harness.object_repository.get_latest(bundle.id).revision == 1

    events = [e.event for e in harness.event_log.read_all() if e.event.object_id == bundle.id]
    assert [e.event_type for e in events] == [
        "task_bundle.created",
        "task_bundle.offered",
        "task_bundle.accepted",
    ]
    # A real causal chain, not independent roots.
    assert events[1].causation_id == events[0].id
    assert events[2].causation_id == events[1].id
    # Every event's object_revision names the (unchanged) content revision.
    assert {e.object_revision for e in events} == {1}


# ---------------------------------------------------------------------------
# MRR-FR-022: node authority, enforced structurally.
# ---------------------------------------------------------------------------


def test_non_target_identity_calling_accept_fails_closed_and_persists_nothing() -> None:
    harness = _harness()
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    bundle, node_id, _ = _fully_wired_bundle(harness, origin_key=origin_key, node_key=node_key)
    harness.task_bundle_service.create(
        bundle, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    harness.task_bundle_service.offer(
        bundle.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    not_the_target_node = new_urn("node")

    with pytest.raises(NodeAuthorityError) as excinfo:
        harness.node_decision_service.accept(
            bundle.id,
            not_the_target_node,
            origin_key.public_key(),
            actor=not_the_target_node,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )
    assert excinfo.value.target_node_id == node_id
    assert excinfo.value.attempted_node_id == not_the_target_node

    latest = harness.object_repository.get_latest(bundle.id)
    assert latest.revision == 1  # never advances — offer() was event-only too
    events = [e for e in harness.event_log.read_all() if e.event.object_id == bundle.id]
    assert len(events) == 2  # created, offered — no accepted event recorded


def test_non_target_identity_calling_reject_fails_closed_and_persists_nothing() -> None:
    harness = _harness()
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    bundle, node_id, _ = _fully_wired_bundle(harness, origin_key=origin_key, node_key=node_key)
    harness.task_bundle_service.create(
        bundle, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    harness.task_bundle_service.offer(
        bundle.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    not_the_target_node = new_urn("node")

    with pytest.raises(NodeAuthorityError):
        harness.node_decision_service.reject(
            bundle.id,
            not_the_target_node,
            origin_key.public_key(),
            reason_category="other",
            actor=not_the_target_node,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )

    latest = harness.object_repository.get_latest(bundle.id)
    assert latest.revision == 1
    events = [e for e in harness.event_log.read_all() if e.event.object_id == bundle.id]
    assert len(events) == 2  # created, offered — no rejection recorded


def test_unknown_bundle_id_raises_task_bundle_not_found() -> None:
    harness = _harness()
    node_id = new_urn("node")
    with pytest.raises(TaskBundleNotFoundError):
        harness.node_decision_service.accept(
            new_urn("task-bundle"),
            node_id,
            Ed25519PrivateKey.generate().public_key(),
            actor=node_id,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )


# ---------------------------------------------------------------------------
# MRR-FR-031: origin signature verified before any node decision — including
# the CRITICAL ADR-0007 claims: it still verifies after transitions, and it
# rejects genuine content tampering (not just "signed with the wrong key").
# ---------------------------------------------------------------------------


def test_tampered_origin_signature_refused_before_accept() -> None:
    harness = _harness()
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    bundle, node_id, _ = _fully_wired_bundle(harness, origin_key=origin_key, node_key=node_key)
    harness.task_bundle_service.create(
        bundle, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    harness.task_bundle_service.offer(
        bundle.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    # Simulate tampering: a fresh key does not match the one that signed the
    # bundle at create() time.
    wrong_key = Ed25519PrivateKey.generate()

    with pytest.raises(SignatureVerificationError):
        harness.node_decision_service.accept(
            bundle.id,
            node_id,
            wrong_key.public_key(),
            actor=node_id,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )

    latest = harness.object_repository.get_latest(bundle.id)
    assert latest.revision == 1
    events = [e for e in harness.event_log.read_all() if e.event.object_id == bundle.id]
    assert len(events) == 2  # nothing new recorded


def test_content_tampered_after_signing_is_rejected_on_node_side() -> None:
    """CRITICAL — the exact failure mode ADR-0007 exists to close.

    Before ADR-0007, verification scanned stored revisions for one whose
    ``signature.value`` matched (``_find_signed_revision``), proving "some
    ancestor was validly signed" rather than "the CURRENT content matches
    what was signed". This test tampers with the CURRENT stored content
    directly (bypassing every service — the only way tampering can happen,
    since no service exposes a content-mutating method for an existing
    revision) while leaving ``signature.value`` untouched, and asserts that
    accepting on the node side is refused: the reconstructed bundle's fields
    no longer canonicalize to what the signature actually covers.
    """
    harness = _harness()
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    bundle, node_id, _ = _fully_wired_bundle(harness, origin_key=origin_key, node_key=node_key)
    harness.task_bundle_service.create(
        bundle, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    harness.task_bundle_service.offer(
        bundle.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    stored = harness.object_repository.get_latest(bundle.id)
    tampered_body = dict(stored.body)
    tampered_body["purpose"] = "a completely different purpose the origin never signed"
    harness.object_repository.overwrite_latest_body_for_test(bundle.id, tampered_body)

    with pytest.raises(SignatureVerificationError):
        harness.node_decision_service.accept(
            bundle.id,
            node_id,
            origin_key.public_key(),  # the genuinely correct key
            actor=node_id,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )

    events = [e for e in harness.event_log.read_all() if e.event.object_id == bundle.id]
    assert len(events) == 2  # created, offered — no accepted event recorded despite tampering


def test_signature_still_verifies_after_offer_and_accept() -> None:
    """CRITICAL (ADR-0007's reason to exist): after an arbitrary sequence of
    lifecycle transitions, the origin signature still verifies DIRECTLY
    against whatever ``get_latest`` returns — no historical scan needed,
    because the content record was never touched by any of those
    transitions.
    """
    harness = _harness()
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    bundle, node_id, _ = _fully_wired_bundle(harness, origin_key=origin_key, node_key=node_key)
    harness.task_bundle_service.create(
        bundle, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    harness.task_bundle_service.offer(
        bundle.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    harness.node_decision_service.accept(
        bundle.id,
        node_id,
        origin_key.public_key(),
        actor=node_id,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    latest = harness.object_repository.get_latest(bundle.id)
    assert latest.revision == 1
    current_bundle = TaskBundle.model_validate(latest.body)

    # Must not raise: direct verification against the CURRENT content record,
    # over the persisted exclude_none=True body itself (ADR-0004,
    # task-packets/E5-T00.yaml) — the same form _authorize_and_verify uses.
    verify_object_signature(
        origin_key.public_key(),
        latest.body,
        current_bundle.signature.value,
        algorithm=current_bundle.signature.algorithm,
    )


def test_signature_still_verifies_after_offer_and_defer_or_reject() -> None:
    harness = _harness()
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()

    for decision in ("defer", "reject"):
        bundle, node_id, _ = _fully_wired_bundle(harness, origin_key=origin_key, node_key=node_key)
        harness.task_bundle_service.create(
            bundle, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
        )
        harness.task_bundle_service.offer(
            bundle.id,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )
        if decision == "defer":
            harness.node_decision_service.defer(
                bundle.id,
                node_id,
                origin_key.public_key(),
                actor=node_id,
                policy_version=_POLICY_VERSION,
                correlation_id=_correlation_id(),
            )
        else:
            harness.node_decision_service.reject(
                bundle.id,
                node_id,
                origin_key.public_key(),
                reason_category="other",
                actor=node_id,
                policy_version=_POLICY_VERSION,
                correlation_id=_correlation_id(),
            )

        latest = harness.object_repository.get_latest(bundle.id)
        assert latest.revision == 1
        current_bundle = TaskBundle.model_validate(latest.body)
        verify_object_signature(
            origin_key.public_key(),
            latest.body,
            current_bundle.signature.value,
            algorithm=current_bundle.signature.algorithm,
        )


# ---------------------------------------------------------------------------
# ADR-0007: the current lifecycle status is event-derived, not read off the
# content record's own (creation-time) status field.
# ---------------------------------------------------------------------------


def test_current_status_is_event_derived() -> None:
    """Direct unit test of ``_current_status`` — the new helper ADR-0007
    introduces: newest matching lifecycle-transition event wins, falling
    back to the content record's own creation-time status when none exists
    yet. Built from raw fixtures rather than through the services, so the
    helper's own filtering logic (object_id match, event_type allowlist) is
    exercised directly.
    """
    event_log = FakeEventLog()
    bundle_id = new_urn("task-bundle")
    other_bundle_id = new_urn("task-bundle")
    content = StoredObject(
        id=bundle_id,
        api_version="mrr/v1alpha1",
        kind="TaskBundle",
        practice_id=new_urn("practice"),
        revision=1,
        created_at=datetime.now(UTC),
        created_by=new_urn("agent-role"),
        content_hash="sha256:" + "a" * 64,
        supersedes=None,
        labels=None,
        body={"status": "CREATED"},
    )

    # No transition events yet -> fall back to the content snapshot.
    assert _current_status(event_log, content) == "CREATED"

    def _record(event_type: str, object_id: str, payload: dict[str, Any]) -> None:
        event_log.append_for_test(
            DomainEvent(
                id=new_urn("domain-event"),
                event_type=event_type,
                occurred_at=datetime.now(UTC),
                actor=_ACTOR,
                policy_version=_POLICY_VERSION,
                causation_id=None,
                correlation_id=_correlation_id(),
                object_id=object_id,
                object_revision=1,
                payload=payload,
            )
        )

    # Noise: a different bundle's events must never affect this one's status.
    _record("task_bundle.offered", other_bundle_id, {"to_status": "OFFERED"})
    # created is NOT a transition event — must not override the fallback.
    _record("task_bundle.created", bundle_id, {"status": "CREATED"})
    assert _current_status(event_log, content) == "CREATED"

    _record("task_bundle.offered", bundle_id, {"from_status": "CREATED", "to_status": "OFFERED"})
    assert _current_status(event_log, content) == "OFFERED"

    # modification_acknowledged carries no to_status and changes nothing.
    _record(
        "task_bundle.modification_acknowledged",
        bundle_id,
        {"status": "OFFERED", "acknowledged_content_revision": 1},
    )
    assert _current_status(event_log, content) == "OFFERED"

    _record("task_bundle.accepted", bundle_id, {"from_status": "OFFERED", "to_status": "ACCEPTED"})
    assert _current_status(event_log, content) == "ACCEPTED"


def test_content_snapshot_status_diverges_from_live_status_after_transition() -> None:
    """The divergence ADR-0007 introduces, made explicit: after ``offer``,
    the content record's own persisted ``status`` field still says
    ``CREATED`` (its creation-time snapshot — untouched, since offer writes
    no revision), while the live, event-derived status is ``OFFERED``.
    """
    harness = _harness()
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    bundle, node_id, _ = _fully_wired_bundle(harness, origin_key=origin_key, node_key=node_key)
    harness.task_bundle_service.create(
        bundle, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    offered = harness.task_bundle_service.offer(
        bundle.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    assert offered.status == "OFFERED"
    assert offered.content.body["status"] == "CREATED"
    assert offered.status != offered.content.body["status"]


# ---------------------------------------------------------------------------
# Illegal transitions: fail closed, append NO event, create NO revision.
# ---------------------------------------------------------------------------


def test_accept_on_a_not_yet_offered_bundle_raises_and_persists_nothing() -> None:
    harness = _harness()
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    bundle, node_id, _ = _fully_wired_bundle(harness, origin_key=origin_key, node_key=node_key)
    harness.task_bundle_service.create(
        bundle, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    # Never offered — still CREATED.

    with pytest.raises(InvalidTransitionError) as excinfo:
        harness.node_decision_service.accept(
            bundle.id,
            node_id,
            origin_key.public_key(),
            actor=node_id,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )
    assert excinfo.value.from_state == "CREATED"
    assert excinfo.value.to_state == "ACCEPTED"

    latest = harness.object_repository.get_latest(bundle.id)
    assert latest.revision == 1
    assert latest.body["status"] == "CREATED"
    events = [e for e in harness.event_log.read_all() if e.event.object_id == bundle.id]
    assert len(events) == 1  # only "created" — the illegal accept appended nothing


def test_double_accept_raises_and_persists_nothing_new() -> None:
    harness = _harness()
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    bundle, node_id, _ = _fully_wired_bundle(harness, origin_key=origin_key, node_key=node_key)
    harness.task_bundle_service.create(
        bundle, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    harness.task_bundle_service.offer(
        bundle.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    harness.node_decision_service.accept(
        bundle.id,
        node_id,
        origin_key.public_key(),
        actor=node_id,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    with pytest.raises(InvalidTransitionError):
        harness.node_decision_service.accept(
            bundle.id,
            node_id,
            origin_key.public_key(),
            actor=node_id,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )

    latest = harness.object_repository.get_latest(bundle.id)
    assert latest.revision == 1  # never advances
    events = [e for e in harness.event_log.read_all() if e.event.object_id == bundle.id]
    assert len(events) == 3  # created, offered, accepted — the second accept appended nothing


# ---------------------------------------------------------------------------
# defer() and reject() (MRR-FR-024: reject records a reason category).
# ---------------------------------------------------------------------------


def test_defer_transitions_and_persists_event() -> None:
    harness = _harness()
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    bundle, node_id, _ = _fully_wired_bundle(harness, origin_key=origin_key, node_key=node_key)
    harness.task_bundle_service.create(
        bundle, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    harness.task_bundle_service.offer(
        bundle.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    deferred = harness.node_decision_service.defer(
        bundle.id,
        node_id,
        origin_key.public_key(),
        actor=node_id,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    assert deferred.status == "DEFERRED"
    assert deferred.content.revision == 1


def test_reject_records_reason_category_event() -> None:
    harness = _harness()
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    bundle, node_id, _ = _fully_wired_bundle(harness, origin_key=origin_key, node_key=node_key)
    harness.task_bundle_service.create(
        bundle, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    harness.task_bundle_service.offer(
        bundle.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    rejected = harness.node_decision_service.reject(
        bundle.id,
        node_id,
        origin_key.public_key(),
        reason_category="data_access_denied",
        explanation="local policy forbids this data class today",
        actor=node_id,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    assert rejected.status == "REJECTED"
    assert rejected.content.revision == 1

    assert rejected.appended_event.event.event_type == "task_bundle.rejected"
    assert rejected.appended_event.event.payload["reason_category"] == "data_access_denied"
    assert (
        rejected.appended_event.event.payload["explanation"]
        == "local policy forbids this data class today"
    )


def test_reject_rejects_unknown_reason_category() -> None:
    harness = _harness()
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    bundle, node_id, _ = _fully_wired_bundle(harness, origin_key=origin_key, node_key=node_key)
    harness.task_bundle_service.create(
        bundle, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    harness.task_bundle_service.offer(
        bundle.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )

    with pytest.raises(ValueError, match="reason_category"):
        harness.node_decision_service.reject(
            bundle.id,
            node_id,
            origin_key.public_key(),
            reason_category="not_a_real_reason",  # type: ignore[arg-type]
            actor=node_id,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )

    latest = harness.object_repository.get_latest(bundle.id)
    events = [e for e in harness.event_log.read_all() if e.event.object_id == bundle.id]
    assert len(events) == 2  # created, offered — nothing appended for the bad reject
    assert _current_status(harness.event_log, latest) == "OFFERED"  # unchanged


# ---------------------------------------------------------------------------
# propose_modification(): a new signed CONTENT revision, prior revision
# intact (MRR-FR-023/034). ADR-0007: the only node decision that still
# writes a revision. Since offer() no longer advances the revision, the
# modifier's counter-proposal is latest.revision + 1 == 2 (not 3, as it was
# under the pre-ADR-0007, revision-per-transition model).
# ---------------------------------------------------------------------------


def test_propose_modification_creates_new_signed_revision_prior_intact() -> None:
    harness = _harness()
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    bundle, node_id, _ = _fully_wired_bundle(harness, origin_key=origin_key, node_key=node_key)
    harness.task_bundle_service.create(
        bundle, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    offered = harness.task_bundle_service.offer(
        bundle.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    # offer() is event-only (ADR-0007) — the content record is still
    # revision 1, the origin's own original signed bytes.
    assert offered.content.revision == 1

    modified = _sign(
        bundle.model_copy(
            update={
                "revision": 2,
                "status": "OFFERED",
                "content_hash": "sha256:" + "d" * 64,
                "resource_limits": bundle.resource_limits.model_copy(
                    update={"cpu": 2.0, "memory_mb": 1024, "disk_mb": 200, "timeout_seconds": 120}
                ),
            }
        ),
        node_key,
    )

    result = harness.node_decision_service.propose_modification(
        bundle.id,
        modified,
        node_id,
        origin_key.public_key(),
        actor=node_id,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert result.body["status"] == "OFFERED"
    assert result.body["revision"] == 2
    assert result.body["resource_limits"]["cpu"] == 2.0
    assert result.body["content_hash"] == "sha256:" + "d" * 64

    # The prior (origin-signed) revision is completely intact and still
    # readable at its own store row — untouched by the modification.
    original_row = harness.object_repository.get_revision(bundle.id, 1)
    assert original_row.body["revision"] == 1
    assert original_row.body["status"] == "CREATED"
    assert original_row.body["content_hash"] == bundle.content_hash
    assert original_row.body["resource_limits"]["cpu"] == 1.0

    events = [
        e.event.event_type for e in harness.event_log.read_all() if e.event.object_id == bundle.id
    ]
    # propose_modification is one atomic write, not two — see the service
    # module docstring's "propose_modification is one write, not two"
    # section for why a separate MODIFICATION_PROPOSED row is not persisted.
    assert events == [
        "task_bundle.created",
        "task_bundle.offered",
        "task_bundle.modification_offered",
    ]

    # And the new revision's own signature verifies directly (it is now the
    # CURRENT content record — the same ADR-0007 property, exercised across
    # a genuine content revision this time) — over the persisted
    # exclude_none=True body itself (ADR-0004, task-packets/E5-T00.yaml).
    new_latest = harness.object_repository.get_latest(bundle.id)
    current_bundle = TaskBundle.model_validate(new_latest.body)
    verify_object_signature(
        node_key.public_key(),
        new_latest.body,
        current_bundle.signature.value,
        algorithm=current_bundle.signature.algorithm,
    )


def test_propose_modification_rejects_unchanged_content_hash() -> None:
    harness = _harness()
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    bundle, node_id, _ = _fully_wired_bundle(harness, origin_key=origin_key, node_key=node_key)
    harness.task_bundle_service.create(
        bundle, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    harness.task_bundle_service.offer(
        bundle.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    not_really_modified = _sign(
        bundle.model_copy(
            update={
                "revision": 2,
                "status": "OFFERED",
                "content_hash": bundle.content_hash,  # deliberately unchanged
            }
        ),
        node_key,
    )

    with pytest.raises(ValueError, match="content_hash"):
        harness.node_decision_service.propose_modification(
            bundle.id,
            not_really_modified,
            node_id,
            origin_key.public_key(),
            actor=node_id,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )


def test_full_negotiation_loop_modification_then_accept() -> None:
    """A full round trip — offer, node proposes a modification, origin
    acknowledges it (event-only, ADR-0007), node accepts the modified
    revision using the MODIFIER's key (now the current signer, since
    propose_modification advanced the content record to revision 2).
    """
    harness = _harness()
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    bundle, node_id, _ = _fully_wired_bundle(harness, origin_key=origin_key, node_key=node_key)
    harness.task_bundle_service.create(
        bundle, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    harness.task_bundle_service.offer(
        bundle.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    modified = _sign(
        bundle.model_copy(
            update={"revision": 2, "status": "OFFERED", "content_hash": "sha256:" + "e" * 64}
        ),
        node_key,
    )
    proposed = harness.node_decision_service.propose_modification(
        bundle.id,
        modified,
        node_id,
        origin_key.public_key(),
        actor=node_id,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    assert proposed.revision == 2

    acknowledged = harness.task_bundle_service.accept_modification(
        bundle.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    assert acknowledged.status == "OFFERED"  # no state change, see docstring
    assert acknowledged.content.revision == 2  # unaffected — event-only

    accepted = harness.node_decision_service.accept(
        bundle.id,
        node_id,
        node_key.public_key(),  # the modifier's own key, now the current signer
        actor=node_id,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    assert accepted.status == "ACCEPTED"
    assert accepted.content.revision == 2  # still the modifier's content revision


def test_accept_modification_requires_offered_status() -> None:
    harness = _harness()
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    bundle, node_id, _ = _fully_wired_bundle(harness, origin_key=origin_key, node_key=node_key)
    harness.task_bundle_service.create(
        bundle, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    # Still CREATED — never offered.

    with pytest.raises(InvalidTransitionError):
        harness.task_bundle_service.accept_modification(
            bundle.id,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )


# ---------------------------------------------------------------------------
# create() rejects a non-CREATED initial status (ADR-0005 makes ``status`` a
# real, schema-defined field — mirrors
# ResearchScoreService.create()'s own "reject non-DRAFT initial status"
# guard, via the same sentinel-transition technique).
# ---------------------------------------------------------------------------


def test_create_rejects_non_created_initial_status() -> None:
    harness = _harness()
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    bundle, node_id, score_id = _fully_wired_bundle(
        harness, origin_key=origin_key, node_key=node_key
    )
    wrong_initial_status = _sign(bundle.model_copy(update={"status": "OFFERED"}), origin_key)

    with pytest.raises(InvalidTransitionError) as excinfo:
        harness.task_bundle_service.create(
            wrong_initial_status,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )
    assert excinfo.value.to_state == "OFFERED"
    with pytest.raises(ObjectNotFoundError):
        harness.object_repository.get_latest(bundle.id)


# ---------------------------------------------------------------------------
# The round-trip invariant review caught missing before ADR-0005: the
# persisted body is a plain, schema-valid TaskBundle serialization —
# TaskBundle.model_validate(stored.body) must always succeed.
# ---------------------------------------------------------------------------


def test_created_body_round_trips_through_task_bundle_model_validate() -> None:
    harness = _harness()
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    bundle, node_id, _ = _fully_wired_bundle(harness, origin_key=origin_key, node_key=node_key)

    created = harness.task_bundle_service.create(
        bundle, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    round_tripped_created = TaskBundle.model_validate(created.body)
    assert round_tripped_created.status == "CREATED"

    # ADR-0007: a pure lifecycle transition never touches the content
    # record, so its persisted body still round-trips to the SAME
    # creation-time status, even though the live status has moved on.
    offered = harness.task_bundle_service.offer(
        bundle.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    round_tripped_after_offer = TaskBundle.model_validate(offered.content.body)
    assert round_tripped_after_offer.status == "CREATED"
    assert offered.status == "OFFERED"

    accepted = harness.node_decision_service.accept(
        bundle.id,
        node_id,
        origin_key.public_key(),
        actor=node_id,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    round_tripped_after_accept = TaskBundle.model_validate(accepted.content.body)
    assert round_tripped_after_accept.status == "CREATED"
    assert accepted.status == "ACCEPTED"


# ---------------------------------------------------------------------------
# Event provenance (MRR-NFR-001).
# ---------------------------------------------------------------------------


def test_events_carry_complete_provenance() -> None:
    harness = _harness()
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    bundle, node_id, _ = _fully_wired_bundle(harness, origin_key=origin_key, node_key=node_key)
    correlation_id = _correlation_id()

    harness.task_bundle_service.create(
        bundle, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    harness.task_bundle_service.offer(
        bundle.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    events = [e.event for e in harness.event_log.read_all() if e.event.object_id == bundle.id]
    for event in events:
        assert event.actor == _ACTOR
        assert event.policy_version == _POLICY_VERSION
        assert event.correlation_id == correlation_id
        assert event.object_id == bundle.id
        assert event.occurred_at.tzinfo is not None
        assert event.object_revision == 1  # the content record's unchanged revision
    assert events[0].causation_id is None
    assert events[1].causation_id == events[0].id
