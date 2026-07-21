"""Property test: for any sequence of ``create -> offer -> respond`` on a
``mrr.services.transfer.service.TransferService``, the stored content
record's ``transferred_objects`` content hashes are byte-identical to what
``create`` was called with — never rewritten (task-packets/E6-T01.yaml
acceptance test: "hash-pinning property test", and the packet's own
invariant: "the recipient cannot silently replace the source hash",
docs/spec/01_SYSTEM_SPEC.md section 4.9 acceptance criterion).

``offer``/``respond`` are ADR-0007 EVENT-ONLY transitions (see
``mrr.services.transfer.service``'s own module docstring) — they never touch
the content record at all, so this property is really "the content record
written once by ``create`` is never replaced," proved here for a
hypothesis-generated range of ``transferred_objects`` lists and decisions
rather than one hand-picked example.

Local, deliberate duplicate of
tests/unit/services/transfer/test_service.py's own fixtures and fakes —
this codebase's established convention for independent test tiers.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from hypothesis import given, settings
from hypothesis import strategies as st
from mrr.contracts import Practice, TransferContract
from mrr.crypto.keys import derive_key_id, encode_public_key, generate_ed25519_keypair
from mrr.domain.exceptions import ObjectNotFoundError, RevisionConflictError
from mrr.domain.hashing_policy import sign_object
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject, TypedEdge
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.transfer.service import TransferService

_NOW = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
_ACTOR = new_urn("agent-role")
_POLICY_VERSION = "policy-2026-07-01"

#: A valid `$defs.sha256` content hash, hypothesis-generated over its
#: 64-lowercase-hex-digit body only (the `sha256:` prefix is fixed).
_content_hash_strategy = st.text(alphabet="0123456789abcdef", min_size=64, max_size=64).map(
    lambda hex_digits: f"sha256:{hex_digits}"
)

_transferred_object_strategy = st.builds(
    lambda content_hash: {"id": new_urn("claim"), "content_hash": content_hash},
    content_hash=_content_hash_strategy,
)

_decision_strategy = st.sampled_from(["accepted", "rejected", "deferred", "unresolved"])


# ---------------------------------------------------------------------------
# In-memory fakes — identical shape to
# tests/unit/services/transfer/test_service.py's own (local duplicate).
# ---------------------------------------------------------------------------


class _FakeObjectRepository:
    def __init__(self) -> None:
        self._revisions: dict[str, list[StoredObject]] = {}

    def insert_revision(
        self, obj: StoredObject, expected_current_revision: int | None
    ) -> StoredObject:
        current = self._revisions.get(obj.id, [])
        current_max = current[-1].revision if current else None
        if current_max != expected_current_revision:
            raise RevisionConflictError(obj.id, expected_current_revision, current_max)
        self._revisions.setdefault(obj.id, []).append(obj)
        return obj

    def get_latest(self, id: str) -> StoredObject:
        revisions = self._revisions.get(id)
        if not revisions:
            raise ObjectNotFoundError(id)
        return revisions[-1]

    def get_revision(self, id: str, revision: int) -> StoredObject:
        for rev in self._revisions.get(id, []):
            if rev.revision == revision:
                return rev
        raise ObjectNotFoundError(id, revision)

    def list_revisions(self, id: str) -> list[StoredObject]:
        return list(self._revisions.get(id, []))


class _FakeEventLog:
    def __init__(self) -> None:
        self.appended: list[AppendedEvent] = []

    def append_for_test(self, event: DomainEvent) -> AppendedEvent:
        appended = AppendedEvent(
            event=event,
            sequence=len(self.appended) + 1,
            content_hash=f"sha256:{'b' * 64}",
            prev_hash=self.appended[-1].content_hash if self.appended else None,
        )
        self.appended.append(appended)
        return appended

    def read_all(self) -> list[AppendedEvent]:
        return list(self.appended)


def _service() -> tuple[TransferService, _FakeObjectRepository]:
    object_repository = _FakeObjectRepository()
    event_log = _FakeEventLog()
    edges: list[TypedEdge] = []

    def _record(
        obj: StoredObject, expected: int | None, event: DomainEvent
    ) -> tuple[StoredObject, AppendedEvent]:
        stored = object_repository.insert_revision(obj, expected)
        return stored, event_log.append_for_test(event)

    def _record_event(event: DomainEvent) -> AppendedEvent:
        return event_log.append_for_test(event)

    def _record_edges(
        new_edges: list[TypedEdge], event: DomainEvent
    ) -> tuple[list[TypedEdge], AppendedEvent]:
        edges.extend(new_edges)
        return new_edges, event_log.append_for_test(event)

    service = TransferService(object_repository, event_log, _record, _record_event, _record_edges)
    return service, object_repository


def _key_entry(public_key: Ed25519PublicKey) -> dict[str, Any]:
    return {
        "kid": derive_key_id(public_key),
        "algorithm": "Ed25519",
        "encoded_public_key": encode_public_key(public_key),
        "valid_from": _NOW - timedelta(days=1),
        "valid_until": _NOW + timedelta(days=365),
        "state": "active",
    }


def _practice(*, practice_id: str, keys: list[dict[str, Any]]) -> Practice:
    return Practice.model_validate(
        {
            "id": practice_id,
            "api_version": "mrr/v1alpha1",
            "kind": "Practice",
            "practice_id": practice_id,
            "revision": 1,
            "created_at": _NOW,
            "created_by": new_urn("agent-role"),
            "content_hash": "sha256:" + "a" * 64,
            "name": "Fixture Practice",
            "description": "Fixture practice for the hash-pinning property test.",
            "keys": keys,
            "governance_contacts": ["mailto:governance@fixture.invalid"],
            "supported_policy_versions": ["policy-2026-07-01"],
            "disclosure": {"max_disclosure": "PUBLIC", "trust_statement": "fixture"},
        }
    )


def _contract(
    *, sender_practice_id: str, key_id: str, transferred_objects: list[dict[str, str]]
) -> TransferContract:
    return TransferContract.model_validate(
        {
            "id": new_urn("transfer-contract"),
            "api_version": "mrr/v1alpha1",
            "kind": "TransferContract",
            "practice_id": sender_practice_id,
            "revision": 1,
            "created_at": _NOW,
            "created_by": new_urn("agent-role"),
            "content_hash": "sha256:" + "a" * 64,
            "sender_practice_id": sender_practice_id,
            "receiver_practice_id": new_urn("practice"),
            "transferred_objects": transferred_objects,
            "purpose": "Share for independent replication.",
            "permitted_uses": ["replication_analysis"],
            "disclosure_rules": {},
            "attribution_rules": {},
            "caveats": [],
            "correction_subscription": False,
            "obligations": [],
            "nonce": "n" * 16,
            "expires_at": _NOW + timedelta(days=7),
            "signature": {
                "signer_practice_id": sender_practice_id,
                "key_id": key_id,
                "algorithm": "Ed25519",
                "signed_at": _NOW,
                "value": "0" * 44,
            },
            "status": "created",
        }
    )


def _sign(contract: TransferContract, private_key: Ed25519PrivateKey) -> TransferContract:
    signature_value = sign_object(
        private_key, json.loads(contract.model_dump_json(exclude_none=True))
    )
    return contract.model_copy(
        update={"signature": contract.signature.model_copy(update={"value": signature_value})}
    )


@settings(max_examples=30)
@given(
    transferred_objects=st.lists(_transferred_object_strategy, min_size=1, max_size=5),
    decision=_decision_strategy,
)
def test_transferred_object_hashes_survive_offer_and_respond_unchanged(
    transferred_objects: list[dict[str, str]], decision: str
) -> None:
    service, object_repository = _service()
    private_key, public_key = generate_ed25519_keypair()
    practice_id = new_urn("practice")
    entry = _key_entry(public_key)
    practice = _practice(practice_id=practice_id, keys=[entry])
    contract = _sign(
        _contract(
            sender_practice_id=practice_id,
            key_id=entry["kid"],
            transferred_objects=transferred_objects,
        ),
        private_key,
    )
    original_hashes = [ref["content_hash"] for ref in transferred_objects]

    correlation_id = new_urn("research-run")
    service.create(
        contract, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    service.offer(
        contract.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    service.respond(
        contract.id,
        decision,  # type: ignore[arg-type]
        practice,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    stored = object_repository.get_latest(contract.id)
    assert stored.revision == 1
    stored_hashes = [ref["content_hash"] for ref in stored.body["transferred_objects"]]
    assert stored_hashes == original_hashes
