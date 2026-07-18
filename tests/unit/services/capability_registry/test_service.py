"""Unit tests for
``mrr.services.capability_registry.service.CapabilityRegistry``
(task-packets/E2-T02.yaml), run entirely DB-free against in-memory fakes of
``mrr.domain.repositories.ObjectRepository`` and the event-log read surface
the service depends on — no PostgreSQL, no ``sqlalchemy.Engine``. Same
"lightweight fake unit-of-work" pattern
``tests/unit/services/research_score/test_service.py`` uses (the fakes below
are a local, deliberate duplicate — see that module's own docstring for why
duplicating rather than importing test doubles across test modules is the
established convention here).

Acceptance-test mapping (task-packets/E2-T02.yaml):

- "a validly signed, in-window manifest registers and is resolvable by
  node_id" -> ``test_register_accepts_a_validly_signed_manifest``.
- "a tampered manifest or wrong verifying key fails closed - nothing
  persisted, typed error raised" ->
  ``test_register_tampered_manifest_fails_closed_and_persists_nothing``,
  ``test_register_wrong_verifying_key_fails_closed_and_persists_nothing``.
- "an expired or not-yet-valid manifest is stored but excluded from
  get-current and from capability match" ->
  ``test_register_accepts_out_of_window_manifest_but_get_current_excludes_it``,
  ``test_get_current_manifest_raises_for_not_yet_valid_manifest``,
  ``test_find_nodes_with_capability_excludes_expired_and_not_yet_valid_nodes``.
- "match by capability name returns exactly the currently valid nodes
  declaring it, and never a permission verdict" ->
  ``test_find_nodes_with_capability_returns_only_matching_in_window_nodes``,
  ``test_find_nodes_with_capability_is_a_match_not_an_authorization_decision``.
- "re-registration creates revision 2 with revision 1 intact" ->
  ``test_reregistration_creates_revision_2_and_leaves_revision_1_intact``.
- "registration writes exactly one event with complete provenance" ->
  ``test_register_event_carries_complete_provenance``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.contracts import NodeManifest
from mrr.crypto.exceptions import SignatureVerificationError
from mrr.domain.exceptions import (
    NodeManifestNotFoundError,
    NodeManifestValidityError,
    ObjectNotFoundError,
    RevisionConflictError,
)
from mrr.domain.hashing_policy import sign_object
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.capability_registry.service import CapabilityRegistry, RecordRevisionWithEvent

# ---------------------------------------------------------------------------
# In-memory fakes (ObjectRepository protocol conformance + a minimal event
# journal), and a fake "unit of work" combining them. Deliberate local
# duplicate of tests/unit/services/research_score/test_service.py's own
# fakes — see this module's docstring.
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
    service depends on (``CapabilityRegistry._EventJournal``).
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


def _registry() -> tuple[CapabilityRegistry, FakeObjectRepository, FakeEventLog]:
    object_repository = FakeObjectRepository()
    event_log = FakeEventLog()
    registry = CapabilityRegistry(
        object_repository, event_log, _fake_record(object_repository, event_log)
    )
    return registry, object_repository, event_log


# ---------------------------------------------------------------------------
# NodeManifest fixture factory and signing helper.
# ---------------------------------------------------------------------------

_ACTOR = new_urn("agent-role")
_POLICY_VERSION = "policy-2026-07-01"


def _correlation_id() -> str:
    return new_urn("research-run")


def _manifest(*, node_id: str | None = None, **overrides: Any) -> NodeManifest:
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
        "node_id": node_id or new_urn("node"),
        "capabilities": [
            {
                "name": "statistics.recompute",
                "version": "1.0.0",
                "input_schema": "urn:mrr:schema:numeric-check:1",
                "output_schema": "urn:mrr:schema:evidence-crate:1",
                "max_autonomy": "A2",
                "approval": "automatic",
                "network_profile": "none",
            }
        ],
        "restrictions": [],
        "accepted_classifications": ["PUBLIC"],
        "data_residency": "DE",
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


def _sign(manifest: NodeManifest, private_key: Ed25519PrivateKey) -> NodeManifest:
    """Sign ``manifest`` for real with ``private_key`` (E1-T02
    ``sign_object``) and return a copy carrying the resulting signature
    value — the same canonical-bytes construction
    ``CapabilityRegistry.register`` uses to verify
    (``manifest.model_dump(mode="json")``), so a correctly signed fixture
    round-trips through ``register`` successfully.
    """
    signature_value = sign_object(private_key, manifest.model_dump(mode="json"))
    return manifest.model_copy(
        update={"signature": manifest.signature.model_copy(update={"value": signature_value})}
    )


def _signed_manifest(private_key: Ed25519PrivateKey, **overrides: Any) -> NodeManifest:
    return _sign(_manifest(**overrides), private_key)


# ---------------------------------------------------------------------------
# register(): signature verification (fail closed) and persistence.
# ---------------------------------------------------------------------------


def test_register_accepts_a_validly_signed_manifest() -> None:
    registry, object_repository, event_log = _registry()
    private_key = Ed25519PrivateKey.generate()
    manifest = _signed_manifest(private_key)

    stored = registry.register(
        manifest,
        private_key.public_key(),
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert stored.id == manifest.node_id
    assert stored.revision == 1
    assert object_repository.get_latest(manifest.node_id).id == manifest.node_id
    events = event_log.read_all()
    assert len(events) == 1
    assert events[0].event.event_type == "node_manifest.registered"


def test_register_tampered_manifest_fails_closed_and_persists_nothing() -> None:
    registry, object_repository, event_log = _registry()
    private_key = Ed25519PrivateKey.generate()
    manifest = _signed_manifest(private_key)

    # Tamper a field covered by the signature AFTER signing — the signature
    # no longer verifies against the (now different) canonical payload.
    tampered = manifest.model_copy(update={"restrictions": ["a_new_restriction_not_signed"]})

    with pytest.raises(SignatureVerificationError):
        registry.register(
            tampered,
            private_key.public_key(),
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )

    with pytest.raises(ObjectNotFoundError):
        object_repository.get_latest(manifest.node_id)
    assert event_log.read_all() == []


def test_register_wrong_verifying_key_fails_closed_and_persists_nothing() -> None:
    registry, object_repository, event_log = _registry()
    signing_key = Ed25519PrivateKey.generate()
    wrong_key = Ed25519PrivateKey.generate()
    manifest = _signed_manifest(signing_key)

    with pytest.raises(SignatureVerificationError):
        registry.register(
            manifest,
            wrong_key.public_key(),  # not the key that signed this manifest
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )

    with pytest.raises(ObjectNotFoundError):
        object_repository.get_latest(manifest.node_id)
    assert event_log.read_all() == []


def test_register_rejects_wrong_revision_number() -> None:
    registry, object_repository, event_log = _registry()
    private_key = Ed25519PrivateKey.generate()
    manifest = _signed_manifest(private_key, revision=2)  # no prior manifest -> must be 1

    with pytest.raises(ValueError, match="revision must be 1"):
        registry.register(
            manifest,
            private_key.public_key(),
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )
    assert event_log.read_all() == []


# ---------------------------------------------------------------------------
# Re-registration: append-only revisions.
# ---------------------------------------------------------------------------


def test_reregistration_creates_revision_2_and_leaves_revision_1_intact() -> None:
    registry, object_repository, event_log = _registry()
    private_key = Ed25519PrivateKey.generate()
    node_id = new_urn("node")
    first = _signed_manifest(private_key, node_id=node_id)

    registry.register(
        first,
        private_key.public_key(),
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    second = _signed_manifest(
        private_key,
        node_id=node_id,
        revision=2,
        restrictions=["no_raw_personal_data_export"],
    )
    stored = registry.register(
        second,
        private_key.public_key(),
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert stored.revision == 2
    rev1 = object_repository.get_revision(node_id, 1)
    rev2 = object_repository.get_revision(node_id, 2)
    assert rev1.body["restrictions"] == []
    assert rev2.body["restrictions"] == ["no_raw_personal_data_export"]
    events = event_log.read_all()
    assert len(events) == 2
    assert [appended.event.event_type for appended in events] == [
        "node_manifest.registered",
        "node_manifest.registered",
    ]


def test_register_event_carries_complete_provenance() -> None:
    registry, _, event_log = _registry()
    private_key = Ed25519PrivateKey.generate()
    node_id = new_urn("node")
    correlation_id = _correlation_id()

    registry.register(
        _signed_manifest(private_key, node_id=node_id),
        private_key.public_key(),
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )
    registry.register(
        _signed_manifest(private_key, node_id=node_id, revision=2),
        private_key.public_key(),
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    events = [appended.event for appended in event_log.read_all()]
    assert len(events) == 2
    first_event, second_event = events

    assert first_event.causation_id is None
    assert second_event.causation_id == first_event.id  # real causal chain

    for event in (first_event, second_event):
        assert event.actor == _ACTOR
        assert event.policy_version == _POLICY_VERSION
        assert event.correlation_id == correlation_id
        assert event.object_id == node_id
        assert event.occurred_at.tzinfo is not None

    assert first_event.object_revision == 1
    assert second_event.object_revision == 2


# ---------------------------------------------------------------------------
# get_current_manifest(): temporal validity.
# ---------------------------------------------------------------------------


def test_get_current_manifest_raises_not_found_for_unknown_node() -> None:
    registry, _, _ = _registry()

    with pytest.raises(NodeManifestNotFoundError) as excinfo:
        registry.get_current_manifest(new_urn("node"))
    assert excinfo.value.node_id


def test_register_accepts_out_of_window_manifest_but_get_current_excludes_it() -> None:
    """Registration succeeds regardless of temporal validity — only lookup
    and match are gated on the window (task-packets/E2-T02.yaml invariant:
    "... though it remains stored and historically addressable").
    """
    registry, _, _ = _registry()
    private_key = Ed25519PrivateKey.generate()
    now = datetime.now(UTC)
    node_id = new_urn("node")
    expired = _signed_manifest(
        private_key,
        node_id=node_id,
        valid_from=now - timedelta(days=10),
        valid_until=now - timedelta(days=1),
    )

    stored = registry.register(
        expired,
        private_key.public_key(),
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    assert stored.revision == 1  # persisted despite being expired

    with pytest.raises(NodeManifestValidityError) as excinfo:
        registry.get_current_manifest(node_id)
    assert excinfo.value.node_id == node_id
    assert "expired" in str(excinfo.value)


def test_get_current_manifest_raises_for_not_yet_valid_manifest() -> None:
    registry, _, _ = _registry()
    private_key = Ed25519PrivateKey.generate()
    now = datetime.now(UTC)
    node_id = new_urn("node")
    not_yet_valid = _signed_manifest(
        private_key,
        node_id=node_id,
        valid_from=now + timedelta(days=1),
        valid_until=now + timedelta(days=10),
    )
    registry.register(
        not_yet_valid,
        private_key.public_key(),
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    with pytest.raises(NodeManifestValidityError) as excinfo:
        registry.get_current_manifest(node_id)
    assert "not yet valid" in str(excinfo.value)


def test_get_current_manifest_accepts_an_in_window_manifest() -> None:
    registry, _, _ = _registry()
    private_key = Ed25519PrivateKey.generate()
    node_id = new_urn("node")
    manifest = _signed_manifest(private_key, node_id=node_id)
    registry.register(
        manifest,
        private_key.public_key(),
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    stored = registry.get_current_manifest(node_id)
    assert stored.id == node_id


def test_list_capabilities_returns_current_manifest_capability_names() -> None:
    registry, _, _ = _registry()
    private_key = Ed25519PrivateKey.generate()
    node_id = new_urn("node")
    registry.register(
        _signed_manifest(private_key, node_id=node_id),
        private_key.public_key(),
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert registry.list_capabilities(node_id) == ["statistics.recompute"]


# ---------------------------------------------------------------------------
# find_nodes_with_capability(): matching, not authorization.
# ---------------------------------------------------------------------------


def test_find_nodes_with_capability_returns_only_matching_in_window_nodes() -> None:
    registry, _, _ = _registry()
    private_key = Ed25519PrivateKey.generate()

    declaring_node = new_urn("node")
    other_node = new_urn("node")
    registry.register(
        _signed_manifest(private_key, node_id=declaring_node),
        private_key.public_key(),
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    registry.register(
        _signed_manifest(
            private_key,
            node_id=other_node,
            capabilities=[
                {
                    "name": "literature.retrieve",
                    "version": "1.0.0",
                    "input_schema": "urn:mrr:schema:literature-query:1",
                    "output_schema": "urn:mrr:schema:evidence-crate:1",
                    "max_autonomy": "A1",
                    "approval": "human",
                    "network_profile": "allowlist",
                }
            ],
        ),
        private_key.public_key(),
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    matches = registry.find_nodes_with_capability("statistics.recompute")
    assert matches == [declaring_node]

    other_matches = registry.find_nodes_with_capability("literature.retrieve")
    assert other_matches == [other_node]

    assert registry.find_nodes_with_capability("no-such.capability") == []


def test_find_nodes_with_capability_excludes_expired_and_not_yet_valid_nodes() -> None:
    registry, _, _ = _registry()
    private_key = Ed25519PrivateKey.generate()
    now = datetime.now(UTC)

    valid_node = new_urn("node")
    expired_node = new_urn("node")
    not_yet_valid_node = new_urn("node")

    registry.register(
        _signed_manifest(private_key, node_id=valid_node),
        private_key.public_key(),
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    registry.register(
        _signed_manifest(
            private_key,
            node_id=expired_node,
            valid_from=now - timedelta(days=10),
            valid_until=now - timedelta(days=1),
        ),
        private_key.public_key(),
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    registry.register(
        _signed_manifest(
            private_key,
            node_id=not_yet_valid_node,
            valid_from=now + timedelta(days=1),
            valid_until=now + timedelta(days=10),
        ),
        private_key.public_key(),
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert registry.find_nodes_with_capability("statistics.recompute") == [valid_node]


def test_find_nodes_with_capability_is_a_match_not_an_authorization_decision() -> None:
    """A node requiring human approval and a restrictive network profile is
    still returned — find_nodes_with_capability only matches capability
    name and temporal validity, docs/spec/01_SYSTEM_SPEC.md section 7.3:
    "It does not grant permission". The caller, not this method, is
    responsible for any subsequent policy/approval decision.
    """
    registry, _, _ = _registry()
    private_key = Ed25519PrivateKey.generate()
    node_id = new_urn("node")
    registry.register(
        _signed_manifest(
            private_key,
            node_id=node_id,
            capabilities=[
                {
                    "name": "restricted.action",
                    "version": "1.0.0",
                    "input_schema": "urn:mrr:schema:x:1",
                    "output_schema": "urn:mrr:schema:y:1",
                    "max_autonomy": "A0",
                    "approval": "dual",
                    "network_profile": "unrestricted_forbidden",
                }
            ],
        ),
        private_key.public_key(),
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    result = registry.find_nodes_with_capability("restricted.action")

    assert result == [node_id]
    assert all(isinstance(item, str) for item in result)  # node ids, never a verdict


def test_find_nodes_with_capability_ignores_events_from_other_object_kinds() -> None:
    """The event-log-driven node-discovery step (see the service module
    docstring) filters strictly on event_type == "node_manifest.registered"
    — an unrelated event for some other object must not be misread as
    naming a node id to resolve.
    """
    registry, object_repository, event_log = _registry()
    private_key = Ed25519PrivateKey.generate()

    # An event for a completely unrelated object/kind, predating any real
    # registration.
    unrelated_event = DomainEvent(
        id=new_urn("domain-event"),
        event_type="research_score.created",
        occurred_at=datetime.now(UTC),
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        causation_id=None,
        correlation_id=_correlation_id(),
        object_id=new_urn("research-score"),
        object_revision=1,
        payload={},
    )
    event_log.append_for_test(unrelated_event)

    node_id = new_urn("node")
    registry.register(
        _signed_manifest(private_key, node_id=node_id),
        private_key.public_key(),
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert registry.find_nodes_with_capability("statistics.recompute") == [node_id]
    # Sanity: the unrelated object id was never mistaken for a node id.
    with pytest.raises(ObjectNotFoundError):
        object_repository.get_latest(unrelated_event.object_id)


# ---------------------------------------------------------------------------
# Canonical-bytes correctness: a manifest with a None-valued optional field
# still signs and verifies correctly (task-packets/E2-T02.yaml design point
# 2), even though the persisted body and the verification input serialize
# that field differently (see _manifest_to_stored_object's docstring).
# ---------------------------------------------------------------------------


def test_register_round_trips_a_manifest_with_an_unset_optional_field() -> None:
    """``data_residency`` is optional and non-nullable per the schema.
    ``register()`` verifies against ``manifest.model_dump(mode="json")``,
    which serializes an unset ``data_residency`` as explicit JSON ``null``
    — this must still canonicalize, sign, and verify correctly (RFC 8785
    handles JSON null fine), and the persisted ``body`` (built with
    ``exclude_none=True``) must drop the field entirely rather than storing
    it as ``null``, so a later schema validation of the stored body would
    still pass.
    """
    registry, object_repository, _ = _registry()
    private_key = Ed25519PrivateKey.generate()
    node_id = new_urn("node")
    manifest = _signed_manifest(private_key, node_id=node_id, data_residency=None)
    assert manifest.model_dump(mode="json")["data_residency"] is None  # explicit null, pre-verify

    registry.register(
        manifest,
        private_key.public_key(),
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    stored_body = object_repository.get_latest(node_id).body
    assert "data_residency" not in stored_body  # absent, not null — stays schema-valid
