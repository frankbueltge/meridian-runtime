"""Integration test for task-packets/E5-T11.yaml's own named acceptance
oracle, "the sharp case" (docs/design/2026-07-26-e5-t11-ableitung-praxis-
identitaet.md, "Das Akzeptanz-Orakel"):

    Ein von diesem Kommando erzeugtes Practice-Objekt wird von der
    unveraenderten Empfaengerseite als --trusted-sender-practice akzeptiert,
    fuer ein Buendel, das mit demselben Schluessel signiert wurde.

A ``Practice`` produced by the REAL ``mrr practice init`` console entry
point (``mrr.services.cli.practice_main.main``, network-free, DB-free — the
new E5-T11 operator surface, the only new code this whole chain exercises)
is proven, end to end, to actually function as the counterparty's input:
signing an envelope and an ``OfflineBundle`` with the SAME key, writing the
bundle to a real file, reading it back through the REAL
``mrr.adapters.federation.local.LocalFilesystemBundleTransport``, and having
the UNCHANGED ``mrr.domain.offline_bundle.validate_inbound_bundle`` /
``mrr.domain.envelope_validation.validate_inbound_envelope`` accept it
against a ``mrr.domain.manifest_trust.practice_key_ring`` built, also
UNCHANGED, from that same produced ``Practice`` document.

The counter-case alongside it is equally sharp: a ``Practice`` built from
key A must NOT legitimise an envelope/bundle actually signed with key B —
even when that bundle's own ``signature.key_id`` LIES and claims practice
A's own kid (task-packets/E5-T11.yaml's reviewer_resolution names the exact
pre-existing defect this exploits: "``envelope sign`` accepts ``--key-id``
as an unchecked opaque string" — nothing on the SENDING side stops this;
this test proves the RECEIVING side, ``validate_inbound_bundle``'s own
signature verification, catches it anyway).

Nothing downstream of ``build_self_signed_practice`` is touched by this
task (task-packets/E5-T11.yaml forbidden_changes: ``hashing_policy.py``,
``envelope_validation.py``, ``offline_bundle.py``, ``key_management.py``,
``packages/contracts/**``, ``federation_main.py``) — this test is the
first-ever demonstration that a Meridian-published identity, built by the
new command, actually functions as Ulysses' own input. The keys and
Practice documents used here are throwaway TEST fixtures created inside
this module — no real Meridian or Ulysses identity is invented or committed
(task-packets/E5-T11.yaml explicitly_not: "No identity for a foreign
practice ... no Practice minted for Ulysses").
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.adapters.federation.local import LocalFilesystemBundleTransport
from mrr.contracts.practice import Practice
from mrr.crypto.exceptions import SignatureVerificationError
from mrr.crypto.keys import generate_ed25519_keypair
from mrr.domain.envelope_validation import validate_inbound_envelope
from mrr.domain.identity import new_urn
from mrr.domain.manifest_trust import practice_key_ring
from mrr.domain.offline_bundle import validate_inbound_bundle
from mrr.services.cli import federation_main, practice_main

_NOW = datetime(2026, 7, 26, 11, 0, 0, tzinfo=UTC)
_SENT_AT = _NOW - timedelta(minutes=5)
_EXPIRES_AT = _NOW + timedelta(days=7)
_VALID_FROM = _NOW - timedelta(days=1)
_VALID_UNTIL = _NOW + timedelta(days=365)
_BUNDLE_CREATED_AT = _NOW - timedelta(minutes=5)
_BUNDLE_EXPIRES_AT = _NOW + timedelta(days=7)


def _never_processed(_: str) -> bool:
    return False


def _write_pem_key(path: Path, key: Ed25519PrivateKey) -> None:
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path.write_bytes(pem)


def _init_practice_argv(
    *, key_path: Path, output_path: Path, name: str = "Throwaway Test Practice"
) -> list[str]:
    """The full ``mrr practice init`` argv for a throwaway TEST practice —
    never a real Meridian/Ulysses identity (task-packets/E5-T11.yaml
    explicitly_not).
    """
    return [
        "init",
        "--key-file",
        str(key_path),
        "--name",
        name,
        "--description",
        "A throwaway test practice -- never a real Meridian/Ulysses identity.",
        "--governance-contact",
        "mailto:governance@fixture.invalid",
        "--policy-version",
        "policy-2026-07-01",
        "--max-disclosure",
        "PUBLIC",
        "--trust-statement",
        "fixture",
        "--valid-from",
        _VALID_FROM.isoformat(),
        "--valid-until",
        _VALID_UNTIL.isoformat(),
        "--created-by",
        new_urn("agent-role"),
        "--output",
        str(output_path),
    ]


def _sign_envelope_argv(
    *,
    payload_path: Path,
    key_path: Path,
    key_id: str,
    sender_practice_id: str,
    sender_node_id: str,
    recipient_node_id: str,
    message_id: str,
    output_path: Path,
) -> list[str]:
    return [
        "envelope",
        "sign",
        "--payload",
        str(payload_path),
        "--payload-kind",
        "VerificationResult",
        "--message-id",
        message_id,
        "--sender-node-id",
        sender_node_id,
        "--sender-practice-id",
        sender_practice_id,
        "--recipient-node-id",
        recipient_node_id,
        "--sent-at",
        _SENT_AT.isoformat(),
        "--expires-at",
        _EXPIRES_AT.isoformat(),
        "--key-file",
        str(key_path),
        "--key-id",
        key_id,
        "--output",
        str(output_path),
    ]


def _write_bundle_argv(
    *,
    envelope_path: Path,
    key_path: Path,
    key_id: str,
    sender_practice_id: str,
    sender_node_id: str,
    recipient_node_id: str,
    bundle_id: str,
    output_path: Path,
) -> list[str]:
    return [
        "outbox",
        "write",
        "--envelope",
        str(envelope_path),
        "--bundle-id",
        bundle_id,
        "--bundle-nonce",
        "n" * 16,
        "--sender-node-id",
        sender_node_id,
        "--sender-practice-id",
        sender_practice_id,
        "--recipient-node-id",
        recipient_node_id,
        "--created-at",
        _BUNDLE_CREATED_AT.isoformat(),
        "--expires-at",
        _BUNDLE_EXPIRES_AT.isoformat(),
        "--key-file",
        str(key_path),
        "--key-id",
        key_id,
        "--output",
        str(output_path),
    ]


# ---------------------------------------------------------------------------
# THE SHARP CASE: a Practice this command produced is accepted, whole, by
# the UNCHANGED receiver, for a bundle signed with the SAME key.
# ---------------------------------------------------------------------------


def test_a_produced_practice_is_accepted_for_a_bundle_signed_with_its_own_key(
    tmp_path: Path,
) -> None:
    private_key, _ = generate_ed25519_keypair()
    sender_node_id = new_urn("node")
    recipient_node_id = new_urn("node")

    key_path = tmp_path / "meridian.key.pem"
    _write_pem_key(key_path, private_key)

    # --- 1. mrr practice init: the new E5-T11 operator surface builds and
    #        self-signs the Practice document.
    practice_path = tmp_path / "practice.json"
    init_exit = practice_main.main(
        _init_practice_argv(key_path=key_path, output_path=practice_path)
    )
    assert init_exit == 0
    assert practice_path.is_file()

    practice = Practice.model_validate(json.loads(practice_path.read_text(encoding="utf-8")))
    assert practice.practice_id == practice.id
    key_id = practice.keys[0].kid

    # --- 2. mrr federation envelope sign / outbox write: the UNCHANGED
    #        E5-T10/E5-T06/E5-T08 path, signed with the EXACT SAME key.
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(
        json.dumps({"kind": "VerificationResult", "content_hash": "sha256:" + "b" * 64}),
        encoding="utf-8",
    )
    envelope_path = tmp_path / "envelope.json"
    message_id = new_urn("node-message-envelope")
    sign_exit = federation_main.main(
        _sign_envelope_argv(
            payload_path=payload_path,
            key_path=key_path,
            key_id=key_id,
            sender_practice_id=practice.id,
            sender_node_id=sender_node_id,
            recipient_node_id=recipient_node_id,
            message_id=message_id,
            output_path=envelope_path,
        )
    )
    assert sign_exit == 0

    bundle_path = tmp_path / "outbox" / "bundle.json"
    bundle_id = new_urn("offline-bundle")
    write_exit = federation_main.main(
        _write_bundle_argv(
            envelope_path=envelope_path,
            key_path=key_path,
            key_id=key_id,
            sender_practice_id=practice.id,
            sender_node_id=sender_node_id,
            recipient_node_id=recipient_node_id,
            bundle_id=bundle_id,
            output_path=bundle_path,
        )
    )
    assert write_exit == 0

    # --- 3. Read the bundle FILE back (the real transport adapter) and run
    #        the UNCHANGED validate_inbound_bundle against a KeyRing built,
    #        also UNCHANGED, from the produced Practice document.
    bundle = LocalFilesystemBundleTransport().read_bundle(bundle_path)
    ring = practice_key_ring(practice)
    accepted_envelopes = validate_inbound_bundle(
        bundle,
        this_node_id=recipient_node_id,
        trusted_sender_practice_id=practice.id,
        ring=ring,
        already_processed=_never_processed,
        at=_NOW,
    )
    assert [envelope.message_id for envelope in accepted_envelopes] == [message_id]

    # --- 4. The second stage the CLI's own inbox accept never performs:
    #        the UNCHANGED per-envelope validate_inbound_envelope.
    accepted_envelope = accepted_envelopes[0]
    validate_inbound_envelope(
        accepted_envelope,
        this_node_id=recipient_node_id,
        trusted_sender_practice_id=practice.id,
        ring=ring,
        already_processed=_never_processed,
        at=_NOW,
    )


# ---------------------------------------------------------------------------
# THE COUNTER-CASE: a Practice built from key A does NOT legitimise an
# envelope actually signed with key B, even when that envelope's signature
# LIES and claims key A's own kid.
# ---------------------------------------------------------------------------


def test_a_practice_built_from_key_a_does_not_legitimise_a_bundle_signed_with_key_b(
    tmp_path: Path,
) -> None:
    key_a, _ = generate_ed25519_keypair()
    key_b, _ = generate_ed25519_keypair()
    sender_node_id = new_urn("node")
    recipient_node_id = new_urn("node")

    key_a_path = tmp_path / "practice-a.key.pem"
    _write_pem_key(key_a_path, key_a)
    key_b_path = tmp_path / "practice-b.key.pem"
    _write_pem_key(key_b_path, key_b)

    # --- 1. mrr practice init: Practice A, self-signed with key A only.
    practice_a_path = tmp_path / "practice-a.json"
    assert (
        practice_main.main(
            _init_practice_argv(
                key_path=key_a_path, output_path=practice_a_path, name="Practice A (test)"
            )
        )
        == 0
    )
    practice_a = Practice.model_validate(json.loads(practice_a_path.read_text(encoding="utf-8")))
    practice_a_kid = practice_a.keys[0].kid

    # --- 2. An envelope/bundle actually signed with key B, but whose
    #        signature CLAIMS practice A's own kid — the exact pre-existing
    #        gap the governance commit names ("--key-id accepts an
    #        unchecked opaque string"): nothing on the SENDING side stops
    #        this; it is the RECEIVER's job to catch it.
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(
        json.dumps({"kind": "VerificationResult", "content_hash": "sha256:" + "c" * 64}),
        encoding="utf-8",
    )
    envelope_path = tmp_path / "envelope-signed-by-b.json"
    message_id = new_urn("node-message-envelope")
    assert (
        federation_main.main(
            _sign_envelope_argv(
                payload_path=payload_path,
                key_path=key_b_path,
                key_id=practice_a_kid,
                sender_practice_id=practice_a.id,
                sender_node_id=sender_node_id,
                recipient_node_id=recipient_node_id,
                message_id=message_id,
                output_path=envelope_path,
            )
        )
        == 0
    )

    bundle_path = tmp_path / "outbox" / "bundle-signed-by-b.json"
    bundle_id = new_urn("offline-bundle")
    assert (
        federation_main.main(
            _write_bundle_argv(
                envelope_path=envelope_path,
                key_path=key_b_path,
                key_id=practice_a_kid,
                sender_practice_id=practice_a.id,
                sender_node_id=sender_node_id,
                recipient_node_id=recipient_node_id,
                bundle_id=bundle_id,
                output_path=bundle_path,
            )
        )
        == 0
    )

    # --- 3. The UNCHANGED receiver, anchored to Practice A's own ring,
    #        must refuse this bundle: the claimed kid resolves (it IS
    #        Practice A's own kid), so resolution succeeds, but the bytes
    #        were actually signed by key B's private key — the signature
    #        itself does not verify under Practice A's public key.
    bundle = LocalFilesystemBundleTransport().read_bundle(bundle_path)
    ring_a = practice_key_ring(practice_a)

    with pytest.raises(SignatureVerificationError):
        validate_inbound_bundle(
            bundle,
            this_node_id=recipient_node_id,
            trusted_sender_practice_id=practice_a.id,
            ring=ring_a,
            already_processed=_never_processed,
            at=_NOW,
        )
