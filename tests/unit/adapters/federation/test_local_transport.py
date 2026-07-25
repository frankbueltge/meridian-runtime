"""Unit tests for mrr.adapters.federation.local (task-packets/E5-T08.yaml),
against a real ``tmp_path`` filesystem — no network, no database, fully
local per task-packets/E5-T08.yaml's own invariants.

Covers the packet's named acceptance tests at the adapter layer: AT1's
round-trip (a bundle built by the UNCHANGED E5-T06 core, written by
``LocalFilesystemBundleTransport.write_bundle`` and read back by
``read_bundle``, is byte-identical to the independently-computed canonical
form and its signature still verifies via the unchanged
``validate_inbound_bundle``); ``write_bundle`` never overwrites an existing
path; every malformed-input case ``read_bundle`` names raises its own typed
``BundleReadError``; ``read_bundle`` performs NO trust evaluation (a bundle
with a garbage signature still parses); and the replay ledger's own AT3 —
sorted/byte-stable/atomic writes, a malformed ledger raises rather than
defaulting to empty, and recording is a distinct, single, idempotent act.

Fixture builders are deliberately local (this codebase's own convention —
see tests/unit/domain/test_offline_bundle.py's own precedent) rather than
imported from that module, since that module's helpers are private to it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from mrr.adapters.federation.local import (
    BundleReadError,
    BundleWriteConflictError,
    FileBackedReplayLedger,
    LocalFilesystemBundleTransport,
    ReplayLedgerCorruptError,
)
from mrr.contracts.node_message_envelope import NodeMessageEnvelope
from mrr.contracts.offline_bundle import OfflineBundle
from mrr.contracts.practice import Practice
from mrr.crypto.canonical import canonicalize
from mrr.crypto.hashing import content_hash
from mrr.crypto.keys import derive_key_id, encode_public_key, generate_ed25519_keypair
from mrr.domain.hashing_policy import sign_object
from mrr.domain.identity import new_urn
from mrr.domain.manifest_trust import practice_key_ring
from mrr.domain.offline_bundle import build_outbox_bundle, validate_inbound_bundle

_NOW = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)
_VALID_FROM = _NOW - timedelta(days=1)
_VALID_UNTIL = _NOW + timedelta(days=365)
_BUNDLE_CREATED_AT = _NOW - timedelta(minutes=1)
_BUNDLE_EXPIRES_AT = _NOW + timedelta(days=7)

# ---------------------------------------------------------------------------
# Fixture builders — deliberately local, mirroring tests/unit/domain/
# test_offline_bundle.py's own identical fixtures. Test keys are created
# HERE, in this test module, never written to corpora/ or any committed
# fixture (task-packets/E5-T08.yaml R6).
# ---------------------------------------------------------------------------


def _key_entry(public_key: Ed25519PublicKey) -> dict[str, Any]:
    return {
        "kid": derive_key_id(public_key),
        "algorithm": "Ed25519",
        "encoded_public_key": encode_public_key(public_key),
        "valid_from": _VALID_FROM,
        "valid_until": _VALID_UNTIL,
        "state": "active",
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
        "description": "Fixture practice for federation transport unit tests.",
        "keys": keys,
        "governance_contacts": ["mailto:governance@fixture.invalid"],
        "supported_policy_versions": ["policy-2026-07-01"],
        "disclosure": {"max_disclosure": "PUBLIC", "trust_statement": "fixture"},
    }
    return Practice.model_validate(data)


def _signed_envelope(
    *,
    sender_practice_id: str,
    key_id: str,
    recipient_node_id: str,
    private_key: Ed25519PrivateKey,
    tag: int = 0,
) -> NodeMessageEnvelope:
    data: dict[str, Any] = {
        "message_id": new_urn("node-message-envelope"),
        "sender_node_id": new_urn("node"),
        "sender_practice_id": sender_practice_id,
        "recipient_node_id": recipient_node_id,
        "sent_at": _NOW - timedelta(minutes=1),
        "expires_at": _NOW + timedelta(days=1),
        "payload_kind": "TaskBundle",
        "payload_content_hash": "sha256:" + "c" * 64,
        "payload": {"kind": "TaskBundle", "content_hash": "sha256:" + "c" * 64, "tag": tag},
        "signature": {
            "signer_practice_id": sender_practice_id,
            "key_id": key_id,
            "algorithm": "Ed25519",
            "signed_at": _NOW,
            "value": "0" * 44,
        },
    }
    envelope = NodeMessageEnvelope.model_validate(data)
    signature_value = sign_object(
        private_key, json.loads(envelope.model_dump_json(exclude_none=True))
    )
    return envelope.model_copy(
        update={"signature": envelope.signature.model_copy(update={"value": signature_value})}
    )


def _never_processed(bundle_id: str) -> bool:
    return False


def _trusted_scenario() -> tuple[OfflineBundle, Practice, str]:
    """A fully self-consistent scenario: a Practice with one active,
    in-window key, one already-signed envelope addressed to
    ``this_node_id``, and an ``OfflineBundle`` genuinely assembled and
    signed by the UNCHANGED ``build_outbox_bundle``.
    """
    private_key, public_key = generate_ed25519_keypair()
    practice_id = new_urn("practice")
    this_node_id = new_urn("node")
    entry = _key_entry(public_key)
    practice = _practice(practice_id=practice_id, keys=[entry])
    envelope = _signed_envelope(
        sender_practice_id=practice_id,
        key_id=entry["kid"],
        recipient_node_id=this_node_id,
        private_key=private_key,
    )
    bundle = build_outbox_bundle(
        [envelope],
        bundle_id=new_urn("offline-bundle"),
        bundle_nonce="n" * 16,
        sender_node_id=new_urn("node"),
        sender_practice_id=practice_id,
        recipient_node_id=this_node_id,
        created_at=_BUNDLE_CREATED_AT,
        expires_at=_BUNDLE_EXPIRES_AT,
        signing_key=private_key,
        key_id=entry["kid"],
    )
    return bundle, practice, this_node_id


# ---------------------------------------------------------------------------
# AT1: round-trip byte identity and signature re-verification.
# ---------------------------------------------------------------------------


def test_write_then_read_round_trips_byte_identical_and_signature_still_verifies(
    tmp_path: Path,
) -> None:
    bundle, practice, this_node_id = _trusted_scenario()
    ring = practice_key_ring(practice)
    transport = LocalFilesystemBundleTransport()
    path = tmp_path / "outbox" / "bundle.json"

    expected_canonical_bytes = canonicalize(json.loads(bundle.model_dump_json(exclude_none=True)))
    expected_sha256 = content_hash(expected_canonical_bytes)

    transport.write_bundle(bundle, path)
    written_bytes = path.read_bytes()

    # The file on disk is EXACTLY the canonical form the core signs over —
    # not a hand-rolled second serialisation.
    assert written_bytes == expected_canonical_bytes
    assert content_hash(written_bytes) == expected_sha256

    read_back = transport.read_bundle(path)
    assert read_back == bundle

    # Re-serialising the READ-BACK bundle reproduces the identical bytes —
    # true round-trip stability, not just Pydantic model equality.
    round_tripped_bytes = canonicalize(json.loads(read_back.model_dump_json(exclude_none=True)))
    assert round_tripped_bytes == written_bytes

    # And the signature the core computed still verifies over what was
    # actually written and read back — the load-bearing property.
    verified = validate_inbound_bundle(
        read_back,
        this_node_id=this_node_id,
        trusted_sender_practice_id=practice.id,
        ring=ring,
        already_processed=_never_processed,
        at=_NOW,
    )
    assert [envelope.message_id for envelope in verified] == [
        envelope.message_id for envelope in bundle.envelopes
    ]


def test_write_bundle_creates_parent_directories(tmp_path: Path) -> None:
    bundle, _, _ = _trusted_scenario()
    transport = LocalFilesystemBundleTransport()
    path = tmp_path / "deeply" / "nested" / "outbox" / "bundle.json"

    transport.write_bundle(bundle, path)

    assert path.is_file()


# ---------------------------------------------------------------------------
# write_bundle: never over an existing file.
# ---------------------------------------------------------------------------


def test_write_bundle_refuses_an_existing_output_path(tmp_path: Path) -> None:
    bundle, _, _ = _trusted_scenario()
    transport = LocalFilesystemBundleTransport()
    path = tmp_path / "bundle.json"
    path.write_bytes(b"pre-existing content, must not be clobbered")

    with pytest.raises(BundleWriteConflictError) as excinfo:
        transport.write_bundle(bundle, path)
    assert excinfo.value.path == path
    assert path.read_bytes() == b"pre-existing content, must not be clobbered"


def test_write_bundle_leaves_no_temp_files_behind(tmp_path: Path) -> None:
    bundle, _, _ = _trusted_scenario()
    transport = LocalFilesystemBundleTransport()
    path = tmp_path / "bundle.json"

    transport.write_bundle(bundle, path)

    assert list(tmp_path.rglob("*.tmp")) == []


def test_write_bundle_leaves_no_temp_files_behind_after_a_conflict(tmp_path: Path) -> None:
    bundle, _, _ = _trusted_scenario()
    transport = LocalFilesystemBundleTransport()
    path = tmp_path / "bundle.json"
    path.write_bytes(b"already here")

    with pytest.raises(BundleWriteConflictError):
        transport.write_bundle(bundle, path)

    assert list(tmp_path.rglob("*.tmp")) == []


# ---------------------------------------------------------------------------
# read_bundle: every malformed-input case is its own typed BundleReadError.
# ---------------------------------------------------------------------------


def test_read_bundle_raises_for_a_missing_file(tmp_path: Path) -> None:
    transport = LocalFilesystemBundleTransport()
    path = tmp_path / "does-not-exist.json"

    with pytest.raises(BundleReadError) as excinfo:
        transport.read_bundle(path)
    assert excinfo.value.path == path
    assert "does not exist" in excinfo.value.reason


def test_read_bundle_raises_for_bytes_that_are_not_valid_utf8(tmp_path: Path) -> None:
    transport = LocalFilesystemBundleTransport()
    path = tmp_path / "not-utf8.json"
    path.write_bytes(b"\xff\xfe\x00\x01")

    with pytest.raises(BundleReadError) as excinfo:
        transport.read_bundle(path)
    assert "UTF-8" in excinfo.value.reason


def test_read_bundle_raises_for_text_that_is_not_valid_json(tmp_path: Path) -> None:
    transport = LocalFilesystemBundleTransport()
    path = tmp_path / "not-json.json"
    path.write_text("{not valid json at all", encoding="utf-8")

    with pytest.raises(BundleReadError) as excinfo:
        transport.read_bundle(path)
    assert "JSON" in excinfo.value.reason


def test_read_bundle_raises_when_document_does_not_validate_as_offline_bundle(
    tmp_path: Path,
) -> None:
    transport = LocalFilesystemBundleTransport()
    path = tmp_path / "wrong-shape.json"
    path.write_text(json.dumps({"hello": "world"}), encoding="utf-8")

    with pytest.raises(BundleReadError) as excinfo:
        transport.read_bundle(path)
    assert "OfflineBundle" in excinfo.value.reason


# ---------------------------------------------------------------------------
# read_bundle performs NO trust evaluation — a tampered/garbage signature
# still parses successfully.
# ---------------------------------------------------------------------------


def test_read_bundle_does_not_evaluate_trust_a_garbage_signature_still_parses(
    tmp_path: Path,
) -> None:
    bundle, _, _ = _trusted_scenario()
    transport = LocalFilesystemBundleTransport()
    path = tmp_path / "bundle.json"
    transport.write_bundle(bundle, path)

    document = json.loads(path.read_text(encoding="utf-8"))
    document["signature"]["value"] = "9" * 44  # a syntactically valid, but wrong, signature
    tampered_path = tmp_path / "tampered.json"
    tampered_path.write_text(json.dumps(document), encoding="utf-8")

    # No exception — read_bundle parses and validates SHAPE only.
    parsed = transport.read_bundle(tampered_path)
    assert parsed.signature.value == "9" * 44


# ---------------------------------------------------------------------------
# Replay ledger.
# ---------------------------------------------------------------------------


def test_ledger_with_no_file_yet_reports_nothing_processed(tmp_path: Path) -> None:
    ledger = FileBackedReplayLedger(tmp_path / "ledger.json")
    assert ledger.already_processed("urn:mrr:offline-bundle:anything") is False


def test_ledger_record_then_already_processed_is_true(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    ledger = FileBackedReplayLedger(path)
    bundle_id = new_urn("offline-bundle")

    ledger.record(bundle_id)

    assert ledger.already_processed(bundle_id) is True
    assert ledger.already_processed(new_urn("offline-bundle")) is False


def test_ledger_file_is_sorted_sort_keys_and_trailing_newline(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    ledger = FileBackedReplayLedger(path)
    ids = [new_urn("offline-bundle") for _ in range(3)]

    for bundle_id in sorted(ids, reverse=True):  # record out of sorted order
        ledger.record(bundle_id)

    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    document = json.loads(text)
    assert document["processed_bundle_ids"] == sorted(ids)
    assert document["schema_version"] == 1
    # Byte-stable: recording the exact same set again in a fresh ledger
    # instance produces the identical bytes.
    second_path = tmp_path / "ledger-again.json"
    second_ledger = FileBackedReplayLedger(second_path)
    for bundle_id in ids:
        second_ledger.record(bundle_id)
    assert second_path.read_bytes() == path.read_bytes()


def test_ledger_record_is_idempotent_for_the_same_id(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    ledger = FileBackedReplayLedger(path)
    bundle_id = new_urn("offline-bundle")

    ledger.record(bundle_id)
    first_bytes = path.read_bytes()
    ledger.record(bundle_id)
    second_bytes = path.read_bytes()

    assert first_bytes == second_bytes
    document = json.loads(second_bytes.decode("utf-8"))
    assert document["processed_bundle_ids"].count(bundle_id) == 1


def test_ledger_leaves_no_temp_files_behind(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    ledger = FileBackedReplayLedger(path)

    ledger.record(new_urn("offline-bundle"))

    assert list(tmp_path.rglob("*.tmp")) == []


# ---------------------------------------------------------------------------
# A malformed ledger raises — it NEVER defaults to "nothing processed yet".
# ---------------------------------------------------------------------------


def test_ledger_raises_for_bytes_that_are_not_valid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    path.write_bytes(b"\xff\xfe\x00\x01")
    ledger = FileBackedReplayLedger(path)

    with pytest.raises(ReplayLedgerCorruptError):
        ledger.already_processed("anything")


def test_ledger_raises_for_text_that_is_not_valid_json(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    path.write_text("{not json", encoding="utf-8")
    ledger = FileBackedReplayLedger(path)

    with pytest.raises(ReplayLedgerCorruptError):
        ledger.already_processed("anything")


def test_ledger_raises_when_top_level_value_is_not_an_object(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(["a", "list", "not", "an", "object"]), encoding="utf-8")
    ledger = FileBackedReplayLedger(path)

    with pytest.raises(ReplayLedgerCorruptError):
        ledger.already_processed("anything")


def test_ledger_raises_for_wrong_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    path.write_text(
        json.dumps({"schema_version": 999, "processed_bundle_ids": []}), encoding="utf-8"
    )
    ledger = FileBackedReplayLedger(path)

    with pytest.raises(ReplayLedgerCorruptError):
        ledger.already_processed("anything")


def test_ledger_raises_when_ids_are_not_a_list_of_strings(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    path.write_text(
        json.dumps({"schema_version": 1, "processed_bundle_ids": [1, 2, 3]}), encoding="utf-8"
    )
    ledger = FileBackedReplayLedger(path)

    with pytest.raises(ReplayLedgerCorruptError):
        ledger.already_processed("anything")


def test_ledger_raises_for_a_duplicate_id(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    path.write_text(
        json.dumps({"schema_version": 1, "processed_bundle_ids": ["a", "a"]}), encoding="utf-8"
    )
    ledger = FileBackedReplayLedger(path)

    with pytest.raises(ReplayLedgerCorruptError):
        ledger.already_processed("anything")


def test_ledger_raises_for_an_unsorted_list(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    path.write_text(
        json.dumps({"schema_version": 1, "processed_bundle_ids": ["b", "a"]}), encoding="utf-8"
    )
    ledger = FileBackedReplayLedger(path)

    with pytest.raises(ReplayLedgerCorruptError):
        ledger.already_processed("anything")


def test_ledger_record_refuses_to_repair_pre_existing_corruption(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    path.write_text("not json at all", encoding="utf-8")
    ledger = FileBackedReplayLedger(path)
    before = path.read_bytes()

    with pytest.raises(ReplayLedgerCorruptError):
        ledger.record(new_urn("offline-bundle"))

    assert path.read_bytes() == before
