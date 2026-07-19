"""Integration tests for
``mrr.services.capability_registry.service.CapabilityRegistry``
(task-packets/E2-T02.yaml), run against a real PostgreSQL via the
``postgres_engine`` fixture in tests/integration/conftest.py — wired exactly
as production code would: ``PostgresObjectRepository``/``PostgresEventLog``
over the fixture's engine, with ``bind_unit_of_work`` closing over all
three. Skips visibly if ``MRR_TEST_DATABASE_URL`` is unset (fails hard
instead if ``CI=true``) — see that module's docstring.

Acceptance-test mapping (task-packets/E2-T02.yaml, integration tier):

- "registration writes exactly one event with complete provenance
  (integration, real PostgreSQL)" ->
  ``test_register_persists_one_revision_and_one_event_atomically``.
- "re-registration creates revision 2 with revision 1 intact" ->
  ``test_reregistration_persists_revision_2_and_leaves_revision_1_intact``.
- "a tampered manifest or wrong verifying key fails closed - nothing
  persisted" (rollback under a real transaction) ->
  ``test_invalid_signature_registration_persists_nothing``.
- "an expired or not-yet-valid manifest is stored but excluded from
  get-current and from capability match" ->
  ``test_expired_manifest_is_stored_but_excluded_from_lookup_and_match``.

``receive()`` acceptance-test mapping (task-packets/E5-T02.yaml, integration
tier — a real PostgreSQL proves the atomic write/rollback guarantee
``receive`` inherits from ``register`` by delegation, and that a rejection
persists nothing under a real transaction):

- trust-anchored acceptance persists one revision and one
  ``node_manifest.registered`` event, exactly like ``register`` itself ->
  ``test_receive_persists_one_revision_and_one_event_when_trust_anchored``.
- trust-resolution failure persists nothing and records exactly one
  ``node_manifest.rejected`` event with a coarse reason ->
  ``test_receive_persists_nothing_and_records_a_rejected_event_on_trust_failure``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.contracts import NodeManifest, Practice
from mrr.crypto.exceptions import SignatureVerificationError
from mrr.crypto.keys import derive_key_id, encode_public_key
from mrr.domain.exceptions import ManifestKeyNotValidError, NodeManifestValidityError
from mrr.domain.hashing_policy import sign_object
from mrr.domain.identity import new_urn
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.persistence.tables import domain_events_table, objects_table
from mrr.services.capability_registry.service import (
    CapabilityRegistry,
    bind_event_unit_of_work,
    bind_unit_of_work,
)
from sqlalchemy import Engine

_POLICY_VERSION = "policy-2026-07-01"


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


def _signed_manifest(private_key: Ed25519PrivateKey, **overrides: Any) -> NodeManifest:
    """Sign over the ``exclude_none=True`` form (ADR-0004,
    task-packets/E5-T00.yaml) — the same canonical body
    ``CapabilityRegistry.register`` verifies against.
    """
    manifest = _manifest(**overrides)
    signature_value = sign_object(
        private_key, json.loads(manifest.model_dump_json(exclude_none=True))
    )
    return manifest.model_copy(
        update={"signature": manifest.signature.model_copy(update={"value": signature_value})}
    )


def _registry_for(
    engine: Engine,
) -> tuple[CapabilityRegistry, PostgresObjectRepository, PostgresEventLog]:
    object_repository = PostgresObjectRepository(engine)
    event_log = PostgresEventLog(engine)
    record = bind_unit_of_work(engine, object_repository, event_log)
    registry = CapabilityRegistry(object_repository, event_log, record)
    return registry, object_repository, event_log


# ---------------------------------------------------------------------------
# Practice/KeyRing fixture factory for receive() (task-packets/E5-T02.yaml).
# ---------------------------------------------------------------------------


def _key_entry(
    private_key: Ed25519PrivateKey,
    *,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    state: str = "active",
) -> dict[str, Any]:
    now = datetime.now(UTC)
    public_key = private_key.public_key()
    return {
        "kid": derive_key_id(public_key),
        "algorithm": "Ed25519",
        "encoded_public_key": encode_public_key(public_key),
        "valid_from": valid_from or now - timedelta(days=1),
        "valid_until": valid_until or now + timedelta(days=365),
        "state": state,
    }


def _practice(*, practice_id: str, keys: list[dict[str, Any]]) -> Practice:
    now = datetime.now(UTC)
    data: dict[str, Any] = {
        "id": practice_id,
        "api_version": "mrr/v1alpha1",
        "kind": "Practice",
        "practice_id": practice_id,
        "revision": 1,
        "created_at": now,
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "name": "Fixture Sender Practice",
        "description": "Fixture practice trusted as manifest sender in receive() tests.",
        "keys": keys,
        "governance_contacts": ["mailto:governance@fixture.invalid"],
        "supported_policy_versions": ["policy-2026-07-01"],
        "disclosure": {"max_disclosure": "PUBLIC", "trust_statement": "fixture"},
    }
    return Practice.model_validate(data)


def _trusted_scenario(
    *,
    node_id: str | None = None,
    key_state: str = "active",
) -> tuple[NodeManifest, Practice, Ed25519PrivateKey]:
    private_key = Ed25519PrivateKey.generate()
    practice_id = new_urn("practice")
    entry = _key_entry(private_key, state=key_state)
    practice = _practice(practice_id=practice_id, keys=[entry])
    manifest = _signed_manifest(
        private_key,
        node_id=node_id,
        practice_id=practice_id,
        public_keys=[entry["encoded_public_key"]],
        signature={
            "signer_practice_id": practice_id,
            "key_id": entry["kid"],
            "algorithm": "Ed25519",
            "signed_at": datetime.now(UTC),
            "value": "0" * 44,
        },
    )
    return manifest, practice, private_key


def test_register_persists_one_revision_and_one_event_atomically(
    postgres_engine: Engine,
) -> None:
    registry, object_repository, event_log = _registry_for(postgres_engine)
    private_key = Ed25519PrivateKey.generate()
    node_id = new_urn("node")
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")

    stored = registry.register(
        _signed_manifest(private_key, node_id=node_id),
        private_key.public_key(),
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    assert stored.revision == 1
    assert stored.id == node_id

    # Assert straight from the database, not just through the repository
    # abstraction — the whole point of the atomic unit-of-work invariant.
    with postgres_engine.connect() as conn:
        object_rows = conn.execute(
            sa.select(objects_table).where(objects_table.c.id == node_id)
        ).fetchall()
        event_rows = conn.execute(
            sa.select(domain_events_table).where(domain_events_table.c.object_id == node_id)
        ).fetchall()

    assert len(object_rows) == 1
    assert len(event_rows) == 1
    assert event_rows[0].event_type == "node_manifest.registered"
    assert event_rows[0].actor == actor
    assert event_rows[0].policy_version == _POLICY_VERSION
    assert event_rows[0].correlation_id == correlation_id
    assert event_rows[0].object_revision == 1
    assert event_rows[0].causation_id is None

    assert registry.get_current_manifest(node_id).revision == 1
    assert registry.find_nodes_with_capability("statistics.recompute") == [node_id]


def test_reregistration_persists_revision_2_and_leaves_revision_1_intact(
    postgres_engine: Engine,
) -> None:
    registry, object_repository, event_log = _registry_for(postgres_engine)
    private_key = Ed25519PrivateKey.generate()
    node_id = new_urn("node")
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")

    registry.register(
        _signed_manifest(private_key, node_id=node_id),
        private_key.public_key(),
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )
    stored = registry.register(
        _signed_manifest(
            private_key,
            node_id=node_id,
            revision=2,
            restrictions=["no_raw_personal_data_export"],
        ),
        private_key.public_key(),
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    assert stored.revision == 2
    rev1 = object_repository.get_revision(node_id, 1)
    rev2 = object_repository.get_revision(node_id, 2)
    assert rev1.body["restrictions"] == []
    assert rev2.body["restrictions"] == ["no_raw_personal_data_export"]

    events = [appended for appended in event_log.read_all() if appended.event.object_id == node_id]
    assert len(events) == 2
    assert events[0].event.causation_id is None
    assert events[1].event.causation_id == events[0].event.id


def test_invalid_signature_registration_persists_nothing(postgres_engine: Engine) -> None:
    registry, object_repository, event_log = _registry_for(postgres_engine)
    private_key = Ed25519PrivateKey.generate()
    node_id = new_urn("node")
    manifest = _signed_manifest(private_key, node_id=node_id)
    tampered = manifest.model_copy(update={"restrictions": ["tampered_after_signing"]})

    with pytest.raises(SignatureVerificationError):
        registry.register(
            tampered,
            private_key.public_key(),
            actor=new_urn("agent-role"),
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )

    with postgres_engine.connect() as conn:
        object_rows = conn.execute(
            sa.select(objects_table).where(objects_table.c.id == node_id)
        ).fetchall()
        event_rows = conn.execute(
            sa.select(domain_events_table).where(domain_events_table.c.object_id == node_id)
        ).fetchall()
    assert object_rows == []
    assert event_rows == []


def test_expired_manifest_is_stored_but_excluded_from_lookup_and_match(
    postgres_engine: Engine,
) -> None:
    registry, object_repository, _ = _registry_for(postgres_engine)
    private_key = Ed25519PrivateKey.generate()
    node_id = new_urn("node")
    now = datetime.now(UTC)

    registry.register(
        _signed_manifest(
            private_key,
            node_id=node_id,
            valid_from=now - timedelta(days=10),
            valid_until=now - timedelta(days=1),
        ),
        private_key.public_key(),
        actor=new_urn("agent-role"),
        policy_version=_POLICY_VERSION,
        correlation_id=new_urn("research-run"),
    )

    # Stored and historically addressable ...
    assert object_repository.get_latest(node_id).revision == 1
    # ... but excluded from both lookup and match.
    with pytest.raises(NodeManifestValidityError):
        registry.get_current_manifest(node_id)
    assert registry.find_nodes_with_capability("statistics.recompute") == []


# ---------------------------------------------------------------------------
# receive(): trust-anchored acceptance, against a real PostgreSQL
# (task-packets/E5-T02.yaml).
# ---------------------------------------------------------------------------


def test_receive_persists_one_revision_and_one_event_when_trust_anchored(
    postgres_engine: Engine,
) -> None:
    registry, object_repository, event_log = _registry_for(postgres_engine)
    record_event = bind_event_unit_of_work(postgres_engine, event_log)
    node_id = new_urn("node")
    manifest, practice, _ = _trusted_scenario(node_id=node_id)
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")

    stored = registry.receive(
        manifest,
        practice,
        record_event,
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    assert stored.revision == 1
    assert stored.id == node_id

    with postgres_engine.connect() as conn:
        object_rows = conn.execute(
            sa.select(objects_table).where(objects_table.c.id == node_id)
        ).fetchall()
        event_rows = conn.execute(
            sa.select(domain_events_table).where(domain_events_table.c.object_id == node_id)
        ).fetchall()

    assert len(object_rows) == 1
    assert len(event_rows) == 1
    assert event_rows[0].event_type == "node_manifest.registered"
    assert registry.get_current_manifest(node_id).revision == 1


def test_receive_persists_nothing_and_records_a_rejected_event_on_trust_failure(
    postgres_engine: Engine,
) -> None:
    """A revoked resolving key fails closed under a real transaction: no
    object revision is ever written, and exactly one ``node_manifest.rejected``
    event is recorded carrying only a coarse reason category.
    """
    registry, object_repository, event_log = _registry_for(postgres_engine)
    record_event = bind_event_unit_of_work(postgres_engine, event_log)
    node_id = new_urn("node")
    manifest, practice, _ = _trusted_scenario(node_id=node_id, key_state="revoked")
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")

    with pytest.raises(ManifestKeyNotValidError):
        registry.receive(
            manifest,
            practice,
            record_event,
            actor=actor,
            policy_version=_POLICY_VERSION,
            correlation_id=correlation_id,
        )

    with postgres_engine.connect() as conn:
        object_rows = conn.execute(
            sa.select(objects_table).where(objects_table.c.id == node_id)
        ).fetchall()
        event_rows = conn.execute(
            sa.select(domain_events_table).where(domain_events_table.c.object_id == node_id)
        ).fetchall()

    assert object_rows == []
    assert len(event_rows) == 1
    assert event_rows[0].event_type == "node_manifest.rejected"
    assert event_rows[0].payload == {"reason_category": "key_not_valid"}
