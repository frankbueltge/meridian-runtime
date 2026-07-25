"""Unit tests for ``mrr federation outbox write`` / ``mrr federation inbox
accept`` (task-packets/E5-T08.yaml R5/R7), DB-free and network-free — every
fixture (keys, Practice document, NodeMessageEnvelope) is built and written
to ``tmp_path`` inside this module; nothing is read from ``corpora/`` and no
key or identity is ever committed (task-packets/E5-T08.yaml R6).

Covers:

- AT1 at the CLI layer: a full outbox-write -> inbox-accept round trip
  exits 0 both times and reports the accepted envelope's own id.
- AT2: the five accept conditions (wrong recipient, outside validity
  window, already processed, untrusted signer practice, tampered byte) each
  exit 3 with a message naming its OWN distinct condition — never a single
  generic "rejected".
- AT3: after each of the five refusals above, the replay ledger file is
  byte-identical to before (asserted on bytes); a successful acceptance
  appends exactly one id.
- AT4: a successful ``inbox accept`` states, in the JSON payload AND on
  stderr, that accepted envelopes are bundle-verified but NOT yet
  envelope-validated.
- ``outbox write``'s own NFR-012 ordering: ``--output`` conflict (exit 3,
  checked first) and missing/invalid dependency files (exit 2).
- R6: no ``--generate-key`` flag exists anywhere on this command.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from mrr.crypto.keys import derive_key_id, encode_public_key, generate_ed25519_keypair
from mrr.domain.hashing_policy import sign_object
from mrr.domain.identity import new_urn
from mrr.services.cli import federation_main

_NOW = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)
_VALID_FROM = _NOW - timedelta(days=1)
_VALID_UNTIL = _NOW + timedelta(days=365)
_BUNDLE_CREATED_AT = _NOW - timedelta(minutes=1)
_BUNDLE_EXPIRES_AT = _NOW + timedelta(days=7)

_EXIT_DEPENDENCY_UNAVAILABLE = 2
_EXIT_REFUSED = 3

# ---------------------------------------------------------------------------
# Fixture builders — local to this module (test keys created HERE, never
# committed anywhere; task-packets/E5-T08.yaml R6).
# ---------------------------------------------------------------------------


def _write_pem_key(path: Path, key: Ed25519PrivateKey) -> None:
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path.write_bytes(pem)


def _practice_document(*, practice_id: str, public_key: Ed25519PublicKey) -> dict[str, Any]:
    return {
        "id": practice_id,
        "api_version": "mrr/v1alpha1",
        "kind": "Practice",
        "practice_id": practice_id,
        "revision": 1,
        "created_at": _NOW.isoformat(),
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "name": "Fixture Practice",
        "description": "Fixture practice for the federation CLI unit tests.",
        "keys": [
            {
                "kid": derive_key_id(public_key),
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


def _signed_envelope_document(
    *,
    sender_practice_id: str,
    key_id: str,
    recipient_node_id: str,
    private_key: Ed25519PrivateKey,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "message_id": new_urn("node-message-envelope"),
        "sender_node_id": new_urn("node"),
        "sender_practice_id": sender_practice_id,
        "recipient_node_id": recipient_node_id,
        "sent_at": (_NOW - timedelta(minutes=1)).isoformat(),
        "expires_at": (_NOW + timedelta(days=1)).isoformat(),
        "payload_kind": "TaskBundle",
        "payload_content_hash": "sha256:" + "c" * 64,
        "payload": {"kind": "TaskBundle", "content_hash": "sha256:" + "c" * 64},
        "signature": {
            "signer_practice_id": sender_practice_id,
            "key_id": key_id,
            "algorithm": "Ed25519",
            "signed_at": _NOW.isoformat(),
            "value": "0" * 44,
        },
    }
    signature_value = sign_object(private_key, data)
    data["signature"]["value"] = signature_value
    return data


class _Scenario:
    """A fully self-consistent, on-disk fixture set: one sender practice/key,
    one signed envelope, and the file paths a real ``mrr federation``
    invocation would take.
    """

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.private_key, self.public_key = generate_ed25519_keypair()
        self.practice_id = new_urn("practice")
        self.sender_node_id = new_urn("node")
        self.recipient_node_id = new_urn("node")
        self.key_id = derive_key_id(self.public_key)

        self.key_path = tmp_path / "sender.key.pem"
        _write_pem_key(self.key_path, self.private_key)

        self.practice_path = tmp_path / "practice.json"
        self.practice_path.write_text(
            json.dumps(
                _practice_document(practice_id=self.practice_id, public_key=self.public_key)
            ),
            encoding="utf-8",
        )

        self.envelope_path = tmp_path / "envelope.json"
        self.envelope_path.write_text(
            json.dumps(
                _signed_envelope_document(
                    sender_practice_id=self.practice_id,
                    key_id=self.key_id,
                    recipient_node_id=self.recipient_node_id,
                    private_key=self.private_key,
                )
            ),
            encoding="utf-8",
        )

        self.bundle_id = new_urn("offline-bundle")
        self.output_path = tmp_path / "outbox" / "bundle.json"
        self.ledger_path = tmp_path / "ledger.json"

    def write_argv(self) -> list[str]:
        return [
            "outbox",
            "write",
            "--envelope",
            str(self.envelope_path),
            "--bundle-id",
            self.bundle_id,
            "--bundle-nonce",
            "n" * 16,
            "--sender-node-id",
            self.sender_node_id,
            "--sender-practice-id",
            self.practice_id,
            "--recipient-node-id",
            self.recipient_node_id,
            "--created-at",
            _BUNDLE_CREATED_AT.isoformat(),
            "--expires-at",
            _BUNDLE_EXPIRES_AT.isoformat(),
            "--key-file",
            str(self.key_path),
            "--key-id",
            self.key_id,
            "--output",
            str(self.output_path),
        ]

    def accept_argv(
        self, *, this_node_id: str | None = None, at: datetime | None = None
    ) -> list[str]:
        argv = [
            "inbox",
            "accept",
            "--bundle",
            str(self.output_path),
            "--this-node-id",
            this_node_id if this_node_id is not None else self.recipient_node_id,
            "--trusted-sender-practice",
            str(self.practice_path),
            "--ledger",
            str(self.ledger_path),
        ]
        if at is not None:
            argv += ["--at", at.isoformat()]
        return argv


def _read_ledger_bytes_if_present(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


# ---------------------------------------------------------------------------
# AT1: full round trip through the CLI.
# ---------------------------------------------------------------------------


def test_full_round_trip_outbox_write_then_inbox_accept(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario = _Scenario(tmp_path)

    write_exit = federation_main.main(scenario.write_argv())
    assert write_exit == 0
    assert scenario.output_path.is_file()

    accept_exit = federation_main.main(scenario.accept_argv(at=_NOW))
    assert accept_exit == 0
    assert scenario.ledger_path.is_file()

    ledger_document = json.loads(scenario.ledger_path.read_text(encoding="utf-8"))
    assert ledger_document["processed_bundle_ids"] == [scenario.bundle_id]


def test_inbox_accept_reports_bundle_verified_but_not_envelope_validated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario = _Scenario(tmp_path)
    assert federation_main.main(scenario.write_argv()) == 0
    capsys.readouterr()

    exit_code = federation_main.main(scenario.accept_argv(at=_NOW))
    assert exit_code == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["envelope_validated"] is False
    assert "NOT yet envelope-validated" in payload["note"]
    assert "NOT yet envelope-validated" in captured.err


# ---------------------------------------------------------------------------
# `outbox write`'s own NFR-012 ordering.
# ---------------------------------------------------------------------------


def test_outbox_write_refuses_an_existing_output_path(tmp_path: Path) -> None:
    scenario = _Scenario(tmp_path)
    scenario.output_path.parent.mkdir(parents=True, exist_ok=True)
    scenario.output_path.write_bytes(b"already here")

    exit_code = federation_main.main(scenario.write_argv())

    assert exit_code == _EXIT_REFUSED
    assert scenario.output_path.read_bytes() == b"already here"


def test_outbox_write_missing_envelope_file_is_a_dependency_failure(tmp_path: Path) -> None:
    scenario = _Scenario(tmp_path)
    argv = scenario.write_argv()
    argv[argv.index("--envelope") + 1] = str(tmp_path / "no-such-envelope.json")

    exit_code = federation_main.main(argv)

    assert exit_code == _EXIT_DEPENDENCY_UNAVAILABLE
    assert not scenario.output_path.exists()


def test_outbox_write_missing_key_file_is_a_dependency_failure(tmp_path: Path) -> None:
    scenario = _Scenario(tmp_path)
    argv = scenario.write_argv()
    argv[argv.index("--key-file") + 1] = str(tmp_path / "no-such-key.pem")

    exit_code = federation_main.main(argv)

    assert exit_code == _EXIT_DEPENDENCY_UNAVAILABLE


def test_outbox_write_key_file_not_ed25519_is_a_dependency_failure(tmp_path: Path) -> None:
    scenario = _Scenario(tmp_path)
    bad_key_path = tmp_path / "not-a-key.pem"
    bad_key_path.write_text("this is not a PEM key at all", encoding="utf-8")
    argv = scenario.write_argv()
    argv[argv.index("--key-file") + 1] = str(bad_key_path)

    exit_code = federation_main.main(argv)

    assert exit_code == _EXIT_DEPENDENCY_UNAVAILABLE


# ---------------------------------------------------------------------------
# R6: no --generate-key convenience anywhere on this command.
# ---------------------------------------------------------------------------


def test_no_generate_key_flag_exists_on_outbox_write_or_inbox_accept(
    capsys: pytest.CaptureFixture[str],
) -> None:
    help_text = ""
    for argv in (["--help"], ["outbox", "write", "--help"], ["inbox", "accept", "--help"]):
        try:
            federation_main.main(argv)
        except SystemExit as exc:
            assert exc.code == 0
        help_text += capsys.readouterr().out

    assert "--generate-key" not in help_text
    assert "--key-file" in help_text


# ---------------------------------------------------------------------------
# AT2 + AT3: the five accept conditions, each its own distinct refusal, and
# the ledger left byte-identical after every one of them.
# ---------------------------------------------------------------------------


def test_wrong_recipient_is_refused_and_ledger_is_untouched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario = _Scenario(tmp_path)
    assert federation_main.main(scenario.write_argv()) == 0
    before = _read_ledger_bytes_if_present(scenario.ledger_path)
    capsys.readouterr()

    exit_code = federation_main.main(scenario.accept_argv(this_node_id=new_urn("node"), at=_NOW))

    assert exit_code == _EXIT_REFUSED
    assert "wrong recipient" in capsys.readouterr().err
    assert _read_ledger_bytes_if_present(scenario.ledger_path) == before


def test_outside_validity_window_is_refused_and_ledger_is_untouched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario = _Scenario(tmp_path)
    assert federation_main.main(scenario.write_argv()) == 0
    before = _read_ledger_bytes_if_present(scenario.ledger_path)
    capsys.readouterr()

    exit_code = federation_main.main(
        scenario.accept_argv(at=_BUNDLE_EXPIRES_AT + timedelta(seconds=1))
    )

    assert exit_code == _EXIT_REFUSED
    assert "validity window" in capsys.readouterr().err
    assert _read_ledger_bytes_if_present(scenario.ledger_path) == before


def test_already_processed_is_refused_and_ledger_is_untouched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario = _Scenario(tmp_path)
    assert federation_main.main(scenario.write_argv()) == 0
    # A prior, real acceptance records the bundle id.
    assert federation_main.main(scenario.accept_argv(at=_NOW)) == 0
    before = _read_ledger_bytes_if_present(scenario.ledger_path)
    assert before is not None
    capsys.readouterr()

    exit_code = federation_main.main(scenario.accept_argv(at=_NOW))

    assert exit_code == _EXIT_REFUSED
    assert "already processed" in capsys.readouterr().err
    assert _read_ledger_bytes_if_present(scenario.ledger_path) == before


def test_untrusted_signer_practice_is_refused_and_ledger_is_untouched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario = _Scenario(tmp_path)
    assert federation_main.main(scenario.write_argv()) == 0
    before = _read_ledger_bytes_if_present(scenario.ledger_path)
    capsys.readouterr()

    # A DIFFERENT practice (unrelated key) as the declared trusted sender.
    _, other_public_key = generate_ed25519_keypair()
    other_practice_id = new_urn("practice")
    other_practice_path = tmp_path / "other-practice.json"
    other_practice_path.write_text(
        json.dumps(_practice_document(practice_id=other_practice_id, public_key=other_public_key)),
        encoding="utf-8",
    )
    argv = scenario.accept_argv(at=_NOW)
    argv[argv.index("--trusted-sender-practice") + 1] = str(other_practice_path)

    exit_code = federation_main.main(argv)

    assert exit_code == _EXIT_REFUSED
    assert "untrusted signer practice" in capsys.readouterr().err
    assert _read_ledger_bytes_if_present(scenario.ledger_path) == before


def test_tampered_byte_is_refused_and_ledger_is_untouched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario = _Scenario(tmp_path)
    assert federation_main.main(scenario.write_argv()) == 0
    before = _read_ledger_bytes_if_present(scenario.ledger_path)
    capsys.readouterr()

    document = json.loads(scenario.output_path.read_text(encoding="utf-8"))
    document["bundle_nonce"] = "z" * 16
    scenario.output_path.write_text(json.dumps(document), encoding="utf-8")

    exit_code = federation_main.main(scenario.accept_argv(at=_NOW))

    assert exit_code == _EXIT_REFUSED
    assert "signature does not verify" in capsys.readouterr().err
    assert _read_ledger_bytes_if_present(scenario.ledger_path) == before


# ---------------------------------------------------------------------------
# A malformed ledger is a distinct, dependency-tier failure (exit 2), never
# a silent "nothing processed yet".
# ---------------------------------------------------------------------------


def test_malformed_ledger_is_a_dependency_failure_not_a_refusal(tmp_path: Path) -> None:
    scenario = _Scenario(tmp_path)
    assert federation_main.main(scenario.write_argv()) == 0
    scenario.ledger_path.write_text("not json at all", encoding="utf-8")
    before = scenario.ledger_path.read_bytes()

    exit_code = federation_main.main(scenario.accept_argv(at=_NOW))

    assert exit_code == _EXIT_DEPENDENCY_UNAVAILABLE
    assert scenario.ledger_path.read_bytes() == before


def test_missing_bundle_file_is_a_dependency_failure(tmp_path: Path) -> None:
    scenario = _Scenario(tmp_path)
    argv = scenario.accept_argv(at=_NOW)
    argv[argv.index("--bundle") + 1] = str(tmp_path / "no-such-bundle.json")

    exit_code = federation_main.main(argv)

    assert exit_code == _EXIT_DEPENDENCY_UNAVAILABLE
