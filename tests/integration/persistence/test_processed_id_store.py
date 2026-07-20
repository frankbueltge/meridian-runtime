"""Integration tests for mrr.persistence.repositories.PostgresProcessedIdStore
(task-packets/E5-T07.yaml), run via the `postgres_engine` fixture in
tests/integration/conftest.py. Skips visibly if MRR_TEST_DATABASE_URL is
unset (fails hard instead if CI=true) — see that module's docstring.

Acceptance-test mapping:

- "alembic upgrade head" (creates the processed_ids table) ->
  ``test_alembic_upgrade_head_creates_processed_ids_table`` (every other
  test in this module also exercises this, via the fixture, on every run).
- "record_processed then already_processed returns True; an id never
  recorded returns False; recording the SAME (node, id) twice leaves
  exactly one row and no error (idempotency); two different recipient
  nodes recording the same id do not shadow each other" ->
  ``test_record_then_already_processed_returns_true``,
  ``test_unrecorded_id_returns_false``,
  ``test_duplicate_record_leaves_exactly_one_row_and_no_error``,
  ``test_two_recipient_nodes_do_not_shadow_each_other``.
- "the store's already_processed wired verbatim as the predicate makes
  validate_inbound_envelope reject a resubmitted envelope
  (EnvelopeAlreadyProcessedError) and validate_inbound_bundle reject a
  resubmitted bundle (BundleAlreadyProcessedError), while first submission
  passes" -> ``test_store_predicate_rejects_resubmitted_envelope_end_to_end``,
  ``test_store_predicate_rejects_resubmitted_bundle_end_to_end``.
- "prune_expired removes rows at/after their retention horizon and returns
  the count; a row whose object is still within its validity window is NOT
  pruned ... after a legitimate prune past the horizon, already_processed
  returns False" -> ``test_prune_expired_removes_row_past_its_horizon``,
  ``test_prune_expired_does_not_remove_a_row_still_within_its_grace_window``,
  ``test_prune_expired_returns_the_count_pruned_across_multiple_rows``.
- "idempotent record under a UnitOfWork rollback leaves no row" ->
  ``test_record_processed_with_connection_rollback_leaves_no_row``.
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
from mrr.domain.exceptions import BundleAlreadyProcessedError, EnvelopeAlreadyProcessedError
from mrr.domain.hashing_policy import sign_object
from mrr.domain.identity import new_urn
from mrr.domain.manifest_trust import practice_key_ring
from mrr.domain.offline_bundle import build_outbox_bundle, validate_inbound_bundle
from mrr.persistence.repositories import PostgresProcessedIdStore
from mrr.persistence.tables import processed_ids_table
from sqlalchemy import Engine, inspect

_NOW = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
_VALID_FROM = _NOW - timedelta(days=1)
_VALID_UNTIL = _NOW + timedelta(days=365)


# ---------------------------------------------------------------------------
# Fixture builders (deliberately local — this codebase's own convention of
# duplicating small fixture builders per test tier rather than sharing them,
# see tests/unit/domain/test_manifest_trust.py's own precedent).
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
        "description": "Fixture practice for the processed-id store integration tests.",
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
            "signed_at": _NOW,
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
    )
    return _sign_envelope(envelope, private_key), practice, this_node_id


def _trusted_bundle_scenario(
    *,
    created_at: datetime = _NOW - timedelta(minutes=1),
    expires_at: datetime = _NOW + timedelta(days=7),
) -> tuple[Any, Practice, str]:
    private_key, public_key = generate_ed25519_keypair()
    practice_id = new_urn("practice")
    this_node_id = new_urn("node")
    entry = _key_entry(public_key)
    practice = _practice(practice_id=practice_id, keys=[entry])
    inner_envelope = _envelope(
        sender_practice_id=practice_id,
        key_id=entry["kid"],
        recipient_node_id=this_node_id,
    )
    signed_inner_envelope = _sign_envelope(inner_envelope, private_key)
    bundle = build_outbox_bundle(
        [signed_inner_envelope],
        bundle_id=new_urn("offline-bundle"),
        bundle_nonce="n" * 16,
        sender_node_id=new_urn("node"),
        sender_practice_id=practice_id,
        recipient_node_id=this_node_id,
        created_at=created_at,
        expires_at=expires_at,
        signing_key=private_key,
        key_id=entry["kid"],
    )
    return bundle, practice, this_node_id


# ---------------------------------------------------------------------------
# alembic upgrade head
# ---------------------------------------------------------------------------


def test_alembic_upgrade_head_creates_processed_ids_table(postgres_engine: Engine) -> None:
    inspector = inspect(postgres_engine)
    assert "processed_ids" in set(inspector.get_table_names())


# ---------------------------------------------------------------------------
# record_processed / already_processed: idempotency and per-node scoping.
# ---------------------------------------------------------------------------


def test_record_then_already_processed_returns_true(postgres_engine: Engine) -> None:
    store = PostgresProcessedIdStore(postgres_engine)
    node_id = new_urn("node")
    message_id = new_urn("node-message-envelope")

    newly_recorded = store.record_processed(
        message_id,
        id_kind="envelope",
        recipient_node_id=node_id,
        expires_at=_NOW + timedelta(minutes=5),
        at=_NOW,
    )

    assert newly_recorded is True
    assert store.already_processed(node_id)(message_id) is True


def test_unrecorded_id_returns_false(postgres_engine: Engine) -> None:
    store = PostgresProcessedIdStore(postgres_engine)
    node_id = new_urn("node")
    never_recorded_id = new_urn("node-message-envelope")

    assert store.already_processed(node_id)(never_recorded_id) is False


def test_duplicate_record_leaves_exactly_one_row_and_no_error(postgres_engine: Engine) -> None:
    store = PostgresProcessedIdStore(postgres_engine)
    node_id = new_urn("node")
    message_id = new_urn("node-message-envelope")

    first = store.record_processed(
        message_id,
        id_kind="envelope",
        recipient_node_id=node_id,
        expires_at=_NOW + timedelta(minutes=5),
        at=_NOW,
    )
    second = store.record_processed(
        message_id,
        id_kind="envelope",
        recipient_node_id=node_id,
        expires_at=_NOW + timedelta(minutes=5),
        at=_NOW + timedelta(seconds=1),
    )

    assert first is True
    assert second is False  # idempotent no-op, not an error
    assert store.already_processed(node_id)(message_id) is True

    with postgres_engine.connect() as conn:
        row_count = conn.execute(
            sa.select(sa.func.count())
            .select_from(processed_ids_table)
            .where(
                processed_ids_table.c.recipient_node_id == node_id,
                processed_ids_table.c.id == message_id,
            )
        ).scalar_one()
    assert row_count == 1


def test_two_recipient_nodes_do_not_shadow_each_other(postgres_engine: Engine) -> None:
    store = PostgresProcessedIdStore(postgres_engine)
    node_a = new_urn("node")
    node_b = new_urn("node")
    message_id = new_urn("node-message-envelope")

    store.record_processed(
        message_id,
        id_kind="envelope",
        recipient_node_id=node_a,
        expires_at=_NOW + timedelta(minutes=5),
        at=_NOW,
    )

    assert store.already_processed(node_a)(message_id) is True
    assert store.already_processed(node_b)(message_id) is False


# ---------------------------------------------------------------------------
# Drop-in predicate: durable replay fail-closed end to end.
# ---------------------------------------------------------------------------


def test_store_predicate_rejects_resubmitted_envelope_end_to_end(postgres_engine: Engine) -> None:
    store = PostgresProcessedIdStore(postgres_engine)
    envelope, practice, this_node_id = _trusted_envelope_scenario()
    ring = practice_key_ring(practice)
    predicate = store.already_processed(this_node_id)

    # First submission: the store's own predicate is drop-in — no
    # adaptation — and passes because nothing has been recorded yet.
    validate_inbound_envelope(
        envelope,
        this_node_id=this_node_id,
        trusted_sender_practice_id=practice.id,
        ring=ring,
        already_processed=predicate,
        at=_NOW,
    )

    store.record_processed(
        envelope.message_id,
        id_kind="envelope",
        recipient_node_id=this_node_id,
        expires_at=envelope.expires_at,
        at=_NOW,
    )

    # Resubmission of the SAME envelope: the durable predicate now returns
    # True, so validate_inbound_envelope fails closed — no change to that
    # function was needed, it was always ready for this predicate seam.
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


def test_store_predicate_rejects_resubmitted_bundle_end_to_end(postgres_engine: Engine) -> None:
    store = PostgresProcessedIdStore(postgres_engine)
    bundle, practice, this_node_id = _trusted_bundle_scenario()
    ring = practice_key_ring(practice)
    predicate = store.already_processed(this_node_id)

    validate_inbound_bundle(
        bundle,
        this_node_id=this_node_id,
        trusted_sender_practice_id=practice.id,
        ring=ring,
        already_processed=predicate,
        at=_NOW,
    )

    store.record_processed(
        bundle.bundle_id,
        id_kind="bundle",
        recipient_node_id=this_node_id,
        expires_at=bundle.expires_at,
        at=_NOW,
    )

    with pytest.raises(BundleAlreadyProcessedError) as excinfo:
        validate_inbound_bundle(
            bundle,
            this_node_id=this_node_id,
            trusted_sender_practice_id=practice.id,
            ring=ring,
            already_processed=predicate,
            at=_NOW,
        )
    assert excinfo.value.bundle_id == bundle.bundle_id


# ---------------------------------------------------------------------------
# prune_expired: removes only rows past their retention horizon.
# ---------------------------------------------------------------------------


def test_prune_expired_removes_row_past_its_horizon(postgres_engine: Engine) -> None:
    store = PostgresProcessedIdStore(postgres_engine, grace=timedelta(0))
    node_id = new_urn("node")
    message_id = new_urn("node-message-envelope")
    expires_at = _NOW - timedelta(hours=1)  # already expired, no grace

    store.record_processed(
        message_id,
        id_kind="envelope",
        recipient_node_id=node_id,
        expires_at=expires_at,
        at=_NOW - timedelta(hours=2),
    )
    assert store.already_processed(node_id)(message_id) is True

    pruned_count = store.prune_expired(_NOW)

    assert pruned_count == 1
    assert store.already_processed(node_id)(message_id) is False


def test_prune_expired_does_not_remove_a_row_still_within_its_grace_window(
    postgres_engine: Engine,
) -> None:
    grace = timedelta(days=1)
    store = PostgresProcessedIdStore(postgres_engine, grace=grace)
    node_id = new_urn("node")
    message_id = new_urn("node-message-envelope")
    # expires_at is in the past, but expires_at + grace is still in the
    # future — the row must NOT be pruned yet.
    expires_at = _NOW - timedelta(hours=1)

    store.record_processed(
        message_id,
        id_kind="envelope",
        recipient_node_id=node_id,
        expires_at=expires_at,
        at=_NOW - timedelta(hours=2),
    )

    pruned_count = store.prune_expired(_NOW)

    assert pruned_count == 0
    assert store.already_processed(node_id)(message_id) is True

    # A prune evaluated AFTER the grace window has genuinely elapsed does
    # remove it, and already_processed then correctly returns False.
    later_pruned_count = store.prune_expired(_NOW + grace)
    assert later_pruned_count == 1
    assert store.already_processed(node_id)(message_id) is False


def test_prune_expired_returns_the_count_pruned_across_multiple_rows(
    postgres_engine: Engine,
) -> None:
    store = PostgresProcessedIdStore(postgres_engine, grace=timedelta(0))
    node_id = new_urn("node")

    expired_ids = [new_urn("node-message-envelope") for _ in range(3)]
    for expired_id in expired_ids:
        store.record_processed(
            expired_id,
            id_kind="envelope",
            recipient_node_id=node_id,
            expires_at=_NOW - timedelta(hours=1),
            at=_NOW - timedelta(hours=2),
        )

    still_valid_id = new_urn("node-message-envelope")
    store.record_processed(
        still_valid_id,
        id_kind="envelope",
        recipient_node_id=node_id,
        expires_at=_NOW + timedelta(days=1),
        at=_NOW,
    )

    pruned_count = store.prune_expired(_NOW)

    assert pruned_count == 3
    for expired_id in expired_ids:
        assert store.already_processed(node_id)(expired_id) is False
    assert store.already_processed(node_id)(still_valid_id) is True


# ---------------------------------------------------------------------------
# record_processed_with_connection: participates in a caller transaction.
# ---------------------------------------------------------------------------


def test_record_processed_with_connection_rollback_leaves_no_row(postgres_engine: Engine) -> None:
    store = PostgresProcessedIdStore(postgres_engine)
    node_id = new_urn("node")
    message_id = new_urn("node-message-envelope")

    with (
        pytest.raises(RuntimeError, match="injected failure after record, before commit"),
        postgres_engine.begin() as conn,
    ):
        store.record_processed_with_connection(
            conn,
            message_id,
            id_kind="envelope",
            recipient_node_id=node_id,
            expires_at=_NOW + timedelta(minutes=5),
            at=_NOW,
        )
        raise RuntimeError("injected failure after record, before commit")

    assert store.already_processed(node_id)(message_id) is False
    with postgres_engine.connect() as conn:
        row_count = conn.execute(
            sa.select(sa.func.count())
            .select_from(processed_ids_table)
            .where(
                processed_ids_table.c.recipient_node_id == node_id,
                processed_ids_table.c.id == message_id,
            )
        ).scalar_one()
    assert row_count == 0
