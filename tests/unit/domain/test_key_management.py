"""Unit tests for mrr.domain.key_management (task-packets/E5-T01.yaml).

Covers the packet's named acceptance tests: KeyRing resolves a kid to its
descriptor; is_valid_at is true for an active in-window key and false for
every fail-closed case (revoked, rotated, before valid_from, at/after
valid_until, unknown kid); rotation supersedes a prior kid while keeping it
historically addressable; no private key material appears anywhere.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from mrr.crypto.keys import derive_key_id, encode_public_key, generate_ed25519_keypair
from mrr.domain.exceptions import UnknownKeyIdError
from mrr.domain.key_management import (
    KeyRing,
    PublicKeyDescriptor,
    new_descriptor,
    revoke,
    rotate,
)

_NOW = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)
_VALID_FROM = _NOW - timedelta(days=1)
_VALID_UNTIL = _NOW + timedelta(days=365)


def _active_descriptor() -> PublicKeyDescriptor:
    _, public_key = generate_ed25519_keypair()
    return new_descriptor(public_key, valid_from=_VALID_FROM, valid_until=_VALID_UNTIL)


# ---------------------------------------------------------------------------
# PublicKeyDescriptor: kid/key consistency and validity-window ordering are
# enforced at construction time, not trusted from the caller.
# ---------------------------------------------------------------------------


def test_new_descriptor_derives_a_matching_kid() -> None:
    _, public_key = generate_ed25519_keypair()

    descriptor = new_descriptor(public_key, valid_from=_VALID_FROM, valid_until=_VALID_UNTIL)

    assert descriptor.kid == derive_key_id(public_key)
    assert descriptor.state == "active"


def test_descriptor_rejects_a_kid_that_does_not_match_the_key() -> None:
    _, public_key = generate_ed25519_keypair()
    encoded = encode_public_key(public_key)

    with pytest.raises(ValueError, match="does not match the deterministic kid"):
        PublicKeyDescriptor(
            kid="kid:not-the-real-one",
            algorithm="Ed25519",
            encoded_public_key=encoded,
            valid_from=_VALID_FROM,
            valid_until=_VALID_UNTIL,
            state="active",
        )


def test_descriptor_rejects_an_unsupported_algorithm() -> None:
    _, public_key = generate_ed25519_keypair()
    encoded = encode_public_key(public_key)

    with pytest.raises(ValueError, match="unsupported key algorithm"):
        PublicKeyDescriptor(
            kid=derive_key_id(public_key),
            algorithm="RSA",
            encoded_public_key=encoded,
            valid_from=_VALID_FROM,
            valid_until=_VALID_UNTIL,
            state="active",
        )


def test_descriptor_rejects_malformed_encoded_public_key() -> None:
    with pytest.raises(ValueError, match="not a valid Ed25519 public key"):
        PublicKeyDescriptor(
            kid="kid:whatever",
            algorithm="Ed25519",
            encoded_public_key="not valid base64!!",
            valid_from=_VALID_FROM,
            valid_until=_VALID_UNTIL,
            state="active",
        )


def test_descriptor_rejects_valid_until_at_or_before_valid_from() -> None:
    _, public_key = generate_ed25519_keypair()
    encoded = encode_public_key(public_key)

    with pytest.raises(ValueError, match="strictly before"):
        PublicKeyDescriptor(
            kid=derive_key_id(public_key),
            algorithm="Ed25519",
            encoded_public_key=encoded,
            valid_from=_VALID_UNTIL,
            valid_until=_VALID_FROM,
            state="active",
        )


def test_descriptor_rejects_naive_datetimes() -> None:
    _, public_key = generate_ed25519_keypair()
    encoded = encode_public_key(public_key)

    with pytest.raises(ValueError, match="aware datetime"):
        PublicKeyDescriptor(
            kid=derive_key_id(public_key),
            algorithm="Ed25519",
            encoded_public_key=encoded,
            valid_from=_VALID_FROM.replace(tzinfo=None),
            valid_until=_VALID_UNTIL,
            state="active",
        )


# ---------------------------------------------------------------------------
# KeyRing.is_valid_at: fail-closed matrix (the packet's own named test).
# ---------------------------------------------------------------------------


def test_is_valid_at_true_for_an_active_in_window_key() -> None:
    descriptor = _active_descriptor()
    ring = KeyRing(descriptors={descriptor.kid: descriptor})

    assert ring.is_valid_at(_NOW, descriptor.kid) is True


def test_is_valid_at_false_for_revoked_key() -> None:
    descriptor = _active_descriptor()
    ring = KeyRing(descriptors={descriptor.kid: descriptor})

    revoked_ring = revoke(ring, kid=descriptor.kid)

    assert revoked_ring.is_valid_at(_NOW, descriptor.kid) is False
    # Historically addressable: still resolvable, just not valid.
    assert revoked_ring.get(descriptor.kid) is not None
    assert revoked_ring.get(descriptor.kid).state == "revoked"  # type: ignore[union-attr]


def test_is_valid_at_false_for_rotated_key() -> None:
    prior = _active_descriptor()
    ring = KeyRing(descriptors={prior.kid: prior})
    _, successor_public_key = generate_ed25519_keypair()

    rotated_ring = rotate(
        ring,
        prior_kid=prior.kid,
        successor_public_key=successor_public_key,
        valid_from=_VALID_FROM,
        valid_until=_VALID_UNTIL,
    )

    assert rotated_ring.is_valid_at(_NOW, prior.kid) is False


def test_is_valid_at_false_before_valid_from() -> None:
    descriptor = _active_descriptor()
    ring = KeyRing(descriptors={descriptor.kid: descriptor})

    before_window = descriptor.valid_from - timedelta(seconds=1)

    assert ring.is_valid_at(before_window, descriptor.kid) is False


def test_is_valid_at_false_at_valid_until_boundary_and_after() -> None:
    """The window is half-open: ``valid_from <= now < valid_until`` -- at or
    after ``valid_until`` is NOT valid.
    """
    descriptor = _active_descriptor()
    ring = KeyRing(descriptors={descriptor.kid: descriptor})

    assert ring.is_valid_at(descriptor.valid_until, descriptor.kid) is False
    assert ring.is_valid_at(descriptor.valid_until + timedelta(seconds=1), descriptor.kid) is False


def test_is_valid_at_true_exactly_at_valid_from_boundary() -> None:
    descriptor = _active_descriptor()
    ring = KeyRing(descriptors={descriptor.kid: descriptor})

    assert ring.is_valid_at(descriptor.valid_from, descriptor.kid) is True


def test_is_valid_at_false_for_unknown_kid() -> None:
    ring = KeyRing(descriptors={})

    assert ring.is_valid_at(_NOW, "kid:does-not-exist") is False


def test_get_resolves_a_known_kid_and_returns_none_for_unknown() -> None:
    descriptor = _active_descriptor()
    ring = KeyRing(descriptors={descriptor.kid: descriptor})

    assert ring.get(descriptor.kid) == descriptor
    assert ring.get("kid:unknown") is None


# ---------------------------------------------------------------------------
# rotation: supersedes a prior kid, keeps it historically addressable.
# ---------------------------------------------------------------------------


def test_rotate_makes_successor_active_and_prior_rotated_but_addressable() -> None:
    prior = _active_descriptor()
    ring = KeyRing(descriptors={prior.kid: prior})
    _, successor_public_key = generate_ed25519_keypair()

    new_ring = rotate(
        ring,
        prior_kid=prior.kid,
        successor_public_key=successor_public_key,
        valid_from=_VALID_FROM,
        valid_until=_VALID_UNTIL,
    )

    successor_kid = derive_key_id(successor_public_key)

    assert new_ring.is_valid_at(_NOW, successor_kid) is True
    assert new_ring.is_valid_at(_NOW, prior.kid) is False

    prior_after = new_ring.get(prior.kid)
    assert prior_after is not None
    assert prior_after.state == "rotated"
    assert prior_after.kid == prior.kid  # unchanged, still addressable


def test_rotate_does_not_mutate_the_original_ring() -> None:
    prior = _active_descriptor()
    ring = KeyRing(descriptors={prior.kid: prior})
    _, successor_public_key = generate_ed25519_keypair()

    rotate(
        ring,
        prior_kid=prior.kid,
        successor_public_key=successor_public_key,
        valid_from=_VALID_FROM,
        valid_until=_VALID_UNTIL,
    )

    # The original ring is untouched -- rotate returns a NEW ring.
    assert ring.get(prior.kid) is not None
    assert ring.get(prior.kid).state == "active"  # type: ignore[union-attr]
    assert len(ring.descriptors) == 1


def test_rotate_unknown_prior_kid_raises() -> None:
    ring = KeyRing(descriptors={})
    _, successor_public_key = generate_ed25519_keypair()

    with pytest.raises(UnknownKeyIdError):
        rotate(
            ring,
            prior_kid="kid:does-not-exist",
            successor_public_key=successor_public_key,
            valid_from=_VALID_FROM,
            valid_until=_VALID_UNTIL,
        )


def test_revoke_unknown_kid_raises() -> None:
    ring = KeyRing(descriptors={})

    with pytest.raises(UnknownKeyIdError):
        revoke(ring, kid="kid:does-not-exist")


def test_revoke_does_not_mutate_the_original_ring() -> None:
    descriptor = _active_descriptor()
    ring = KeyRing(descriptors={descriptor.kid: descriptor})

    revoke(ring, kid=descriptor.kid)

    assert ring.get(descriptor.kid).state == "active"  # type: ignore[union-attr]


def test_key_ring_rejects_a_mapping_where_key_does_not_equal_descriptor_kid() -> None:
    descriptor = _active_descriptor()

    with pytest.raises(ValueError, match="must equal descriptor.kid"):
        KeyRing(descriptors={"kid:wrong-key": descriptor})


def test_key_ring_descriptors_mapping_is_immutable() -> None:
    descriptor = _active_descriptor()
    ring = KeyRing(descriptors={descriptor.kid: descriptor})

    with pytest.raises(TypeError):
        ring.descriptors[descriptor.kid] = descriptor  # type: ignore[index]
