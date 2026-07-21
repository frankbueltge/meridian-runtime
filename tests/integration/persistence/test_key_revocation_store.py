"""Integration tests for mrr.persistence.repositories.
PostgresKeyRevocationStore (task-packets/E5-T07b.yaml), run via the
``postgres_engine`` fixture in tests/integration/conftest.py. Skips visibly
if MRR_TEST_DATABASE_URL is unset (fails hard instead if CI=true) — see that
module's docstring.

Acceptance-test mapping:

- "alembic upgrade head creates the key_revocations table" ->
  ``test_alembic_upgrade_head_creates_key_revocations_table`` (every other
  test in this module also exercises this, via the fixture, on every run).
- "record_revocation then get_revocation returns a RevocationRecord whose
  kid/practice_id/revoked_at/reason match exactly what was recorded; a kid
  never recorded returns None from get_revocation" ->
  ``test_record_then_get_revocation_returns_matching_record``,
  ``test_unrecorded_kid_returns_none``.
- "recording the SAME kid twice with DIFFERENT revoked_at/reason values
  leaves exactly one row, returns False (not an error) on the second call,
  and get_revocation still returns the FIRST call's original values
  unchanged" -> ``test_duplicate_record_leaves_original_values_unchanged``.
- "restart durability: record through one Engine/connection, then open a
  genuinely SEPARATE sqlalchemy.Engine against the identical
  database/schema ... still returns the identical RevocationRecord" ->
  ``test_revocation_survives_a_separate_engine_against_the_same_schema``.
- "record_revocation_with_connection participates in a caller's
  transaction — a rollback after the call leaves no row" ->
  ``test_record_revocation_with_connection_rollback_leaves_no_row``.
- "composition with E5-T07, end to end: accept and record-as-processed a
  real signed NodeMessageEnvelope; separately record_revocation for its
  signing kid with a revoked_at strictly after the envelope's own
  signature.signed_at; assert trust_revoked_after_creation is True, and
  resubmitting the SAME envelope is still rejected by
  EnvelopeAlreadyProcessedError specifically" ->
  ``test_composition_with_processed_id_store_end_to_end``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from mrr.contracts.node_message_envelope import NodeMessageEnvelope
from mrr.contracts.practice import Practice
from mrr.crypto.keys import derive_key_id, encode_public_key, generate_ed25519_keypair
from mrr.domain.envelope_validation import validate_inbound_envelope
from mrr.domain.exceptions import EnvelopeAlreadyProcessedError
from mrr.domain.hashing_policy import sign_object
from mrr.domain.identity import new_urn
from mrr.domain.manifest_trust import practice_key_ring
from mrr.domain.trust_revocation import trust_revoked_after_creation
from mrr.persistence.repositories import PostgresKeyRevocationStore, PostgresProcessedIdStore
from mrr.persistence.tables import key_revocations_table
from sqlalchemy import Engine, inspect

_NOW = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
_VALID_FROM = _NOW - timedelta(days=1)
_VALID_UNTIL = _NOW + timedelta(days=365)


# ---------------------------------------------------------------------------
# Fixture builders (deliberately local — mirrors
# tests/integration/persistence/test_processed_id_store.py's own precedent).
# ---------------------------------------------------------------------------


def _key_entry(
    public_key: Ed25519PublicKey,
    *,
    valid_from: datetime = _VALID_FROM,
    valid_until: datetime = _VALID_UNTIL,
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
        "description": "Fixture practice for the key-revocation store integration tests.",
        "keys": keys,
        "governance_contacts": ["mailto:governance@fixture.invalid"],
        "supported_policy_versions": ["policy-2026-07-01"],
        "disclosure": {"max_disclosure": "PUBLIC", "trust_statement": "fixture"},
    }
    return Practice.model_validate(data)


def _envelope(
    *,
    sender_practice_id: str,
    key_id: str,
    recipient_node_id: str,
    sent_at: datetime = _NOW - timedelta(minutes=1),
    expires_at: datetime = _NOW + timedelta(minutes=5),
    signed_at: datetime = _NOW,
) -> NodeMessageEnvelope:
    payload_content_hash = "sha256:" + "c" * 64
    data: dict[str, Any] = {
        "message_id": new_urn("node-message-envelope"),
        "sender_node_id": new_urn("node"),
        "sender_practice_id": sender_practice_id,
        "recipient_node_id": recipient_node_id,
        "sent_at": sent_at,
        "expires_at": expires_at,
        "payload_kind": "TaskBundle",
        "payload_content_hash": payload_content_hash,
        "payload": {"kind": "TaskBundle", "content_hash": payload_content_hash},
        "signature": {
            "signer_practice_id": sender_practice_id,
            "key_id": key_id,
            "algorithm": "Ed25519",
            "signed_at": signed_at,
            "value": "0" * 44,
        },
    }
    return NodeMessageEnvelope.model_validate(data)


def _sign_envelope(
    envelope: NodeMessageEnvelope, private_key: Ed25519PrivateKey
) -> NodeMessageEnvelope:
    signature_value = sign_object(
        private_key, json.loads(envelope.model_dump_json(exclude_none=True))
    )
    return envelope.model_copy(
        update={"signature": envelope.signature.model_copy(update={"value": signature_value})}
    )


def _trusted_envelope_scenario(
    *,
    sent_at: datetime = _NOW - timedelta(minutes=1),
    expires_at: datetime = _NOW + timedelta(minutes=5),
    signed_at: datetime = _NOW,
) -> tuple[NodeMessageEnvelope, Practice, str]:
    private_key, public_key = generate_ed25519_keypair()
    practice_id = new_urn("practice")
    this_node_id = new_urn("node")
    entry = _key_entry(public_key)
    practice = _practice(practice_id=practice_id, keys=[entry])
    envelope = _envelope(
        sender_practice_id=practice_id,
        key_id=entry["kid"],
        recipient_node_id=this_node_id,
        sent_at=sent_at,
        expires_at=expires_at,
        signed_at=signed_at,
    )
    return _sign_envelope(envelope, private_key), practice, this_node_id


# ---------------------------------------------------------------------------
# alembic upgrade head
# ---------------------------------------------------------------------------


def test_alembic_upgrade_head_creates_key_revocations_table(postgres_engine: Engine) -> None:
    inspector = inspect(postgres_engine)
    assert "key_revocations" in set(inspector.get_table_names())


# ---------------------------------------------------------------------------
# record_revocation / get_revocation: idempotency and immutability.
# ---------------------------------------------------------------------------


def test_record_then_get_revocation_returns_matching_record(postgres_engine: Engine) -> None:
    store = PostgresKeyRevocationStore(postgres_engine)
    _, public_key = generate_ed25519_keypair()
    kid = derive_key_id(public_key)
    practice_id = new_urn("practice")

    newly_recorded = store.record_revocation(
        kid, practice_id=practice_id, revoked_at=_NOW, reason="key compromise reported"
    )

    assert newly_recorded is True
    record = store.get_revocation(kid)
    assert record is not None
    assert record.kid == kid
    assert record.practice_id == practice_id
    assert record.revoked_at == _NOW
    assert record.reason == "key compromise reported"


def test_unrecorded_kid_returns_none(postgres_engine: Engine) -> None:
    store = PostgresKeyRevocationStore(postgres_engine)
    _, public_key = generate_ed25519_keypair()
    never_recorded_kid = derive_key_id(public_key)

    assert store.get_revocation(never_recorded_kid) is None


def test_duplicate_record_leaves_original_values_unchanged(postgres_engine: Engine) -> None:
    store = PostgresKeyRevocationStore(postgres_engine)
    _, public_key = generate_ed25519_keypair()
    kid = derive_key_id(public_key)
    first_practice_id = new_urn("practice")
    second_practice_id = new_urn("practice")

    first = store.record_revocation(
        kid, practice_id=first_practice_id, revoked_at=_NOW, reason="first reason"
    )
    second = store.record_revocation(
        kid,
        practice_id=second_practice_id,
        revoked_at=_NOW + timedelta(days=1),
        reason="second, different reason",
    )

    assert first is True
    assert second is False  # idempotent no-op, not an error

    record = store.get_revocation(kid)
    assert record is not None
    assert record.practice_id == first_practice_id
    assert record.revoked_at == _NOW
    assert record.reason == "first reason"

    with postgres_engine.connect() as conn:
        row_count = conn.execute(
            sa.select(sa.func.count())
            .select_from(key_revocations_table)
            .where(key_revocations_table.c.kid == kid)
        ).scalar_one()
    assert row_count == 1


# ---------------------------------------------------------------------------
# Restart durability: a genuinely separate Engine sees the same fact.
# ---------------------------------------------------------------------------


def test_revocation_survives_a_separate_engine_against_the_same_schema(
    postgres_engine: Engine,
) -> None:
    store = PostgresKeyRevocationStore(postgres_engine)
    _, public_key = generate_ed25519_keypair()
    kid = derive_key_id(public_key)
    practice_id = new_urn("practice")

    store.record_revocation(kid, practice_id=practice_id, revoked_at=_NOW, reason="restart test")

    # A genuinely separate sqlalchemy.Engine — not the same Python object,
    # no shared connection pool or cache — against the identical
    # database/schema (the search_path option is carried in postgres_engine's
    # own URL, so building a new Engine from that same URL resolves to the
    # same schema).
    second_engine = sa.create_engine(postgres_engine.url)
    try:
        second_store = PostgresKeyRevocationStore(second_engine)
        record = second_store.get_revocation(kid)
    finally:
        second_engine.dispose()

    assert record is not None
    assert record.kid == kid
    assert record.practice_id == practice_id
    assert record.revoked_at == _NOW
    assert record.reason == "restart test"


# ---------------------------------------------------------------------------
# record_revocation_with_connection: participates in a caller transaction.
# ---------------------------------------------------------------------------


def test_record_revocation_with_connection_rollback_leaves_no_row(
    postgres_engine: Engine,
) -> None:
    store = PostgresKeyRevocationStore(postgres_engine)
    _, public_key = generate_ed25519_keypair()
    kid = derive_key_id(public_key)
    practice_id = new_urn("practice")

    with (
        pytest.raises(RuntimeError, match="injected failure after record, before commit"),
        postgres_engine.begin() as conn,
    ):
        store.record_revocation_with_connection(conn, kid, practice_id=practice_id, revoked_at=_NOW)
        raise RuntimeError("injected failure after record, before commit")

    assert store.get_revocation(kid) is None
    with postgres_engine.connect() as conn:
        row_count = conn.execute(
            sa.select(sa.func.count())
            .select_from(key_revocations_table)
            .where(key_revocations_table.c.kid == kid)
        ).scalar_one()
    assert row_count == 0


# ---------------------------------------------------------------------------
# Composition with E5-T07's replay store: no competing rejection reason.
# ---------------------------------------------------------------------------


def test_composition_with_processed_id_store_end_to_end(postgres_engine: Engine) -> None:
    revocation_store = PostgresKeyRevocationStore(postgres_engine)
    processed_id_store = PostgresProcessedIdStore(postgres_engine)

    signed_at = _NOW - timedelta(minutes=1)
    envelope, practice, this_node_id = _trusted_envelope_scenario(signed_at=signed_at)
    ring = practice_key_ring(practice)
    predicate = processed_id_store.already_processed(this_node_id)

    # First submission is accepted and recorded as processed — nothing about
    # this envelope's key is revoked yet.
    validate_inbound_envelope(
        envelope,
        this_node_id=this_node_id,
        trusted_sender_practice_id=practice.id,
        ring=ring,
        already_processed=predicate,
        at=_NOW,
    )
    processed_id_store.record_processed(
        envelope.message_id,
        id_kind="envelope",
        recipient_node_id=this_node_id,
        expires_at=envelope.expires_at,
        at=_NOW,
    )

    # The signing key is durably revoked AFTER the envelope's own signed_at.
    kid = envelope.signature.key_id
    revoked_at = signed_at + timedelta(hours=1)
    revocation_store.record_revocation(kid, practice_id=practice.id, revoked_at=revoked_at)

    # (a) the surfaced-annotation case: the object was signed strictly
    # before its key's recorded revocation instant.
    record = revocation_store.get_revocation(kid)
    assert trust_revoked_after_creation(envelope.signature.signed_at, record) is True

    # (b) resubmitting the SAME envelope is still rejected by
    # EnvelopeAlreadyProcessedError specifically — the later revocation
    # never introduces a new or competing "revoked" rejection reason for an
    # already-processed message.
    with pytest.raises(EnvelopeAlreadyProcessedError) as excinfo:
        validate_inbound_envelope(
            envelope,
            this_node_id=this_node_id,
            trusted_sender_practice_id=practice.id,
            ring=ring,
            already_processed=predicate,
            at=_NOW,
        )
    assert excinfo.value.message_id == envelope.message_id
