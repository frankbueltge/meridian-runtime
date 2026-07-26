"""Unit tests for ``mrr practice init`` (task-packets/E5-T11.yaml), DB-free
and network-free — every fixture (key, flags) is built inside this module;
no key or identity is ever committed (mirrors task-packets/E5-T08.yaml R6,
which this command's own ``--key-file`` discipline reuses for the same
reason: ``mrr.services.cli.practice_main``'s module docstring, "No
``--generate-key`` here either").

Covers:

- A full ``practice init`` run exits 0, writes a file that round-trips back
  into a ``Practice``, is accepted by the UNCHANGED ``PublicKeyDescriptor``/
  self-signature validators, and reports ``kid`` in its JSON result payload
  (task-packets/E5-T11.yaml reviewer_resolution point (4): "init reports
  the kid in its result output").
- ``--output`` already existing is refused (exit 3), checked FIRST, and the
  file is left byte-identical.
- Missing/unreadable/malformed ``--key-file`` is a dependency failure
  (exit 2).
- EVERY content flag is required with no default — omitting ANY ONE is a
  typed refusal (argparse's own exit code 2) and no ``--output`` file is
  ever written (task-packets/E5-T11.yaml acceptance criteria).
- ``--valid-until`` not strictly after ``--valid-from`` is a dependency
  failure (a ``pydantic.ValidationError`` from ``PublicKeyDescriptor``
  itself).
- ``--capability-registry-endpoint`` is the one genuinely optional flag.
- No private key material (PEM text, its base64 body, or the raw private
  bytes in any encoding) ever reaches stdout, stderr, or the written file.
- No ``--generate-key`` flag exists anywhere on this command.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.contracts.practice import Practice
from mrr.crypto.keys import generate_ed25519_keypair
from mrr.services.cli import practice_main

_NOW = datetime(2026, 7, 26, 9, 0, 0, tzinfo=UTC)
_VALID_FROM = _NOW - timedelta(days=1)
_VALID_UNTIL = _NOW + timedelta(days=365)

_EXIT_DEPENDENCY_UNAVAILABLE = 2
_EXIT_REFUSED = 3

_REQUIRED_FLAG_TO_VALUE: dict[str, str] = {
    "--key-file": "__KEY_FILE__",  # substituted with the real key path below
    "--name": "Fixture Practice",
    "--description": "A fixture practice for the CLI unit tests.",
    "--governance-contact": "mailto:governance@fixture.invalid",
    "--policy-version": "policy-2026-07-01",
    "--max-disclosure": "PUBLIC",
    "--trust-statement": "fixture",
    "--valid-from": _VALID_FROM.isoformat(),
    "--valid-until": _VALID_UNTIL.isoformat(),
    "--created-by": "urn:mrr:agent-role:01J00000000000000000000099",
    "--output": "__OUTPUT__",  # substituted with the real output path below
}


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
    """A fully self-consistent, on-disk fixture set: one practice key and
    the file paths a real ``mrr practice init`` invocation would take.
    """

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.private_key, self.public_key = generate_ed25519_keypair()

        self.key_path = tmp_path / "practice.key.pem"
        _write_pem_key(self.key_path, self.private_key)

        self.output_path = tmp_path / "practice.json"

    def argv(self, *, overrides: dict[str, str | None] | None = None) -> list[str]:
        values: dict[str, str | None] = dict(_REQUIRED_FLAG_TO_VALUE)
        values["--key-file"] = str(self.key_path)
        values["--output"] = str(self.output_path)
        if overrides:
            values.update(overrides)

        argv: list[str] = ["init"]
        for flag, value in values.items():
            if value is None:
                continue
            argv += [flag, value]
        return argv


# ---------------------------------------------------------------------------
# Happy path.
# ---------------------------------------------------------------------------


def test_practice_init_writes_a_valid_self_signed_round_trippable_practice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario = _Scenario(tmp_path)

    exit_code = practice_main.main(scenario.argv())

    assert exit_code == 0
    assert scenario.output_path.is_file()

    document = json.loads(scenario.output_path.read_text(encoding="utf-8"))
    practice = Practice.model_validate(document)
    assert practice.practice_id == practice.id
    assert practice.name == "Fixture Practice"
    assert practice.description == "A fixture practice for the CLI unit tests."
    assert practice.governance_contacts == ["mailto:governance@fixture.invalid"]
    assert practice.supported_policy_versions == ["policy-2026-07-01"]
    assert practice.disclosure.max_disclosure == "PUBLIC"
    assert practice.disclosure.trust_statement == "fixture"
    assert practice.capability_registry_endpoint is None
    assert practice.signature is not None
    assert practice.signature.signer_practice_id == practice.id
    assert practice.signature.key_id == practice.keys[0].kid

    captured = capsys.readouterr()
    reported = json.loads(captured.out)
    assert reported["output"] == str(scenario.output_path)
    assert reported["id"] == practice.id
    assert reported["name"] == "Fixture Practice"
    assert reported["kid"] == practice.keys[0].kid
    assert isinstance(reported["practice_sha256"], str)


def test_practice_init_carries_capability_registry_endpoint_when_given(tmp_path: Path) -> None:
    scenario = _Scenario(tmp_path)

    exit_code = practice_main.main(
        scenario.argv(
            overrides={
                "--capability-registry-endpoint": "https://fixture.invalid/capability-registry"
            }
        )
    )

    assert exit_code == 0
    document = json.loads(scenario.output_path.read_text(encoding="utf-8"))
    assert document["capability_registry_endpoint"] == "https://fixture.invalid/capability-registry"


# ---------------------------------------------------------------------------
# --output ordering (mirrors envelope sign's own MRR-NFR-012 discipline).
# ---------------------------------------------------------------------------


def test_practice_init_refuses_an_existing_output_path(tmp_path: Path) -> None:
    scenario = _Scenario(tmp_path)
    scenario.output_path.write_bytes(b"already here")

    exit_code = practice_main.main(scenario.argv())

    assert exit_code == _EXIT_REFUSED
    assert scenario.output_path.read_bytes() == b"already here"


# ---------------------------------------------------------------------------
# Dependency-tier failures (exit 2), never a fabricated substitute.
# ---------------------------------------------------------------------------


def test_practice_init_missing_key_file_is_a_dependency_failure(tmp_path: Path) -> None:
    scenario = _Scenario(tmp_path)

    exit_code = practice_main.main(
        scenario.argv(overrides={"--key-file": str(tmp_path / "no-such-key.pem")})
    )

    assert exit_code == _EXIT_DEPENDENCY_UNAVAILABLE
    assert not scenario.output_path.exists()


def test_practice_init_key_file_not_ed25519_is_a_dependency_failure(tmp_path: Path) -> None:
    scenario = _Scenario(tmp_path)
    bad_key_path = tmp_path / "not-a-key.pem"
    bad_key_path.write_text("this is not a PEM key at all", encoding="utf-8")

    exit_code = practice_main.main(scenario.argv(overrides={"--key-file": str(bad_key_path)}))

    assert exit_code == _EXIT_DEPENDENCY_UNAVAILABLE
    assert not scenario.output_path.exists()


def test_practice_init_valid_until_not_after_valid_from_is_a_dependency_failure(
    tmp_path: Path,
) -> None:
    scenario = _Scenario(tmp_path)

    exit_code = practice_main.main(
        scenario.argv(
            overrides={
                "--valid-from": _VALID_UNTIL.isoformat(),
                "--valid-until": _VALID_FROM.isoformat(),
            }
        )
    )

    assert exit_code == _EXIT_DEPENDENCY_UNAVAILABLE
    assert not scenario.output_path.exists()


def test_practice_init_malformed_created_by_urn_is_a_dependency_failure(tmp_path: Path) -> None:
    scenario = _Scenario(tmp_path)

    exit_code = practice_main.main(scenario.argv(overrides={"--created-by": "not-a-urn-at-all"}))

    assert exit_code == _EXIT_DEPENDENCY_UNAVAILABLE
    assert not scenario.output_path.exists()


# ---------------------------------------------------------------------------
# Every content field is required with no default — omitting ANY ONE is a
# typed refusal (argparse's own exit code 2) and nothing is written.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing_flag", sorted(_REQUIRED_FLAG_TO_VALUE))
def test_omitting_any_required_flag_is_a_typed_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], missing_flag: str
) -> None:
    scenario = _Scenario(tmp_path)
    argv = scenario.argv(overrides={missing_flag: None})

    with pytest.raises(SystemExit) as excinfo:
        practice_main.main(argv)

    assert excinfo.value.code == _EXIT_DEPENDENCY_UNAVAILABLE
    assert not scenario.output_path.exists()
    capsys.readouterr()  # drain argparse's own usage/error output


def test_invalid_max_disclosure_choice_is_a_typed_refusal(tmp_path: Path) -> None:
    scenario = _Scenario(tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        practice_main.main(scenario.argv(overrides={"--max-disclosure": "NOT-A-REAL-LEVEL"}))

    assert excinfo.value.code == _EXIT_DEPENDENCY_UNAVAILABLE
    assert not scenario.output_path.exists()


# ---------------------------------------------------------------------------
# No private key material ever reaches stdout, stderr, or the written file.
# ---------------------------------------------------------------------------


def test_no_private_key_material_reaches_output_or_stdio(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario = _Scenario(tmp_path)
    raw_private_bytes = scenario.private_key.private_bytes_raw()
    private_base64 = base64.b64encode(raw_private_bytes).decode("ascii")
    pem_text = scenario.key_path.read_text(encoding="utf-8")

    exit_code = practice_main.main(scenario.argv())
    assert exit_code == 0

    captured = capsys.readouterr()
    written_text = scenario.output_path.read_text(encoding="utf-8")

    for surface in (captured.out, captured.err, written_text):
        assert raw_private_bytes.hex() not in surface
        assert private_base64 not in surface
        assert "PRIVATE KEY" not in surface
        assert pem_text not in surface


# ---------------------------------------------------------------------------
# No --generate-key convenience anywhere on this command.
# ---------------------------------------------------------------------------


def test_no_generate_key_flag_exists_on_practice_init(capsys: pytest.CaptureFixture[str]) -> None:
    help_text = ""
    for argv in (["--help"], ["init", "--help"]):
        try:
            practice_main.main(argv)
        except SystemExit as exc:
            assert exc.code == 0
        help_text += capsys.readouterr().out

    assert "--generate-key" not in help_text
    assert "--key-file" in help_text
