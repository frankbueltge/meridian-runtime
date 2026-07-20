"""Unit tests for the E5-T04 trust-anchored wiring on ``TaskBundleService``/
``NodeTaskDecisionService`` (``accept_trusted``, ``defer_trusted``,
``reject_trusted``, ``propose_modification_trusted``,
``accept_modification_trusted`` — ``services/control_plane/mrr/services/
task_bundle/service.py``), run entirely DB-free against in-memory fakes, same
convention as ``tests/unit/services/task_bundle/test_service.py`` (this
module is a deliberate local duplicate of that module's own Harness/fixture
setup, per this codebase's established "independent test tiers" convention
— see e.g. that module's own docstring).

Acceptance-test mapping (task-packets/E5-T04.yaml):

- "happy path, node side: an origin practice signs a TaskBundle with an
  active in-window key; the node resolves the trusted key via the origin
  practice's ring and a decision (accept) proceeds using that resolved
  key" -> ``test_accept_trusted_happy_path_resolves_via_origin_ring``.
- "happy path, origin side (MRR-FR-023): a node proposes a modification as
  a new signed revision; the origin resolves the trusted key via the NODE
  practice's ring and accept_modification proceeds using that resolved
  key" -> ``test_accept_modification_trusted_happy_path_resolves_via_node_ring``.
- "fail-closed matrix, each a distinct typed error, nothing decided/
  written" -> ``test_accept_trusted_signer_mismatch_fails_closed_nothing_written``,
  ``test_accept_trusted_unknown_kid_fails_closed_nothing_written``,
  ``test_accept_trusted_revoked_key_fails_closed_nothing_written``,
  ``test_accept_trusted_tampered_bundle_fails_closed_nothing_written``.
- "a bundle signed by a key valid at signing but REVOKED in the
  counterparty ring by the evaluation instant is rejected" ->
  ``test_accept_trusted_revoked_key_fails_closed_nothing_written``.
- "verification uses the ring's key: a bundle signed by an attacker key but
  claiming a trusted kid fails closed (SignatureVerificationError)" ->
  ``test_accept_trusted_key_substitution_attack_fails_closed``.
- ``defer_trusted``/``reject_trusted``/``propose_modification_trusted``
  coverage -> ``test_defer_trusted_happy_path``,
  ``test_reject_trusted_happy_path``,
  ``test_propose_modification_trusted_happy_path``.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from mrr.contracts import NodeManifest, Practice, ResearchScore, TaskBundle
from mrr.crypto.exceptions import SignatureVerificationError
from mrr.crypto.keys import derive_key_id, encode_public_key, generate_ed25519_keypair
from mrr.domain.exceptions import (
    ObjectNotFoundError,
    RevisionConflictError,
    TaskKeyNotValidError,
    TaskSignerMismatchError,
    UnknownKeyIdError,
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
    RecordEvent,
    RecordRevisionWithEvent,
    TaskBundleService,
)

# ---------------------------------------------------------------------------
# In-memory fakes — identical to test_service.py's own (deliberate local
# duplicate, not a shared import).
# ---------------------------------------------------------------------------


class FakeObjectRepository:
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
        """Test-only: directly replace the CURRENT revision's stored
        ``body`` without going through any service — simulating a row
        tampered with after it was written. Mirrors test_service.py's own
        identically-named helper.
        """
        revisions = self._revisions[id]
        revisions[-1] = replace(revisions[-1], body=body)


class FakeEventLog:
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
_NOW = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)


def _correlation_id() -> str:
    return new_urn("research-run")


# ---------------------------------------------------------------------------
# Practice / KeyRing fixtures (task-packets/E5-T01/E5-T04.yaml).
# ---------------------------------------------------------------------------


def _key_entry(
    public_key: Ed25519PublicKey,
    *,
    valid_from: datetime = _NOW - timedelta(days=1),
    valid_until: datetime = _NOW + timedelta(days=365),
    state: str = "active",
) -> dict[str, Any]:
    return {
        "kid": derive_key_id(public_key),
        "algorithm": "Ed25519",
        "encoded_public_key": encode_public_key(public_key),
        "valid_from": valid_from,
        "valid_until": valid_until,
        "state": state,
    }


def _practice(*, practice_id: str, keys: list[dict[str, Any]]) -> Practice:
    data: dict[str, Any] = {
        "id": practice_id,
        "api_version": "mrr/v1alpha1",
        "kind": "Practice",
        "practice_id": practice_id,
        "revision": 1,
        "created_at": _NOW,
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "name": "Fixture Practice",
        "description": "Fixture practice for task_bundle trust wiring tests.",
        "keys": keys,
        "governance_contacts": ["mailto:governance@fixture.invalid"],
        "supported_policy_versions": ["policy-2026-07-01"],
        "disclosure": {"max_disclosure": "PUBLIC", "trust_statement": "fixture"},
    }
    return Practice.model_validate(data)


# ---------------------------------------------------------------------------
# Seeding an approved ResearchScore + a registered NodeManifest (identical
# to test_service.py's own fixtures — required so TaskBundleService.create
# succeeds).
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
    origin_practice_id: str,
    target_node_id: str,
    research_score_id: str,
    signer_practice_id: str,
    key_id: str,
    bundle_id: str | None = None,
    **overrides: Any,
) -> TaskBundle:
    now = datetime.now(UTC)
    data: dict[str, Any] = {
        "id": bundle_id or new_urn("task-bundle"),
        "api_version": "mrr/v1alpha1",
        "kind": "TaskBundle",
        "practice_id": origin_practice_id,
        "revision": 1,
        "created_at": now,
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "origin_practice_id": origin_practice_id,
        "target_node_id": target_node_id,
        "research_score_id": research_score_id,
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
            "signer_practice_id": signer_practice_id,
            "key_id": key_id,
            "algorithm": "Ed25519",
            "signed_at": now,
            "value": "0" * 44,
        },
        "status": "CREATED",
    }
    data.update(overrides)
    return TaskBundle.model_validate(data)


def _sign(bundle: TaskBundle, private_key: Ed25519PrivateKey) -> TaskBundle:
    signature_value = sign_object(
        private_key, json.loads(bundle.model_dump_json(exclude_none=True))
    )
    return bundle.model_copy(
        update={"signature": bundle.signature.model_copy(update={"value": signature_value})}
    )


def _wired_scenario(
    harness: Harness,
) -> tuple[TaskBundle, str, Practice, Ed25519PrivateKey, Practice, Ed25519PrivateKey]:
    """Seed an approved score, register a node declaring the test
    capability, build and genuinely sign a matching bundle with a real
    origin Practice/KeyRing entry. Returns
    ``(bundle, node_id, origin_practice, origin_key, node_practice, node_key)``.
    """
    score = _approved_score()
    _seed_score(harness.object_repository, score)

    node_key = Ed25519PrivateKey.generate()
    node_id = new_urn("node")
    _register_node(harness.capability_registry, node_id, node_key)

    origin_private_key, origin_public_key = generate_ed25519_keypair()
    origin_entry = _key_entry(origin_public_key)
    origin_practice = _practice(practice_id=new_urn("practice"), keys=[origin_entry])

    node_private_key, node_public_key = generate_ed25519_keypair()
    node_entry = _key_entry(node_public_key)
    node_practice = _practice(practice_id=new_urn("practice"), keys=[node_entry])

    bundle = _bundle(
        origin_practice_id=origin_practice.id,
        target_node_id=node_id,
        research_score_id=score.id,
        signer_practice_id=origin_practice.id,
        key_id=origin_entry["kid"],
    )
    signed_bundle = _sign(bundle, origin_private_key)
    return (
        signed_bundle,
        node_id,
        origin_practice,
        origin_private_key,
        node_practice,
        node_private_key,
    )


def _create_and_offer(harness: Harness, bundle: TaskBundle) -> None:
    harness.task_bundle_service.create(
        bundle, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )
    harness.task_bundle_service.offer(
        bundle.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=_correlation_id()
    )


# ---------------------------------------------------------------------------
# Happy path, node side (accept_trusted): resolve via the ORIGIN's ring.
# ---------------------------------------------------------------------------


def test_accept_trusted_happy_path_resolves_via_origin_ring() -> None:
    harness = _harness()
    bundle, node_id, origin_practice, _origin_key, _node_practice, _node_key = _wired_scenario(
        harness
    )
    _create_and_offer(harness, bundle)

    accepted = harness.node_decision_service.accept_trusted(
        bundle.id,
        node_id,
        origin_practice,
        actor=node_id,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
        at=_NOW,
    )

    assert accepted.status == "ACCEPTED"
    assert accepted.content.revision == 1
    events = [
        e.event.event_type for e in harness.event_log.read_all() if e.event.object_id == bundle.id
    ]
    assert events == ["task_bundle.created", "task_bundle.offered", "task_bundle.accepted"]


# ---------------------------------------------------------------------------
# Happy path, origin side (accept_modification_trusted, MRR-FR-023): resolve
# via the NODE's ring.
# ---------------------------------------------------------------------------


def test_accept_modification_trusted_happy_path_resolves_via_node_ring() -> None:
    harness = _harness()
    bundle, node_id, origin_practice, origin_key, node_practice, node_key = _wired_scenario(harness)
    _create_and_offer(harness, bundle)

    modified = _sign(
        bundle.model_copy(
            update={
                "revision": 2,
                "status": "OFFERED",
                "content_hash": "sha256:" + "d" * 64,
                "signature": bundle.signature.model_copy(
                    update={
                        "signer_practice_id": node_practice.id,
                        "key_id": derive_key_id(node_key.public_key()),
                    }
                ),
            }
        ),
        node_key,
    )
    # Node proposes the modification using the plain (already-trusted-by-
    # construction) propose_modification — trust-anchoring THIS step is not
    # what this test is about; accept_modification_trusted is. Verifying the
    # CURRENT (still origin-signed) content requires the origin's own key.
    harness.node_decision_service.propose_modification(
        bundle.id,
        modified,
        node_id,
        origin_key.public_key(),
        actor=node_id,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    acknowledged = harness.task_bundle_service.accept_modification_trusted(
        bundle.id,
        node_practice,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
        at=_NOW,
    )

    assert acknowledged.status == "OFFERED"
    assert acknowledged.content.revision == 2
    events = [
        e.event.event_type for e in harness.event_log.read_all() if e.event.object_id == bundle.id
    ]
    assert events == [
        "task_bundle.created",
        "task_bundle.offered",
        "task_bundle.modification_offered",
        "task_bundle.modification_acknowledged",
    ]


# ---------------------------------------------------------------------------
# Fail-closed matrix, each a distinct typed error, nothing decided/written.
# ---------------------------------------------------------------------------


def test_accept_trusted_signer_mismatch_fails_closed_nothing_written() -> None:
    harness = _harness()
    bundle, node_id, _origin_practice, _origin_key, _node_practice, _node_key = _wired_scenario(
        harness
    )
    _create_and_offer(harness, bundle)
    _, unrelated_public_key = generate_ed25519_keypair()
    wrong_practice = _practice(
        practice_id=new_urn("practice"), keys=[_key_entry(unrelated_public_key)]
    )

    with pytest.raises(TaskSignerMismatchError):
        harness.node_decision_service.accept_trusted(
            bundle.id,
            node_id,
            wrong_practice,
            actor=node_id,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
            at=_NOW,
        )

    latest = harness.object_repository.get_latest(bundle.id)
    assert latest.revision == 1
    events = [e for e in harness.event_log.read_all() if e.event.object_id == bundle.id]
    assert len(events) == 2  # created, offered — no accepted event recorded


def test_accept_trusted_unknown_kid_fails_closed_nothing_written() -> None:
    harness = _harness()
    bundle, node_id, origin_practice, _origin_key, _node_practice, _node_key = _wired_scenario(
        harness
    )
    # The origin practice's ring never actually held the kid the bundle was
    # signed with — a different, unrelated key under the same practice id.
    # Built via _practice() (full model_validate), not model_copy, because
    # _key_entry returns a plain dict and model_copy does not re-validate/
    # coerce nested fields the way model_validate does.
    _, unrelated_public_key = generate_ed25519_keypair()
    ring_without_the_signing_kid = _practice(
        practice_id=origin_practice.id, keys=[_key_entry(unrelated_public_key)]
    )
    _create_and_offer(harness, bundle)

    with pytest.raises(UnknownKeyIdError):
        harness.node_decision_service.accept_trusted(
            bundle.id,
            node_id,
            ring_without_the_signing_kid,
            actor=node_id,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
            at=_NOW,
        )

    latest = harness.object_repository.get_latest(bundle.id)
    assert latest.revision == 1
    events = [e for e in harness.event_log.read_all() if e.event.object_id == bundle.id]
    assert len(events) == 2


def test_accept_trusted_revoked_key_fails_closed_nothing_written() -> None:
    """docs/spec/04 section 8.4: valid at signing, revoked by the evaluation
    instant — still resolvable in the ring, but not currently valid.
    """
    harness = _harness()
    bundle, node_id, origin_practice, _origin_key, _node_practice, _node_key = _wired_scenario(
        harness
    )
    _create_and_offer(harness, bundle)
    revoked_practice = origin_practice.model_copy(
        update={"keys": [origin_practice.keys[0].model_copy(update={"state": "revoked"})]}
    )

    with pytest.raises(TaskKeyNotValidError):
        harness.node_decision_service.accept_trusted(
            bundle.id,
            node_id,
            revoked_practice,
            actor=node_id,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
            at=_NOW,
        )

    latest = harness.object_repository.get_latest(bundle.id)
    assert latest.revision == 1
    events = [e for e in harness.event_log.read_all() if e.event.object_id == bundle.id]
    assert len(events) == 2


def test_accept_trusted_tampered_bundle_fails_closed_nothing_written() -> None:
    """create()/offer() persist whatever content they are given verbatim —
    neither performs a signature check (that is deferred to the node's own
    decision) — so a bundle whose STORED content was tampered with after
    signing can be produced directly here, exactly like
    test_content_tampered_after_signing_is_rejected_on_node_side in
    test_service.py does via overwrite_latest_body_for_test.
    """
    harness = _harness()
    bundle, node_id, origin_practice, _origin_key, _node_practice, _node_key = _wired_scenario(
        harness
    )
    _create_and_offer(harness, bundle)
    stored = harness.object_repository.get_latest(bundle.id)
    tampered_body = dict(stored.body)
    tampered_body["purpose"] = "a completely different purpose the origin never signed"
    harness.object_repository.overwrite_latest_body_for_test(bundle.id, tampered_body)

    with pytest.raises(SignatureVerificationError):
        harness.node_decision_service.accept_trusted(
            bundle.id,
            node_id,
            origin_practice,
            actor=node_id,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
            at=_NOW,
        )

    events = [e for e in harness.event_log.read_all() if e.event.object_id == bundle.id]
    assert len(events) == 2  # created, offered — no accepted event recorded


def test_accept_trusted_key_substitution_attack_fails_closed() -> None:
    """A bundle signed by an ATTACKER's own key while claiming a kid that
    genuinely belongs to the trusted origin practice must fail closed with
    SignatureVerificationError — verification is always against the RING's
    resolved key, never a key the bundle itself claims.
    """
    harness = _harness()
    score = _approved_score()
    _seed_score(harness.object_repository, score)
    node_key = Ed25519PrivateKey.generate()
    node_id = new_urn("node")
    _register_node(harness.capability_registry, node_id, node_key)

    _trusted_private_key, trusted_public_key = generate_ed25519_keypair()
    trusted_entry = _key_entry(trusted_public_key)
    origin_practice = _practice(practice_id=new_urn("practice"), keys=[trusted_entry])

    attacker_private_key, _attacker_public_key = generate_ed25519_keypair()
    bundle = _bundle(
        origin_practice_id=origin_practice.id,
        target_node_id=node_id,
        research_score_id=score.id,
        signer_practice_id=origin_practice.id,
        key_id=trusted_entry["kid"],  # claims the TRUSTED kid ...
    )
    forged_bundle = _sign(bundle, attacker_private_key)  # ... but signs with the ATTACKER's key
    _create_and_offer(harness, forged_bundle)

    with pytest.raises(SignatureVerificationError):
        harness.node_decision_service.accept_trusted(
            forged_bundle.id,
            node_id,
            origin_practice,
            actor=node_id,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
            at=_NOW,
        )

    latest = harness.object_repository.get_latest(forged_bundle.id)
    assert latest.revision == 1
    events = [e for e in harness.event_log.read_all() if e.event.object_id == forged_bundle.id]
    assert len(events) == 2


# ---------------------------------------------------------------------------
# defer_trusted / reject_trusted / propose_modification_trusted: basic
# coverage, same trust-resolution wiring as accept_trusted.
# ---------------------------------------------------------------------------


def test_defer_trusted_happy_path() -> None:
    harness = _harness()
    bundle, node_id, origin_practice, _origin_key, _node_practice, _node_key = _wired_scenario(
        harness
    )
    _create_and_offer(harness, bundle)

    deferred = harness.node_decision_service.defer_trusted(
        bundle.id,
        node_id,
        origin_practice,
        actor=node_id,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
        at=_NOW,
    )

    assert deferred.status == "DEFERRED"


def test_reject_trusted_happy_path() -> None:
    harness = _harness()
    bundle, node_id, origin_practice, _origin_key, _node_practice, _node_key = _wired_scenario(
        harness
    )
    _create_and_offer(harness, bundle)

    rejected = harness.node_decision_service.reject_trusted(
        bundle.id,
        node_id,
        origin_practice,
        reason_category="policy_declined",
        actor=node_id,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
        at=_NOW,
    )

    assert rejected.status == "REJECTED"
    assert rejected.appended_event.event.payload["reason_category"] == "policy_declined"


def test_propose_modification_trusted_happy_path() -> None:
    harness = _harness()
    bundle, node_id, origin_practice, _origin_key, _node_practice, node_key = _wired_scenario(
        harness
    )
    _create_and_offer(harness, bundle)

    modified = _sign(
        bundle.model_copy(
            update={"revision": 2, "status": "OFFERED", "content_hash": "sha256:" + "e" * 64}
        ),
        node_key,
    )

    result = harness.node_decision_service.propose_modification_trusted(
        bundle.id,
        modified,
        node_id,
        origin_practice,
        actor=node_id,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
        at=_NOW,
    )

    assert result.body["revision"] == 2
    assert result.body["status"] == "OFFERED"


# ---------------------------------------------------------------------------
# Existing E2-T03 plain methods stay callable/unchanged (a lightweight
# smoke check; the full suite lives in test_service.py, unmodified).
# ---------------------------------------------------------------------------


def test_plain_accept_still_works_unaffected_by_the_trusted_wiring() -> None:
    harness = _harness()
    bundle, node_id, _origin_practice, origin_key, _node_practice, _node_key = _wired_scenario(
        harness
    )
    _create_and_offer(harness, bundle)

    accepted = harness.node_decision_service.accept(
        bundle.id,
        node_id,
        origin_key.public_key(),
        actor=node_id,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert accepted.status == "ACCEPTED"
