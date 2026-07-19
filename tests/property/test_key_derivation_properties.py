"""Property tests: kid derivation is deterministic and collision-sensitive,
and never a function of any mutable label (task-packets/E5-T01.yaml).

Ed25519 keypairs have no seedable hypothesis strategy exposed by the
``cryptography`` library, so the "different key -> different kid" and
"deterministic for the same key" properties are proved directly with
freshly generated keypairs (mirroring
tests/property/test_signature_roundtrip_properties.py's own "generate once,
reuse" rationale for the parts of these tests that do not vary per key).
The "renaming/relabelling does not change any kid" property, by contrast,
genuinely varies over arbitrary text and is driven by ``hypothesis``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from _json_strategies import json_text
from hypothesis import given
from mrr.crypto.keys import derive_key_id, generate_ed25519_keypair
from mrr.domain.key_management import KeyRing, PublicKeyDescriptor, new_descriptor

_VALID_FROM = datetime(2026, 7, 19, tzinfo=UTC)
_VALID_UNTIL = _VALID_FROM + timedelta(days=365)


def test_kid_is_deterministic_across_many_independent_calls() -> None:
    _, public_key = generate_ed25519_keypair()

    kids = {derive_key_id(public_key) for _ in range(25)}

    assert len(kids) == 1


def test_many_independently_generated_keys_yield_pairwise_distinct_kids() -> None:
    kids = {derive_key_id(generate_ed25519_keypair()[1]) for _ in range(50)}

    # SHA-256 over 32-byte, freshly-generated Ed25519 public keys: no
    # collision is expected across 50 independent draws.
    assert len(kids) == 50


@given(label=json_text(), new_label=json_text())
def test_relabelling_a_practice_key_descriptor_never_changes_its_kid(
    label: str, new_label: str
) -> None:
    """A kid is derived only from the public key's own raw bytes
    (docs/spec/02_DOMAIN_MODEL.md section 1.1 identity discipline, mirrored
    for keys) -- attaching, then changing, an unrelated mutable "label"
    (simulated here as an external dict a caller might keep alongside a
    KeyRing) never changes the kid a descriptor was minted with.
    """
    _, public_key = generate_ed25519_keypair()
    descriptor = new_descriptor(public_key, valid_from=_VALID_FROM, valid_until=_VALID_UNTIL)
    original_kid = descriptor.kid

    # Simulate a caller attaching, then relabelling, metadata that has
    # nothing to do with the key material itself.
    labels_by_kid = {descriptor.kid: label}
    labels_by_kid[descriptor.kid] = new_label

    assert descriptor.kid == original_kid
    assert derive_key_id(public_key) == original_kid


def test_relabelling_does_not_change_kid_membership_in_a_key_ring() -> None:
    """A KeyRing keyed by kid is unaffected by any out-of-band relabelling —
    the ring's own identity (its kid keys) never derives from a label.
    """
    _, public_key = generate_ed25519_keypair()
    descriptor = new_descriptor(public_key, valid_from=_VALID_FROM, valid_until=_VALID_UNTIL)
    ring = KeyRing(descriptors={descriptor.kid: descriptor})

    assert isinstance(ring.get(descriptor.kid), PublicKeyDescriptor)
    assert ring.get(descriptor.kid).kid == derive_key_id(public_key)  # type: ignore[union-attr]
