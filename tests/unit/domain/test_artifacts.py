"""Unit tests for mrr.domain.artifacts (E1-T07): the ArtifactDescriptor
dataclass surface, the ArtifactStore Protocol surface (including its
deliberate absence of any mutation method), the Classification literal, and
the typed error surface in mrr.domain.exceptions this module and its
adapters raise.

Adapter behavior (actual byte storage, integrity verification against real
files, atomic writes) is exercised by tests/unit/adapters/object_store/ and
tests/property/test_artifact_store_properties.py; this module never imports
a filesystem or storage SDK.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, get_args

import pytest
from mrr.domain.artifacts import (
    ArtifactDescriptor,
    ArtifactStore,
    Classification,
    require_valid_content_hash,
)
from mrr.domain.exceptions import (
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    DomainError,
    InvalidContentHashError,
)

_VALID_HASH = "sha256:" + "a" * 64
_VALID_RUN_ID = "urn:mrr:run-manifest:01ARZ3NDEKTSV4RRFFQ69G5FAV"


def _sample_descriptor(**overrides: Any) -> ArtifactDescriptor:
    defaults: dict[str, Any] = {
        "content_hash": _VALID_HASH,
        "media_type": "application/json",
        "size_bytes": 42,
        "producer_run_id": _VALID_RUN_ID,
        "created_at": datetime.now(UTC),
        "classification": "INTERNAL",
    }
    defaults.update(overrides)
    return ArtifactDescriptor(**defaults)


# ---------------------------------------------------------------------------
# Classification literal.
# ---------------------------------------------------------------------------


def test_classification_has_exactly_the_five_schema_values() -> None:
    assert set(get_args(Classification)) == {
        "PUBLIC",
        "INTERNAL",
        "RESTRICTED",
        "SENSITIVE",
        "PARTICIPANT_IDENTIFIABLE",
    }


# ---------------------------------------------------------------------------
# ArtifactDescriptor — carries every MRR-FR-051 field.
# ---------------------------------------------------------------------------


def test_descriptor_carries_every_mrr_fr_051_field() -> None:
    created_at = datetime.now(UTC)
    descriptor = _sample_descriptor(
        content_hash=_VALID_HASH,
        media_type="text/plain",
        size_bytes=7,
        producer_run_id=_VALID_RUN_ID,
        created_at=created_at,
        classification="RESTRICTED",
    )

    assert descriptor.content_hash == _VALID_HASH
    assert descriptor.media_type == "text/plain"
    assert descriptor.size_bytes == 7
    assert descriptor.producer_run_id == _VALID_RUN_ID
    assert descriptor.created_at == created_at
    assert descriptor.classification == "RESTRICTED"


def test_descriptor_is_frozen() -> None:
    descriptor = _sample_descriptor()
    with pytest.raises(AttributeError):
        descriptor.size_bytes = 100  # type: ignore[misc]


def test_descriptor_rejects_malformed_content_hash() -> None:
    with pytest.raises(InvalidContentHashError):
        _sample_descriptor(content_hash="not-a-hash")


def test_descriptor_rejects_negative_size() -> None:
    with pytest.raises(ValueError, match="size_bytes"):
        _sample_descriptor(size_bytes=-1)


def test_descriptor_accepts_zero_size() -> None:
    assert _sample_descriptor(size_bytes=0).size_bytes == 0


def test_descriptor_rejects_empty_media_type() -> None:
    with pytest.raises(ValueError, match="media_type"):
        _sample_descriptor(media_type="")


def test_descriptor_rejects_invalid_producer_run_id() -> None:
    with pytest.raises(ValueError, match="producer_run_id"):
        _sample_descriptor(producer_run_id="not-a-urn")


def test_descriptor_rejects_naive_created_at() -> None:
    with pytest.raises(ValueError, match="aware"):
        _sample_descriptor(created_at=datetime(2026, 1, 1))  # noqa: DTZ001


# ---------------------------------------------------------------------------
# require_valid_content_hash.
# ---------------------------------------------------------------------------


def test_require_valid_content_hash_accepts_wellformed_hash() -> None:
    require_valid_content_hash(_VALID_HASH)  # must not raise


@pytest.mark.parametrize(
    "value",
    [
        "",
        "sha256:short",
        "md5:" + "a" * 32,
        "sha256:" + "A" * 64,  # uppercase hex not accepted
        "sha256:" + "g" * 64,  # 'g' not hex
        _VALID_HASH + "x",
    ],
)
def test_require_valid_content_hash_rejects_malformed_values(value: str) -> None:
    with pytest.raises(InvalidContentHashError):
        require_valid_content_hash(value)


# ---------------------------------------------------------------------------
# ArtifactStore Protocol — structural conformance and the no-mutation shape.
# ---------------------------------------------------------------------------


class _FakeArtifactStore:
    def put(
        self,
        data: bytes,
        *,
        media_type: str,
        producer_run_id: str,
        classification: Classification,
        created_at: datetime,
    ) -> ArtifactDescriptor:  # pragma: no cover - structural test double
        raise NotImplementedError

    def get(self, content_hash: str) -> bytes:  # pragma: no cover
        raise NotImplementedError

    def stat(self, content_hash: str) -> ArtifactDescriptor:  # pragma: no cover
        raise NotImplementedError

    def exists(self, content_hash: str) -> bool:  # pragma: no cover
        raise NotImplementedError


class _IncompleteArtifactStore:
    """Missing stat/exists — must not satisfy the protocol."""

    def put(
        self,
        data: bytes,
        *,
        media_type: str,
        producer_run_id: str,
        classification: Classification,
        created_at: datetime,
    ) -> ArtifactDescriptor:  # pragma: no cover
        raise NotImplementedError

    def get(self, content_hash: str) -> bytes:  # pragma: no cover
        raise NotImplementedError


def test_artifact_store_protocol_accepts_a_conforming_implementation() -> None:
    assert isinstance(_FakeArtifactStore(), ArtifactStore)


def test_artifact_store_protocol_rejects_an_incomplete_implementation() -> None:
    assert not isinstance(_IncompleteArtifactStore(), ArtifactStore)


def test_artifact_store_protocol_exposes_no_delete_update_or_overwrite() -> None:
    """The interface's only write operation is ``put``; there is no method
    named (or resembling) delete/remove/update/overwrite/replace anywhere on
    it — immutability is enforced by the shape of the Protocol itself.
    """
    public_members = {name for name in dir(ArtifactStore) if not name.startswith("_")}
    assert public_members == {"put", "get", "stat", "exists"}

    forbidden_substrings = ("delete", "remove", "update", "overwrite", "replace", "mutate")
    for member in public_members:
        for forbidden in forbidden_substrings:
            assert forbidden not in member.lower()


# ---------------------------------------------------------------------------
# Typed error surface.
# ---------------------------------------------------------------------------


def test_invalid_content_hash_error_carries_the_offending_value() -> None:
    err = InvalidContentHashError("not-a-hash")
    assert isinstance(err, DomainError)
    assert err.content_hash == "not-a-hash"


def test_artifact_not_found_error_carries_content_hash() -> None:
    err = ArtifactNotFoundError(_VALID_HASH)
    assert isinstance(err, DomainError)
    assert err.content_hash == _VALID_HASH


def test_artifact_integrity_error_carries_expected_and_actual() -> None:
    other_hash = "sha256:" + "b" * 64
    err = ArtifactIntegrityError(expected=_VALID_HASH, actual=other_hash)
    assert isinstance(err, DomainError)
    assert err.expected == _VALID_HASH
    assert err.actual == other_hash
