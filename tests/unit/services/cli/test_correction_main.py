"""``mrr correction`` (task-packets/I1-T01.yaml) — the CLI's own oracle.

Two things are under test, and only these two: the documented exit-code map,
and task-packets/E2-T07.yaml's CLI law — R6, "the CLI contains NO domain
logic and NO choreography that belongs to the service". The lifecycle itself
(legal edges, per-recipient idempotence, the ``DELIVERY_PENDING`` hop) is
``CorrectionImpactService``'s and is already covered by its own unit and
integration tiers; re-asserting it here would only duplicate that coverage
and invite the two copies to drift.

Every test below therefore stops at the seam: the orchestration function is
replaced, and what the CLI HANDED it is inspected. A CLI that computed
anything of its own would show up as an argument the caller never supplied.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mrr.services.cli import correction_main


class _StubStored:
    """What the orchestration layer returns — only the fields the CLI reports."""

    def __init__(self) -> None:
        self.id = "urn:mrr:correction-event:01TEST"
        self.revision = 2
        self.body = {"status": "NOTIFYING", "impact_objects": ["urn:mrr:claim:01DEP"]}


def _write(path: Path, document: Any) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _valid_correction_document() -> dict[str, Any]:
    """Shape-only: the CLI must reject this at the CONTRACT, so the test never
    needs a contract-valid correction to prove the ordering invariant.
    """
    return {"kind": "CorrectionEvent", "definitely": "not contract valid"}


# ——— parser wiring ————————————————————————————————————————————————————————


def test_all_four_subcommands_are_registered() -> None:
    parser = correction_main.build_parser()

    for command in ("record", "impact", "notify", "status"):
        assert parser.parse_args([command, *_minimal_flags(command)]).command == command


def _minimal_flags(command: str) -> list[str]:
    common = ["--database-url", "postgresql+psycopg://u:p@h/db"]
    provenance = ["--actor", "urn:mrr:person:01A", "--policy-version", "v1"]
    if command == "record":
        return [*common, "--correction-file", "c.json", *provenance]
    if command == "impact":
        return [*common, "--correction-id", "urn:mrr:correction-event:01A", *provenance]
    if command == "notify":
        return [
            *common,
            "--correction-id",
            "urn:mrr:correction-event:01A",
            "--recipients-file",
            "r.json",
            "--sender-node-id",
            "urn:mrr:node:01A",
            "--notifying-practice-id",
            "urn:mrr:practice:01A",
            "--key-file",
            "k.pem",
            "--key-id",
            "kid",
            "--sent-at",
            "2026-07-31T00:00:00+00:00",
            "--expires-at",
            "2026-08-01T00:00:00+00:00",
            *provenance,
        ]
    return [*common, "--correction-id", "urn:mrr:correction-event:01A"]


# ——— exit code 2: a dependency is unavailable ——————————————————————————————


def test_record_refuses_an_unreadable_correction_file(tmp_path: Path, capsys: Any) -> None:
    args = correction_main.build_parser().parse_args(
        [
            "record",
            *_minimal_flags("record")[:2],
            "--correction-file",
            str(tmp_path / "gone.json"),
            "--actor",
            "urn:mrr:person:01A",
            "--policy-version",
            "v1",
        ]
    )

    assert correction_main.run_command(args) == 2
    assert "cannot read" in capsys.readouterr().err


def test_record_refuses_invalid_json(tmp_path: Path, capsys: Any) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")

    assert _run_record(broken) == 2
    assert "not valid JSON" in capsys.readouterr().err


def test_record_refuses_a_bare_json_null(tmp_path: Path, capsys: Any) -> None:
    """A ``null`` document must not be mistaken for the read-failure sentinel."""
    assert _run_record(_write(tmp_path / "null.json", None)) == 2
    assert "bare JSON null" in capsys.readouterr().err


def test_record_refuses_a_contract_invalid_document(tmp_path: Path, capsys: Any) -> None:
    document = _write(tmp_path / "bad.json", _valid_correction_document())

    assert _run_record(document) == 2
    assert "CorrectionEvent contract" in capsys.readouterr().err


def _run_record(correction_file: Path) -> int:
    args = correction_main.build_parser().parse_args(
        [
            "record",
            "--database-url",
            "postgresql+psycopg://u:p@h/db",
            "--correction-file",
            str(correction_file),
            "--actor",
            "urn:mrr:person:01A",
            "--policy-version",
            "v1",
        ]
    )
    return correction_main.run_command(args)


def test_a_contract_invalid_file_never_opens_a_database_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """task-packets/I1-T01.yaml's ordering invariant, stated as an oracle: the
    only path to a connection is replaced by one that fails the test if it is
    reached at all.
    """

    def _forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a contract-invalid file must never reach the database")

    monkeypatch.setattr(correction_main, "_connected_engine", _forbidden)

    assert _run_record(_write(tmp_path / "bad.json", _valid_correction_document())) == 2


# ——— the CLI law: passthrough, no choreography ————————————————————————————


def test_impact_passes_its_flags_through_and_computes_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    seen: dict[str, Any] = {}

    monkeypatch.setattr(correction_main, "_connected_engine", lambda _url: _FakeEngine())

    def _spy(_engine: Any, **kwargs: Any) -> _StubStored:
        seen.update(kwargs)
        return _StubStored()

    monkeypatch.setattr(correction_main, "propagate_correction_impact", _spy)

    args = correction_main.build_parser().parse_args(["impact", *_minimal_flags("impact")])
    assert correction_main.run_command(args) == 0

    # Exactly the caller's own values — nothing derived, nothing defaulted
    # behind the operator's back.
    assert seen == {
        "correction_id": "urn:mrr:correction-event:01A",
        "actor": "urn:mrr:person:01A",
        "policy_version": "v1",
        "correlation_id": None,
    }
    assert json.loads(capsys.readouterr().out)["revision"] == 2


def test_notify_hands_over_the_offline_transport_and_the_recipients_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mrr.adapters.federation.local import LocalFilesystemEnvelopeTransport

    recipients_file = _write(
        tmp_path / "recipients.json",
        [
            {
                "recipient_practice_id": "urn:mrr:practice:01ULYSSES",
                "recipient_node_id": "urn:mrr:node:01ULYSSES",
                "recipient_endpoint": str(tmp_path / "outbox"),
                "notified_object_ids": ["urn:mrr:claim:01DEP"],
            }
        ],
    )
    key_file = _make_key_file(tmp_path)

    seen: dict[str, Any] = {}
    monkeypatch.setattr(correction_main, "_connected_engine", lambda _url: _FakeEngine())
    monkeypatch.setattr(
        correction_main,
        "notify_correction_recipients",
        lambda _engine, **kwargs: (seen.update(kwargs), _StubStored())[1],
    )

    flags = _minimal_flags("notify")
    flags[flags.index("r.json")] = str(recipients_file)
    flags[flags.index("k.pem")] = str(key_file)
    args = correction_main.build_parser().parse_args(["notify", *flags])

    assert correction_main.run_command(args) == 0
    assert isinstance(seen["transport"], LocalFilesystemEnvelopeTransport)
    assert [one.recipient_practice_id for one in seen["recipients"]] == [
        "urn:mrr:practice:01ULYSSES"
    ]
    # The endpoint is the recipient's own, taken from the file — the CLI has no
    # global outbox flag that could override it.
    assert seen["recipients"][0].recipient_endpoint == str(tmp_path / "outbox")


def test_notify_refuses_a_recipients_file_that_is_not_an_array(
    tmp_path: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(correction_main, "_connected_engine", lambda _url: _FakeEngine())
    recipients_file = _write(tmp_path / "recipients.json", {"not": "an array"})
    key_file = _make_key_file(tmp_path)

    flags = _minimal_flags("notify")
    flags[flags.index("r.json")] = str(recipients_file)
    flags[flags.index("k.pem")] = str(key_file)

    args = correction_main.build_parser().parse_args(["notify", *flags])
    assert correction_main.run_command(args) == 2
    assert "non-empty JSON array" in capsys.readouterr().err


def test_notify_refuses_a_recipient_missing_its_endpoint(
    tmp_path: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(correction_main, "_connected_engine", lambda _url: _FakeEngine())
    recipients_file = _write(
        tmp_path / "recipients.json",
        [
            {
                "recipient_practice_id": "urn:mrr:practice:01A",
                "recipient_node_id": "urn:mrr:node:01A",
            }
        ],
    )
    key_file = _make_key_file(tmp_path)

    flags = _minimal_flags("notify")
    flags[flags.index("r.json")] = str(recipients_file)
    flags[flags.index("k.pem")] = str(key_file)

    args = correction_main.build_parser().parse_args(["notify", *flags])
    assert correction_main.run_command(args) == 2
    assert "recipient_endpoint" in capsys.readouterr().err


def _make_key_file(tmp_path: Path) -> Path:
    from cryptography.hazmat.primitives import serialization
    from mrr.crypto.keys import generate_ed25519_keypair

    private_key, _ = generate_ed25519_keypair()
    key_file = tmp_path / "signing.pem"
    key_file.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return key_file


class _FakeEngine:
    """Stands in for a connected engine: the CLI only ever disposes it."""

    def dispose(self) -> None:
        return None
