"""Contract-tier acceptance tests for task-packets/E5-T08.yaml's CLI surface
(``mrr federation outbox write`` / ``mrr federation inbox accept``),
DB-free and network-free. Complements the exhaustive per-condition matrix in
tests/unit/services/cli/test_federation_main.py with a focused check on
exactly what R7's "contract" tier names: the CLI's exit codes, and the
"not yet envelope-validated" statement.

No corpora fixture is used or created — every key, Practice document, and
NodeMessageEnvelope is built inside this module and written to ``tmp_path``
only (task-packets/E5-T08.yaml R6: no key or identity is ever committed).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from mrr.crypto.keys import derive_key_id, encode_public_key, generate_ed25519_keypair
from mrr.domain.hashing_policy import sign_object
from mrr.domain.identity import new_urn
from mrr.services.cli import federation_main

_NOW = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)
_VALID_FROM = _NOW - timedelta(days=1)
_VALID_UNTIL = _NOW + timedelta(days=365)
_CREATED_AT = _NOW - timedelta(minutes=1)
_EXPIRES_AT = _NOW + timedelta(days=7)

_EXIT_ACCEPTED = 0
_EXIT_DEPENDENCY_UNAVAILABLE = 2
_EXIT_REFUSED = 3


def _build_fixture(tmp_path: Path) -> dict[str, Any]:
    private_key, public_key = generate_ed25519_keypair()
    practice_id = new_urn("practice")
    sender_node_id = new_urn("node")
    recipient_node_id = new_urn("node")
    key_id = derive_key_id(public_key)

    key_path = tmp_path / "sender.key.pem"
    key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    practice_document = {
        "id": practice_id,
        "api_version": "mrr/v1alpha1",
        "kind": "Practice",
        "practice_id": practice_id,
        "revision": 1,
        "created_at": _NOW.isoformat(),
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "name": "Fixture Practice",
        "description": "Fixture practice for the federation CLI contract test.",
        "keys": [
            {
                "kid": key_id,
                "algorithm": "Ed25519",
                "encoded_public_key": encode_public_key(public_key),
                "valid_from": _VALID_FROM.isoformat(),
                "valid_until": _VALID_UNTIL.isoformat(),
                "state": "active",
            }
        ],
        "governance_contacts": ["mailto:governance@fixture.invalid"],
        "supported_policy_versions": ["policy-2026-07-01"],
        "disclosure": {"max_disclosure": "PUBLIC", "trust_statement": "fixture"},
    }
    practice_path = tmp_path / "practice.json"
    practice_path.write_text(json.dumps(practice_document), encoding="utf-8")

    envelope_data: dict[str, Any] = {
        "message_id": new_urn("node-message-envelope"),
        "sender_node_id": sender_node_id,
        "sender_practice_id": practice_id,
        "recipient_node_id": recipient_node_id,
        "sent_at": (_NOW - timedelta(minutes=1)).isoformat(),
        "expires_at": (_NOW + timedelta(days=1)).isoformat(),
        "payload_kind": "TaskBundle",
        "payload_content_hash": "sha256:" + "c" * 64,
        "payload": {"kind": "TaskBundle", "content_hash": "sha256:" + "c" * 64},
        "signature": {
            "signer_practice_id": practice_id,
            "key_id": key_id,
            "algorithm": "Ed25519",
            "signed_at": _NOW.isoformat(),
            "value": "0" * 44,
        },
    }
    envelope_data["signature"]["value"] = sign_object(private_key, envelope_data)
    envelope_path = tmp_path / "envelope.json"
    envelope_path.write_text(json.dumps(envelope_data), encoding="utf-8")

    return {
        "practice_id": practice_id,
        "sender_node_id": sender_node_id,
        "recipient_node_id": recipient_node_id,
        "key_id": key_id,
        "key_path": key_path,
        "practice_path": practice_path,
        "envelope_path": envelope_path,
        "bundle_id": new_urn("offline-bundle"),
        "output_path": tmp_path / "outbox" / "bundle.json",
        "ledger_path": tmp_path / "ledger.json",
    }


def _write_argv(fixture: dict[str, Any]) -> list[str]:
    return [
        "outbox",
        "write",
        "--envelope",
        str(fixture["envelope_path"]),
        "--bundle-id",
        fixture["bundle_id"],
        "--bundle-nonce",
        "n" * 16,
        "--sender-node-id",
        fixture["sender_node_id"],
        "--sender-practice-id",
        fixture["practice_id"],
        "--recipient-node-id",
        fixture["recipient_node_id"],
        "--created-at",
        _CREATED_AT.isoformat(),
        "--expires-at",
        _EXPIRES_AT.isoformat(),
        "--key-file",
        str(fixture["key_path"]),
        "--key-id",
        fixture["key_id"],
        "--output",
        str(fixture["output_path"]),
    ]


def _accept_argv(fixture: dict[str, Any]) -> list[str]:
    return [
        "inbox",
        "accept",
        "--bundle",
        str(fixture["output_path"]),
        "--this-node-id",
        fixture["recipient_node_id"],
        "--trusted-sender-practice",
        str(fixture["practice_path"]),
        "--ledger",
        str(fixture["ledger_path"]),
        "--at",
        _NOW.isoformat(),
    ]


def test_outbox_write_exits_zero_and_inbox_accept_exits_zero(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)

    assert federation_main.main(_write_argv(fixture)) == _EXIT_ACCEPTED
    assert federation_main.main(_accept_argv(fixture)) == _EXIT_ACCEPTED


def test_inbox_accept_states_bundle_verified_but_not_envelope_validated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = _build_fixture(tmp_path)
    assert federation_main.main(_write_argv(fixture)) == _EXIT_ACCEPTED
    capsys.readouterr()

    exit_code = federation_main.main(_accept_argv(fixture))

    assert exit_code == _EXIT_ACCEPTED
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["envelope_validated"] is False
    assert "NOT yet envelope-validated" in payload["note"]
    assert "NOT yet envelope-validated" in captured.err
    assert payload["accepted_envelope_ids"]


def test_outbox_write_output_conflict_exits_refused(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    fixture["output_path"].parent.mkdir(parents=True, exist_ok=True)
    fixture["output_path"].write_bytes(b"pre-existing")

    exit_code = federation_main.main(_write_argv(fixture))

    assert exit_code == _EXIT_REFUSED


def test_outbox_write_missing_envelope_exits_dependency_unavailable(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    argv = _write_argv(fixture)
    argv[argv.index("--envelope") + 1] = str(tmp_path / "missing.json")

    exit_code = federation_main.main(argv)

    assert exit_code == _EXIT_DEPENDENCY_UNAVAILABLE


def test_inbox_accept_wrong_recipient_exits_refused(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    assert federation_main.main(_write_argv(fixture)) == _EXIT_ACCEPTED
    argv = _accept_argv(fixture)
    argv[argv.index("--this-node-id") + 1] = new_urn("node")

    exit_code = federation_main.main(argv)

    assert exit_code == _EXIT_REFUSED


def test_inbox_accept_malformed_ledger_exits_dependency_unavailable(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    assert federation_main.main(_write_argv(fixture)) == _EXIT_ACCEPTED
    fixture["ledger_path"].write_text("not json", encoding="utf-8")

    exit_code = federation_main.main(_accept_argv(fixture))

    assert exit_code == _EXIT_DEPENDENCY_UNAVAILABLE
