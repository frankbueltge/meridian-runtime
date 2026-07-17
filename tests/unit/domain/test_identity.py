"""Unit tests for mrr.domain.identity (E1-T02)."""

from __future__ import annotations

import pytest
from mrr.domain.exceptions import InvalidEntityError, InvalidUrnError
from mrr.domain.identity import is_valid_urn, new_urn, parse_urn


def test_new_urn_has_expected_shape() -> None:
    value = new_urn("claim")

    assert value.startswith("urn:mrr:claim:")
    entity, ulid_part = parse_urn(value)
    assert entity == "claim"
    assert len(ulid_part) == 26


def test_new_urn_is_unique_per_call() -> None:
    assert new_urn("claim") != new_urn("claim")


@pytest.mark.parametrize("entity", ["claim", "evidence-crate", "node-manifest", "a", "a1-2"])
def test_new_urn_accepts_valid_entity_segments(entity: str) -> None:
    assert is_valid_urn(new_urn(entity))


@pytest.mark.parametrize(
    "entity",
    [
        "Claim",  # uppercase
        "claim_type",  # underscore
        "claim type",  # space
        "",  # empty
        "clàim",  # non-ascii
    ],
)
def test_new_urn_rejects_invalid_entity_segments(entity: str) -> None:
    with pytest.raises(InvalidEntityError):
        new_urn(entity)


@pytest.mark.parametrize(
    "value",
    [
        "urn:mrr:claim:01J00000000000000000000011",
        "urn:mrr:evidence-crate:01J00000000000000000000012",
    ],
)
def test_is_valid_urn_accepts_wellformed_urns(value: str) -> None:
    assert is_valid_urn(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "urn:mrr:claim:tooshort",  # ulid too short
        "urn:mrr:claim:01J000000000000000000000111",  # ulid too long
        "urn:mrr:claim:0000000000000000000000000i",  # 'i' not in reduced Crockford32 alphabet
        "urn:mrr:claim:0000000000000000000000000l",  # 'l' excluded (lowercase L)
        "urn:mrr:claim:01j00000000000000000000011",  # lowercase ulid
        "urn:mrr:Claim:01J00000000000000000000011",  # uppercase entity
        "urn:other:claim:01J00000000000000000000011",  # wrong namespace
        "not-a-urn-at-all",
        "",
    ],
)
def test_is_valid_urn_rejects_malformed_urns(value: str) -> None:
    assert is_valid_urn(value) is False


def test_parse_urn_rejects_malformed_urns_with_typed_error() -> None:
    with pytest.raises(InvalidUrnError):
        parse_urn("urn:mrr:claim:not-a-valid-ulid-suffix")


def test_parse_urn_returns_entity_and_ulid() -> None:
    entity, ulid_part = parse_urn("urn:mrr:claim:01J00000000000000000000011")
    assert entity == "claim"
    assert ulid_part == "01J00000000000000000000011"
