"""Unit tests for ``mrr federation envelope sign`` (task-packets/E5-T10.yaml),
DB-free and network-free — every fixture (key, payload) is built and written
to ``tmp_path`` inside this module; nothing is read from ``corpora/`` and no
key or identity is ever committed (mirrors task-packets/E5-T08.yaml R6,
which this command's own ``--key-file`` discipline reuses verbatim).

Covers:

- A full ``envelope sign`` run exits 0, writes a file that round-trips back
  into a ``NodeMessageEnvelope``, and reports the same identities in its
  JSON payload.
- ``--output`` already existing is refused (exit 3), checked FIRST, and the
  file is left byte-identical.
- Missing/unreadable/malformed ``--payload`` or ``--key-file`` is a
  dependency failure (exit 2) — including a ``--payload`` that is valid
  JSON but not a JSON OBJECT.
- A payload with no own ``content_hash`` is a typed refusal (exit 2,
  ``EnvelopePayloadMissingContentHashError`` named in the message) and
  ``--output`` is never written.
- ``--expires-at`` not strictly after ``--sent-at`` is a dependency failure
  (a ``pydantic.ValidationError`` from ``NodeMessageEnvelope`` itself,
  mirroring ``outbox write``'s own identical bucket for a structurally
  unassemblable object).
- No ``--payload-kind`` value is special-cased — an arbitrary kind is
  accepted, proving payload-agnosticism at the CLI layer too.
- R6: no ``--generate-key`` flag exists anywhere on this command.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.contracts.node_message_envelope import NodeMessageEnvelope
from mrr.crypto.keys import generate_ed25519_keypair
from mrr.domain.identity import new_urn
from mrr.services.cli import federation_main

_NOW = datetime(2026, 7, 26, 9, 0, 0, tzinfo=UTC)
_SENT_AT = _NOW - timedelta(minutes=1)
_EXPIRES_AT = _NOW + timedelta(days=1)

_EXIT_DEPENDENCY_UNAVAILABLE = 2
_EXIT_REFUSED = 3

# ---------------------------------------------------------------------------
# Fixture builders — local to this module (test keys created HERE, never
# committed anywhere; mirrors task-packets/E5-T08.yaml R6).
# ---------------------------------------------------------------------------


def _write_pem_key(path: Path, key: Ed25519PrivateKey) -> None:
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path.write_bytes(pem)


class _Scenario:
    """A fully self-consistent, on-disk fixture set: one sender key, one
    payload file, and the file paths a real ``mrr federation envelope
    sign`` invocation would take.
    """

    def __init__(self, tmp_path: Path, *, payload: dict[str, Any] | None = None) -> None:
        self.tmp_path = tmp_path
        self.private_key, _ = generate_ed25519_keypair()
        self.sender_node_id = new_urn("node")
        self.sender_practice_id = new_urn("practice")
        self.recipient_node_id = new_urn("node")
        self.message_id = new_urn("node-message-envelope")
        self.key_id = "kid:fixture-envelope-sign-cli"

        self.key_path = tmp_path / "sender.key.pem"
        _write_pem_key(self.key_path, self.private_key)

        self.payload = (
            payload
            if payload is not None
            else {"kind": "VerificationResult", "content_hash": "sha256:" + "b" * 64}
        )
        self.payload_path = tmp_path / "payload.json"
        self.payload_path.write_text(json.dumps(self.payload), encoding="utf-8")

        self.output_path = tmp_path / "outbox" / "envelope.json"

    def argv(self, *, payload_kind: str = "VerificationResult") -> list[str]:
        return [
            "envelope",
            "sign",
            "--payload",
            str(self.payload_path),
            "--payload-kind",
            payload_kind,
            "--message-id",
            self.message_id,
            "--sender-node-id",
            self.sender_node_id,
            "--sender-practice-id",
            self.sender_practice_id,
            "--recipient-node-id",
            self.recipient_node_id,
            "--sent-at",
            _SENT_AT.isoformat(),
            "--expires-at",
            _EXPIRES_AT.isoformat(),
            "--key-file",
            str(self.key_path),
            "--key-id",
            self.key_id,
            "--output",
            str(self.output_path),
        ]


# ---------------------------------------------------------------------------
# Happy path.
# ---------------------------------------------------------------------------


def test_envelope_sign_writes_a_valid_round_trippable_envelope(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario = _Scenario(tmp_path)

    exit_code = federation_main.main(scenario.argv())

    assert exit_code == 0
    assert scenario.output_path.is_file()

    document = json.loads(scenario.output_path.read_text(encoding="utf-8"))
    envelope = NodeMessageEnvelope.model_validate(document)
    assert envelope.message_id == scenario.message_id
    assert envelope.sender_node_id == scenario.sender_node_id
    assert envelope.sender_practice_id == scenario.sender_practice_id
    assert envelope.recipient_node_id == scenario.recipient_node_id
    assert envelope.payload_kind == "VerificationResult"
    assert envelope.payload_content_hash == scenario.payload["content_hash"]
    assert envelope.payload == scenario.payload

    captured = capsys.readouterr()
    reported = json.loads(captured.out)
    assert reported["message_id"] == scenario.message_id
    assert reported["payload_kind"] == "VerificationResult"
    assert reported["sender_node_id"] == scenario.sender_node_id
    assert reported["sender_practice_id"] == scenario.sender_practice_id
    assert reported["recipient_node_id"] == scenario.recipient_node_id
    assert reported["output"] == str(scenario.output_path)
    assert isinstance(reported["envelope_sha256"], str)


def test_envelope_sign_is_payload_kind_agnostic(tmp_path: Path) -> None:
    scenario = _Scenario(tmp_path)

    exit_code = federation_main.main(scenario.argv(payload_kind="AnArbitraryUnseenKind"))

    assert exit_code == 0
    document = json.loads(scenario.output_path.read_text(encoding="utf-8"))
    assert document["payload_kind"] == "AnArbitraryUnseenKind"


# ---------------------------------------------------------------------------
# --output ordering (mirrors outbox write's own MRR-NFR-012 discipline).
# ---------------------------------------------------------------------------


def test_envelope_sign_refuses_an_existing_output_path(tmp_path: Path) -> None:
    scenario = _Scenario(tmp_path)
    scenario.output_path.parent.mkdir(parents=True, exist_ok=True)
    scenario.output_path.write_bytes(b"already here")

    exit_code = federation_main.main(scenario.argv())

    assert exit_code == _EXIT_REFUSED
    assert scenario.output_path.read_bytes() == b"already here"


# ---------------------------------------------------------------------------
# Dependency-tier failures (exit 2), never a fabricated substitute.
# ---------------------------------------------------------------------------


def test_envelope_sign_missing_payload_file_is_a_dependency_failure(tmp_path: Path) -> None:
    scenario = _Scenario(tmp_path)
    argv = scenario.argv()
    argv[argv.index("--payload") + 1] = str(tmp_path / "no-such-payload.json")

    exit_code = federation_main.main(argv)

    assert exit_code == _EXIT_DEPENDENCY_UNAVAILABLE
    assert not scenario.output_path.exists()


def test_envelope_sign_payload_not_valid_json_is_a_dependency_failure(tmp_path: Path) -> None:
    scenario = _Scenario(tmp_path)
    scenario.payload_path.write_text("not json at all", encoding="utf-8")

    exit_code = federation_main.main(scenario.argv())

    assert exit_code == _EXIT_DEPENDENCY_UNAVAILABLE
    assert not scenario.output_path.exists()


def test_envelope_sign_payload_not_a_json_object_is_a_dependency_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario = _Scenario(tmp_path)
    scenario.payload_path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    exit_code = federation_main.main(scenario.argv())

    assert exit_code == _EXIT_DEPENDENCY_UNAVAILABLE
    assert not scenario.output_path.exists()
    assert "must be a JSON object" in capsys.readouterr().err


def test_envelope_sign_missing_key_file_is_a_dependency_failure(tmp_path: Path) -> None:
    scenario = _Scenario(tmp_path)
    argv = scenario.argv()
    argv[argv.index("--key-file") + 1] = str(tmp_path / "no-such-key.pem")

    exit_code = federation_main.main(argv)

    assert exit_code == _EXIT_DEPENDENCY_UNAVAILABLE
    assert not scenario.output_path.exists()


def test_envelope_sign_key_file_not_ed25519_is_a_dependency_failure(tmp_path: Path) -> None:
    scenario = _Scenario(tmp_path)
    bad_key_path = tmp_path / "not-a-key.pem"
    bad_key_path.write_text("this is not a PEM key at all", encoding="utf-8")
    argv = scenario.argv()
    argv[argv.index("--key-file") + 1] = str(bad_key_path)

    exit_code = federation_main.main(argv)

    assert exit_code == _EXIT_DEPENDENCY_UNAVAILABLE
    assert not scenario.output_path.exists()


# ---------------------------------------------------------------------------
# The hard rule, at the CLI layer: a payload without its own content_hash.
# ---------------------------------------------------------------------------


def test_envelope_sign_payload_missing_content_hash_is_a_typed_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario = _Scenario(tmp_path, payload={"kind": "VerificationResult"})

    exit_code = federation_main.main(scenario.argv())

    assert exit_code == _EXIT_DEPENDENCY_UNAVAILABLE
    assert not scenario.output_path.exists()
    assert "EnvelopePayloadMissingContentHashError" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Contract-level structural failures propagate as a dependency failure too
# (mirrors outbox write's identical ValidationError/ValueError bucket).
# ---------------------------------------------------------------------------


def test_envelope_sign_expires_at_not_after_sent_at_is_a_dependency_failure(
    tmp_path: Path,
) -> None:
    scenario = _Scenario(tmp_path)
    argv = scenario.argv()
    argv[argv.index("--sent-at") + 1] = _EXPIRES_AT.isoformat()
    argv[argv.index("--expires-at") + 1] = _SENT_AT.isoformat()

    exit_code = federation_main.main(argv)

    assert exit_code == _EXIT_DEPENDENCY_UNAVAILABLE
    assert not scenario.output_path.exists()


# ---------------------------------------------------------------------------
# R6: no --generate-key convenience anywhere on this command.
# ---------------------------------------------------------------------------


def test_no_generate_key_flag_exists_on_envelope_sign(capsys: pytest.CaptureFixture[str]) -> None:
    help_text = ""
    for argv in (["envelope", "--help"], ["envelope", "sign", "--help"]):
        try:
            federation_main.main(argv)
        except SystemExit as exc:
            assert exc.code == 0
        help_text += capsys.readouterr().out

    assert "--generate-key" not in help_text
    assert "--key-file" in help_text
