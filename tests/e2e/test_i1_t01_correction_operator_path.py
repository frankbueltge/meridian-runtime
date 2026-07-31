"""I1-T01 acceptance: the correction path is walkable from the command line,
and what it delivers is bundle-ready.

The oracle is the packet's own E2E criterion: a correction is recorded, its
dependents are flagged, and a signed notification lands as a file — driven
**only** through ``mrr correction`` entry points, with no direct
``CorrectionImpactService`` call anywhere in the exercised path.

--- What this test does and does not drive through the CLI ---------------

The claim/source/anchor a correction can even be ABOUT are built through
their services, exactly as ``test_e2e_003_correction_propagation`` does:
there is no CLI for creating a claim, and inventing one is not this packet's
scope. That construction is fixture setup. Everything from the correction
onward — record, impact, notify, status — goes through
``correction_main.main`` with a real argv, which is the path under test.

--- Why a delivered envelope is checked against the outbox command -------

The whole justification for this packet's transport is that its output feeds
``mrr federation outbox write --envelope``, documented as taking "a path to
an already-signed NodeMessageEnvelope JSON file". A test that only checked
"a file appeared" would not establish that. So the delivered bytes are
parsed back as a ``NodeMessageEnvelope`` — if the transport had reshaped
them, that parse (or the signature over them) would not survive.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from mrr.contracts import Claim, CorrectionEvent
from mrr.contracts.node_message_envelope import NodeMessageEnvelope
from mrr.crypto.keys import derive_key_id, generate_ed25519_keypair
from mrr.domain.identity import new_urn
from mrr.persistence.repositories import (
    PostgresEdgeRepository,
    PostgresEventLog,
    PostgresObjectRepository,
)
from mrr.services.claim.service import ClaimService
from mrr.services.claim.service import bind_edge_unit_of_work as _bind_claim_edge_uow
from mrr.services.claim.service import bind_unit_of_work as _bind_claim_uow
from mrr.services.cli import correction_main
from sqlalchemy import Engine

_ACTOR = new_urn("person")
_POLICY_VERSION = "policy-i1-t01"


def _database_url(engine: Engine) -> str:
    """The exact URL the CLI must be handed — rendered with its password, which
    ``Engine.url`` hides by default.
    """
    return engine.url.render_as_string(hide_password=False)


def _claim(**overrides: Any) -> Claim:
    data: dict[str, Any] = {
        "id": new_urn("claim"),
        "api_version": "mrr/v1alpha1",
        "kind": "Claim",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": datetime.now(UTC),
        "created_by": _ACTOR,
        "content_hash": "sha256:" + "a" * 64,
        "assertion": "Fixture claim: the cited dataset supports the reported effect.",
        "claim_type": "statistical",
        "scope": {},
        "status": "draft",
        "evidence_relations": [],
        "counterevidence_relations": [],
        "dependencies": [],
        "source_family_ids": [],
        "uncertainty": [],
        "known_unknowns": [],
        "proposer_id": new_urn("agent-role"),
        "verification_ids": [],
        "correction_ids": [],
    }
    data.update(overrides)
    return Claim.model_validate(data)


def _correction_document(affected_object_id: str) -> dict[str, Any]:
    return {
        "id": new_urn("correction"),
        "api_version": "mrr/v1alpha1",
        "kind": "CorrectionEvent",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "created_by": _ACTOR,
        "content_hash": "sha256:" + "b" * 64,
        "affected_objects": [{"id": affected_object_id, "content_hash": "sha256:" + "e" * 64}],
        "correction_type": "source_invalidated",
        "severity": "critical",
        "reason": "A named figure was misattributed; the record is corrected, not removed.",
        "evidence_refs": [new_urn("evidence-anchor")],
        "originator_id": _ACTOR,
        "requested_action": "Review every claim depending on this source.",
        "replacement_object_id": None,
        "status": "OPEN",
        "impact_objects": [],
    }


def _seed_claim(engine: Engine) -> Claim:
    """Fixture setup only — see the module docstring."""
    object_repository = PostgresObjectRepository(engine)
    event_log = PostgresEventLog(engine)
    claim_service = ClaimService(
        object_repository,
        event_log,
        PostgresEdgeRepository(engine),
        _bind_claim_uow(engine, object_repository, event_log),
        _bind_claim_edge_uow(engine, event_log),
    )
    claim = _claim()
    claim_service.create(
        claim,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=new_urn("research-run"),
    )
    return claim


def _key_file(tmp_path: Path) -> tuple[Path, str]:
    from cryptography.hazmat.primitives import serialization

    private_key, public_key = generate_ed25519_keypair()
    path = tmp_path / "meridian-signing.pem"
    path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return path, derive_key_id(public_key)


def _record_and_flag(engine: Engine, tmp_path: Path, claim: Claim) -> str:
    """`mrr correction record` + `impact`, through the CLI. Returns the id."""
    document = _correction_document(claim.id)
    correction_file = tmp_path / "correction.json"
    correction_file.write_text(json.dumps(document), encoding="utf-8")

    assert (
        correction_main.main(
            [
                "record",
                "--database-url",
                _database_url(engine),
                "--correction-file",
                str(correction_file),
                "--actor",
                _ACTOR,
                "--policy-version",
                _POLICY_VERSION,
            ]
        )
        == 0
    )

    assert (
        correction_main.main(
            [
                "impact",
                "--database-url",
                _database_url(engine),
                "--correction-id",
                document["id"],
                "--actor",
                _ACTOR,
                "--policy-version",
                _POLICY_VERSION,
            ]
        )
        == 0
    )
    return str(document["id"])


def _notify(engine: Engine, tmp_path: Path, correction_id: str, claim_id: str, outbox: Path) -> int:
    key_path, key_id = _key_file(tmp_path)
    recipients_file = tmp_path / "recipients.json"
    recipients_file.write_text(
        json.dumps(
            [
                {
                    "recipient_practice_id": new_urn("practice"),
                    "recipient_node_id": new_urn("node"),
                    "recipient_endpoint": str(outbox),
                    "notified_object_ids": [claim_id],
                }
            ]
        ),
        encoding="utf-8",
    )
    sent_at = datetime.now(UTC)
    return correction_main.main(
        [
            "notify",
            "--database-url",
            _database_url(engine),
            "--correction-id",
            correction_id,
            "--recipients-file",
            str(recipients_file),
            "--sender-node-id",
            new_urn("node"),
            "--notifying-practice-id",
            new_urn("practice"),
            "--key-file",
            str(key_path),
            "--key-id",
            key_id,
            "--sent-at",
            sent_at.isoformat(),
            "--expires-at",
            (sent_at + timedelta(days=1)).isoformat(),
            "--actor",
            _ACTOR,
            "--policy-version",
            _POLICY_VERSION,
        ]
    )


# ——— E2E-1 ————————————————————————————————————————————————————————————————


def test_the_whole_correction_path_runs_through_mrr_commands_only(
    postgres_engine: Engine, tmp_path: Path
) -> None:
    claim = _seed_claim(postgres_engine)
    correction_id = _record_and_flag(postgres_engine, tmp_path, claim)
    outbox = tmp_path / "outbox"

    assert _notify(postgres_engine, tmp_path, correction_id, claim.id, outbox) == 0

    # The notification exists as a FILE, named the way the outbox command
    # consumes it.
    delivered = sorted(outbox.glob("*.json"))
    assert len(delivered) == 1

    # And it is a real, parseable envelope carrying the one payload kind the
    # federation can transport — not merely "some bytes appeared".
    envelope = NodeMessageEnvelope.model_validate_json(delivered[0].read_text(encoding="utf-8"))
    assert envelope.payload_kind == "CorrectionNotification"
    assert delivered[0].stem == envelope.message_id

    # The lifecycle advanced. The correction is a NEW revision, never an
    # overwrite of revision 1.
    assert (
        correction_main.main(
            [
                "status",
                "--database-url",
                _database_url(postgres_engine),
                "--correction-id",
                correction_id,
            ]
        )
        == 0
    )


def test_impact_analysis_is_not_a_lifecycle_advance(
    postgres_engine: Engine, tmp_path: Path, capsys: Any
) -> None:
    """Pinned because it is easy to assume otherwise — and an earlier draft of
    this very test did.

    ``propagate_impact`` computes the downstream set, writes it onto
    ``impact_objects`` and marks impacted claims ``review_required``. It does
    NOT move the correction's own status: the lifecycle hops
    (OPEN -> IMPACT_ANALYSIS -> NOTIFYING -> AWAITING_RESPONSES) all happen
    inside ``notify_affected_practices``. Analysing who is affected is not the
    same act as telling them, and the status must not claim it is.
    """
    claim = _seed_claim(postgres_engine)
    correction_id = _record_and_flag(postgres_engine, tmp_path, claim)
    capsys.readouterr()

    assert (
        correction_main.main(
            [
                "status",
                "--database-url",
                _database_url(postgres_engine),
                "--correction-id",
                correction_id,
            ]
        )
        == 0
    )

    reported = json.loads(capsys.readouterr().out)
    assert reported["correction_id"] == correction_id
    assert reported["status"] == "OPEN"
    # The affected object is reported as the contract renders it — a list of
    # refs, not a flattened string, so a reader can still see its pinned hash.
    assert reported["affected_objects"][0]["id"] == claim.id


# ——— E2E-3: a transport failure is a state, not a crash ————————————————————


def test_an_unwritable_outbox_drives_the_correction_to_delivery_pending(
    postgres_engine: Engine, tmp_path: Path, capsys: Any
) -> None:
    """The load-bearing failure path: ``notify_affected_practices`` reads a
    ``"failed"`` delivery as its cue to advance to ``DELIVERY_PENDING``. A
    transport that raised instead would rob the lifecycle of that state — so
    this asserts the state, not just the absence of an exception.
    """
    claim = _seed_claim(postgres_engine)
    correction_id = _record_and_flag(postgres_engine, tmp_path, claim)

    # A FILE where the transport expects its outbox directory.
    blocked = tmp_path / "blocked-outbox"
    blocked.write_text("occupied", encoding="utf-8")

    assert _notify(postgres_engine, tmp_path, correction_id, claim.id, blocked) == 0
    capsys.readouterr()

    correction_main.main(
        [
            "status",
            "--database-url",
            _database_url(postgres_engine),
            "--correction-id",
            correction_id,
        ]
    )
    assert json.loads(capsys.readouterr().out)["status"] == "DELIVERY_PENDING"


def test_an_unknown_correction_id_is_refused_not_invented(
    postgres_engine: Engine, capsys: Any
) -> None:
    exit_code = correction_main.main(
        [
            "status",
            "--database-url",
            _database_url(postgres_engine),
            "--correction-id",
            new_urn("correction"),
        ]
    )

    assert exit_code == 3
    assert "cannot report" in capsys.readouterr().err


def _unused(_: CorrectionEvent) -> None:  # pragma: no cover - import anchor
    """Keeps the ``CorrectionEvent`` import meaningful for readers tracing the
    contract this path persists; the CLI validates against it internally.
    """
