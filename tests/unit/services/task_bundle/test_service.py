"""Unit tests for ``mrr.services.task_bundle.service.TaskBundleService`` and
``NodeTaskDecisionService`` (task-packets/E2-T03.yaml), run entirely DB-free
against in-memory fakes of ``mrr.domain.repositories.ObjectRepository`` and
the event-log read surface — no PostgreSQL, no ``sqlalchemy.Engine``. Same
"lightweight fake unit-of-work" pattern
``tests/unit/services/research_score/test_service.py`` and
``tests/unit/services/capability_registry/test_service.py`` use (the fakes
below are a local, deliberate duplicate — see those modules' own docstrings
for why duplicating rather than importing test doubles across test modules
is the established convention here).

One shared fake object store/event log backs ``ResearchScoreService``,
``CapabilityRegistry``, ``TaskBundleService``, and
``NodeTaskDecisionService`` in every test — mirroring how one real
PostgreSQL ``objects``/``domain_events`` pair backs all of them in
production (``kind`` distinguishes ``ResearchScore``/``NodeManifest``/
``TaskBundle`` rows in the same table). An approved ``ResearchScore`` is
seeded directly into the fake repository (the same "seed an arbitrary
status directly" convention ``tests/unit/services/research_score/
test_service.py`` uses), and a ``NodeManifest`` declaring the bundle's
capability is registered for real through ``CapabilityRegistry.register``
with a real Ed25519 keypair — genuine reuse of E2-T01/T02, not
reimplementation or mocking of their behavior.

Acceptance-test mapping (task-packets/E2-T03.yaml):

- "create against an unapproved score fails closed" ->
  ``test_create_fails_closed_on_unapproved_score``.
- "create for a capability the target node does not declare fails closed" ->
  ``test_create_fails_closed_on_undeclared_capability``.
- "offer then node-accept moves CREATED->OFFERED->ACCEPTED and persists
  events; the origin has no path to accept on the node's behalf" ->
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
- "a bundle with a tampered/invalid origin signature is refused on the node
  side before any decision" ->
  ``test_tampered_origin_signature_refused_before_accept``.
- "illegal lifecycle transitions persist nothing" (unit-level; the packet's
  own integration-tier duplicate covers real PostgreSQL) ->
  ``test_accept_on_a_not_yet_offered_bundle_raises_and_persists_nothing``.
- the round-trip invariant ADR-0005 exists to restore (a stray body key
  used to make ``TaskBundle.model_validate(stored.body)`` fail) ->
  ``test_persisted_body_round_trips_through_task_bundle_model_validate``.
"""

from __future__ import annotations

import json
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
from mrr.domain.hashing_policy import sign_object
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.capability_registry.service import CapabilityRegistry
from mrr.services.research_score.service import ResearchScoreService
from mrr.services.task_bundle.service import (
    NodeTaskDecisionService,
    RecordRevisionWithEvent,
    TaskBundleService,
)

# ---------------------------------------------------------------------------
# In-memory fakes (ObjectRepository protocol conformance + a minimal event
# journal), and a fake "unit of work" combining them. Deliberate local
# duplicate of the E2-T01/T02 test modules' own fakes.
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


class FakeEventLog:
    """In-memory stand-in for the ``read_all``-only event journal the
    services depend on.
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


class Harness:
    """Everything one test needs: the shared fake store/event log, the two
    reused E2-T01/T02 services, and the two services under test here.
    """

    def __init__(self) -> None:
        self.object_repository = FakeObjectRepository()
        self.event_log = FakeEventLog()
        record = _fake_record(self.object_repository, self.event_log)
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
            self.research_score_service,
            self.capability_registry,
        )
        self.node_decision_service = NodeTaskDecisionService(
            self.object_repository, self.event_log, record
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
    manifest = _node_manifest(node_id=node_id)
    signature_value = sign_object(private_key, manifest.model_dump(mode="json"))
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
    over ``bundle.model_dump(mode="json")`` — the same construction
    ``TaskBundleService``/``NodeTaskDecisionService`` verify against.
    """
    signature_value = sign_object(private_key, bundle.model_dump(mode="json"))
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
# check).
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
# offer() then node accept(): CREATED -> OFFERED -> ACCEPTED, and the origin
# has no accept-style method at all.
# ---------------------------------------------------------------------------


