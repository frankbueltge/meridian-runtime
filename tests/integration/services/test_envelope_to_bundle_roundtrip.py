"""Integration test for the FULL federation path a real archive object can
now travel — task-packets/E5-T10.yaml's own named acceptance oracle, "the
sharp case": ``corpora/model-collapse/verification/
verification-ulysses-hammond.json`` (a real, committed ``VerificationResult``
carrying its own ``content_hash``, the recorded Hammond dissent between two
practices' verifiers) walks the entire path — ``mrr federation envelope
sign`` -> ``mrr federation outbox write`` -> a real file on disk -> read
back -> the UNCHANGED ``mrr.domain.offline_bundle.validate_inbound_bundle``
-> the UNCHANGED ``mrr.domain.envelope_validation.validate_inbound_envelope``
-> ACCEPTED, under a throwaway test key the receiver is told to trust.

Nothing downstream of ``build_signed_envelope`` is touched by this task
(task-packets/E5-T10.yaml forbidden_changes: ``offline_bundle.py``,
``envelope_validation.py``, ``transfer_trust.py``, ``key_management.py``,
``packages/contracts/**``) — this test is the first-ever demonstration that
a real, committed archive object can be built into a signed envelope at
all AND survive the entire ALREADY-EXISTING chain unmodified. The Practice
document and Ed25519 key used here are throwaway TEST fixtures created
inside this module — no real Meridian or Ulysses identity is invented or
committed (task-packets/E5-T10.yaml explicitly_not).

``mrr federation envelope sign``/``outbox write`` are driven through the
REAL console-script entry point (``mrr.services.cli.federation_main.main``,
network-free, DB-free — see that module's own docstring), exactly as
``tests/unit/services/cli/test_federation_main.py`` already exercises the
outbox/inbox round trip for a synthetic envelope. This test is the first to
do so starting from a REAL, committed payload and finishing the chain all
the way to ``validate_inbound_envelope`` — the CLI's own ``inbox accept``
deliberately stops one step short of that (its own documented "bundle-
verified, NOT yet envelope-validated" two-stage design), so the final leg
here is a direct call to the unchanged domain function, exactly as a real
caller of ``inbox accept`` is instructed to do afterward.

The three negative variants named alongside the sharp case in
docs/design/2026-07-26-e5-t10-derivation-envelope-kante.md are proven
against an envelope BUILT from the REAL Hammond payload too (not a
synthetic one) — but BEFORE bundling, not after: once an envelope is
batched into an ``OfflineBundle``, the bundle's own top-level signature
covers every carried envelope's full body, so tampering an already-bundled
envelope would always fail the BUNDLE's own signature check first, never
reaching the per-envelope checks these three variants are named to
exercise. Proving them against the freshly-signed, not-yet-bundled envelope
is therefore the only way to actually exercise
``validate_inbound_envelope``'s own condition 3 and condition 5 here,
rather than merely re-proving the bundle's own (already E5-T06-tested)
signature check.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from mrr.adapters.federation.local import LocalFilesystemBundleTransport
from mrr.contracts.node_message_envelope import NodeMessageEnvelope
from mrr.contracts.practice import Practice
from mrr.crypto.exceptions import SignatureVerificationError
from mrr.crypto.keys import derive_key_id, encode_public_key, generate_ed25519_keypair
from mrr.domain.envelope_validation import validate_inbound_envelope
from mrr.domain.exceptions import EnvelopePayloadContentHashMismatchError
from mrr.domain.identity import new_urn
from mrr.domain.manifest_trust import practice_key_ring
from mrr.domain.offline_bundle import validate_inbound_bundle
from mrr.services.cli import federation_main

REPO_ROOT = Path(__file__).resolve().parents[3]
HAMMOND_VERIFICATION_PATH = (
    REPO_ROOT / "corpora" / "model-collapse" / "verification" / "verification-ulysses-hammond.json"
)
#: The real, committed content_hash of the Hammond dissent record — asserted
#: directly so this test fails loudly if the corpus artifact it depends on
#: ever changes out from under it, rather than silently testing something
#: else (docs/design/2026-07-26-e5-t10-derivation-envelope-kante.md).
HAMMOND_CONTENT_HASH = "sha256:ba90ee1821e241e3a81e35872186d916db9d6c2397527adcbbfc6d1314bd0aef"

_NOW = datetime(2026, 7, 26, 10, 0, 0, tzinfo=UTC)
_SENT_AT = _NOW - timedelta(minutes=5)
_EXPIRES_AT = _NOW + timedelta(days=7)
_VALID_FROM = _NOW - timedelta(days=1)
_VALID_UNTIL = _NOW + timedelta(days=365)


def _never_processed(_: str) -> bool:
    return False


def _load_hammond_payload() -> dict[str, Any]:
    """Load the real, committed corpus artifact — read only, never written
    to or otherwise modified by this test (task-packets/E5-T10.yaml
    forbidden_changes: ``corpora/**``).
    """
    assert HAMMOND_VERIFICATION_PATH.is_file(), (
        f"expected the real Hammond corpus artifact at {HAMMOND_VERIFICATION_PATH}"
    )
    data: dict[str, Any] = json.loads(HAMMOND_VERIFICATION_PATH.read_text(encoding="utf-8"))
    assert data["content_hash"] == HAMMOND_CONTENT_HASH
    return data


class _TestFederation:
    """A throwaway TEST practice, key, and pair of node ids — never a real
    Meridian or Ulysses identity (task-packets/E5-T10.yaml explicitly_not).
    """

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.private_key, public_key = generate_ed25519_keypair()
        self.key_id = derive_key_id(public_key)
        self.practice_id = new_urn("practice")
        self.sender_node_id = new_urn("node")
        self.recipient_node_id = new_urn("node")

        self.practice = Practice.model_validate(
            {
                "id": self.practice_id,
                "api_version": "mrr/v1alpha1",
                "kind": "Practice",
                "practice_id": self.practice_id,
                "revision": 1,
                "created_at": _NOW,
                "created_by": new_urn("agent-role"),
                "content_hash": "sha256:" + "a" * 64,
                "name": "Throwaway Test Practice (E5-T10 integration test)",
                "description": (
                    "A throwaway test practice — never a real Meridian/Ulysses identity."
                ),
                "keys": [
                    {
                        "kid": self.key_id,
                        "algorithm": "Ed25519",
                        "encoded_public_key": encode_public_key(public_key),
                        "valid_from": _VALID_FROM,
                        "valid_until": _VALID_UNTIL,
                        "state": "active",
                    }
                ],
                "governance_contacts": ["mailto:governance@fixture.invalid"],
                "supported_policy_versions": ["policy-2026-07-01"],
                "disclosure": {"max_disclosure": "PUBLIC", "trust_statement": "fixture"},
            }
        )

        self.key_path = tmp_path / "sender.key.pem"
        self.key_path.write_bytes(
            self.private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

    def ring(self) -> Any:
        return practice_key_ring(self.practice)

    def sign_envelope_argv(
        self, *, payload_path: Path, message_id: str, output_path: Path
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
            self.sender_node_id,
            "--sender-practice-id",
            self.practice_id,
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
            str(output_path),
        ]

    def write_bundle_argv(
        self, *, envelope_path: Path, bundle_id: str, output_path: Path
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
            self.sender_node_id,
            "--sender-practice-id",
            self.practice_id,
            "--recipient-node-id",
            self.recipient_node_id,
            "--created-at",
            _SENT_AT.isoformat(),
            "--expires-at",
            _EXPIRES_AT.isoformat(),
            "--key-file",
            str(self.key_path),
            "--key-id",
            self.key_id,
            "--output",
            str(output_path),
        ]


# ---------------------------------------------------------------------------
# THE SHARP CASE: the real Hammond dissent, the whole way, unchanged
# receiver code.
# ---------------------------------------------------------------------------


def test_the_real_hammond_dissent_travels_the_whole_federation_path(tmp_path: Path) -> None:
    federation = _TestFederation(tmp_path)
    message_id = new_urn("node-message-envelope")
    envelope_path = tmp_path / "hammond-envelope.json"
    bundle_path = tmp_path / "outbox" / "hammond-bundle.json"

    # --- 1. mrr federation envelope sign: the real Hammond payload -> a
    #        signed NodeMessageEnvelope file. The new E5-T10 operator
    #        surface, the only new code this whole chain exercises.
    sign_exit = federation_main.main(
        federation.sign_envelope_argv(
            payload_path=HAMMOND_VERIFICATION_PATH,
            message_id=message_id,
            output_path=envelope_path,
        )
    )
    assert sign_exit == 0
    assert envelope_path.is_file()

    # --- 2. mrr federation outbox write: the UNCHANGED E5-T06/E5-T08 path
    #        bundles the freshly-signed envelope and writes it to a file.
    write_exit = federation_main.main(
        federation.write_bundle_argv(
            envelope_path=envelope_path,
            bundle_id=new_urn("offline-bundle"),
            output_path=bundle_path,
        )
    )
    assert write_exit == 0
    assert bundle_path.is_file()

    # --- 3. Read the bundle FILE back (the real transport adapter, not a
    #        hand-rolled JSON parse) and run the UNCHANGED
    #        validate_inbound_bundle against it.
    bundle = LocalFilesystemBundleTransport().read_bundle(bundle_path)
    accepted_envelopes = validate_inbound_bundle(
        bundle,
        this_node_id=federation.recipient_node_id,
        trusted_sender_practice_id=federation.practice_id,
        ring=federation.ring(),
        already_processed=_never_processed,
        at=_NOW,
    )
    assert [e.message_id for e in accepted_envelopes] == [message_id]

    # --- 4. The second stage the CLI's own inbox accept never performs:
    #        the UNCHANGED per-envelope validate_inbound_envelope. Accepted
    #        means "returns normally" — a failing precondition would raise.
    hammond_envelope = accepted_envelopes[0]
    validate_inbound_envelope(
        hammond_envelope,
        this_node_id=federation.recipient_node_id,
        trusted_sender_practice_id=federation.practice_id,
        ring=federation.ring(),
        already_processed=_never_processed,
        at=_NOW,
    )

    # --- 5. The carried payload really is the real, unmodified Hammond
    #        record — proving this is the actual archive object, not a
    #        look-alike fixture.
    original_hammond = _load_hammond_payload()
    assert hammond_envelope.payload == original_hammond
    assert hammond_envelope.payload_content_hash == HAMMOND_CONTENT_HASH


# ---------------------------------------------------------------------------
# The three negative variants named alongside the sharp case, proven
# against an envelope BUILT from the same real Hammond payload — but before
# bundling (see the module docstring for why).
# ---------------------------------------------------------------------------


def test_hammond_payload_without_content_hash_is_refused_before_any_envelope_is_built(
    tmp_path: Path,
) -> None:
    federation = _TestFederation(tmp_path)
    stripped_hammond = _load_hammond_payload()
    del stripped_hammond["content_hash"]
    stripped_path = tmp_path / "hammond-without-content-hash.json"
    stripped_path.write_text(json.dumps(stripped_hammond), encoding="utf-8")
    envelope_path = tmp_path / "should-not-exist.json"

    sign_exit = federation_main.main(
        federation.sign_envelope_argv(
            payload_path=stripped_path,
            message_id=new_urn("node-message-envelope"),
            output_path=envelope_path,
        )
    )

    assert sign_exit != 0
    assert not envelope_path.exists()


def test_hammond_envelope_with_tampered_payload_content_hash_fails_receivers_condition_3(
    tmp_path: Path,
) -> None:
    federation = _TestFederation(tmp_path)
    envelope_path = tmp_path / "hammond-envelope.json"
    assert (
        federation_main.main(
            federation.sign_envelope_argv(
                payload_path=HAMMOND_VERIFICATION_PATH,
                message_id=new_urn("node-message-envelope"),
                output_path=envelope_path,
            )
        )
        == 0
    )

    document = json.loads(envelope_path.read_text(encoding="utf-8"))
    document["payload_content_hash"] = "sha256:" + "9" * 64
    tampered = NodeMessageEnvelope.model_validate(document)

    with pytest.raises(EnvelopePayloadContentHashMismatchError):
        validate_inbound_envelope(
            tampered,
            this_node_id=federation.recipient_node_id,
            trusted_sender_practice_id=federation.practice_id,
            ring=federation.ring(),
            already_processed=_never_processed,
            at=_NOW,
        )


def test_hammond_envelope_with_one_flipped_byte_fails_signature_verification(
    tmp_path: Path,
) -> None:
    federation = _TestFederation(tmp_path)
    envelope_path = tmp_path / "hammond-envelope.json"
    assert (
        federation_main.main(
            federation.sign_envelope_argv(
                payload_path=HAMMOND_VERIFICATION_PATH,
                message_id=new_urn("node-message-envelope"),
                output_path=envelope_path,
            )
        )
        == 0
    )

    document = json.loads(envelope_path.read_text(encoding="utf-8"))
    document["payload_kind"] = document["payload_kind"] + "-tampered"
    tampered = NodeMessageEnvelope.model_validate(document)

    with pytest.raises(SignatureVerificationError):
        validate_inbound_envelope(
            tampered,
            this_node_id=federation.recipient_node_id,
            trusted_sender_practice_id=federation.practice_id,
            ring=federation.ring(),
            already_processed=_never_processed,
            at=_NOW,
        )