def test_origin_service_has_no_accept_method() -> None:
    """MRR-FR-022: the origin API structurally cannot move a bundle to
    ACCEPTED — assert against TaskBundleService's own public API surface,
    not just against behavior.
    """
    public_methods = {name for name in dir(TaskBundleService) if not name.startswith("_")}
    assert "accept" not in public_methods
    assert "reject" not in public_methods
    assert "defer" not in public_methods
    assert "propose_modification" not in public_methods
    assert public_methods == {"create", "offer", "accept_modification"}


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
    assert offered.body["status"] == "OFFERED"
    assert offered.revision == 2

    accepted = harness.node_decision_service.accept(
        bundle.id,
        node_id,
        origin_key.public_key(),
        actor=node_id,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    assert accepted.body["status"] == "ACCEPTED"
    assert accepted.revision == 3

    events = [e.event for e in harness.event_log.read_all() if e.event.object_id == bundle.id]
    assert [e.event_type for e in events] == [
        "task_bundle.created",
        "task_bundle.offered",
        "task_bundle.accepted",
    ]
    # A real causal chain, not independent roots.
    assert events[1].causation_id == events[0].id
    assert events[2].causation_id == events[1].id


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
    assert latest.body["status"] == "OFFERED"  # unchanged, nothing new persisted
    assert latest.revision == 2


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
    assert latest.revision == 2
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
# MRR-FR-031: origin signature verified before any node decision.
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
    assert latest.body["status"] == "OFFERED"
    assert latest.revision == 2
    events = [e for e in harness.event_log.read_all() if e.event.object_id == bundle.id]
    assert len(events) == 2  # nothing new recorded


# ---------------------------------------------------------------------------
# Illegal transitions: fail closed, persist nothing.
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
    assert latest.revision == 3  # unchanged since the first, legal accept()


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
    assert deferred.body["status"] == "DEFERRED"


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
    assert rejected.body["status"] == "REJECTED"

    events = [e.event for e in harness.event_log.read_all() if e.event.object_id == bundle.id]
    reject_event = events[-1]
    assert reject_event.event_type == "task_bundle.rejected"
    assert reject_event.payload["reason_category"] == "data_access_denied"
    assert reject_event.payload["explanation"] == "local policy forbids this data class today"


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
    assert latest.body["status"] == "OFFERED"  # unchanged


# ---------------------------------------------------------------------------
# propose_modification(): a new signed revision, prior revision intact
# (MRR-FR-023/034).
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
    # offer() recomputed content_hash (status is now part of the hashed
    # payload, ADR-0005) — the modifier's new revision must build on the
    # CURRENT stored content, not the pre-create fixture's own placeholder.
    assert offered.revision == 2

    modified = _sign(
        bundle.model_copy(
            update={
                "revision": 3,
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
    assert result.body["revision"] == 3
    assert result.body["resource_limits"]["cpu"] == 2.0
    assert result.body["content_hash"] == "sha256:" + "d" * 64

    # The prior (origin-signed) revision is completely intact and still
    # readable at its own store row — untouched by the modification.
    original_row = harness.object_repository.get_revision(bundle.id, 2)  # the offer() row
    assert original_row.body["revision"] == 2
    assert original_row.body["status"] == "OFFERED"
    assert original_row.body["content_hash"] == offered.body["content_hash"]
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


def test_propose_modification_rejects_unchanged_content_hash() -> None:
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
    not_really_modified = _sign(
        bundle.model_copy(
            update={
                "revision": 3,
                "status": "OFFERED",
                "content_hash": offered.body["content_hash"],  # deliberately unchanged
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
    """ "the node still must accept to reach ACCEPTED" (task-packets/
    E2-T03.yaml's own framing for ``accept_modification``): a full round trip
    — offer, node proposes a modification, origin acknowledges it, node
    accepts the modified revision.
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
            update={"revision": 3, "status": "OFFERED", "content_hash": "sha256:" + "e" * 64}
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
    assert proposed.revision == 3

    acknowledged = harness.task_bundle_service.accept_modification(
        bundle.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    assert acknowledged.body["status"] == "OFFERED"  # no state change, see docstring
    assert acknowledged.revision == 4

    accepted = harness.node_decision_service.accept(
        bundle.id,
        node_id,
        node_key.public_key(),  # the modifier's own key, now the current signer
        actor=node_id,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    assert accepted.body["status"] == "ACCEPTED"
    assert accepted.revision == 5


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
# TaskBundle.model_validate(stored.body) must always succeed, and .status
# must reflect the expected lifecycle state after a transition.
# ---------------------------------------------------------------------------


def test_persisted_body_round_trips_through_task_bundle_model_validate() -> None:
    harness = _harness()
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    bundle, node_id, _ = _fully_wired_bundle(harness, origin_key=origin_key, node_key=node_key)

    created = harness.task_bundle_service.create(
        bundle, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    round_tripped_created = TaskBundle.model_validate(created.body)
    assert round_tripped_created.status == "CREATED"

    offered = harness.task_bundle_service.offer(
        bundle.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    round_tripped_offered = TaskBundle.model_validate(offered.body)
    assert round_tripped_offered.status == "OFFERED"

    accepted = harness.node_decision_service.accept(
        bundle.id,
        node_id,
        origin_key.public_key(),
        actor=node_id,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    round_tripped_accepted = TaskBundle.model_validate(accepted.body)
    assert round_tripped_accepted.status == "ACCEPTED"


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
    assert events[0].causation_id is None
    assert events[1].causation_id == events[0].id
