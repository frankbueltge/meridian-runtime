"""Property tests for mrr.adapters.object_store.local.LocalFilesystemArtifactStore
(task-packets/E1-T07.yaml): arbitrary byte strings round-trip through
put/get exactly, the derived key is stable and equals mrr.crypto.hashing,
and distinct contents never collide to the same key.

Each example gets its own temporary directory, created and torn down inside
the test body via ``tempfile.TemporaryDirectory``, rather than depending on
pytest's function-scoped ``tmp_path`` fixture — that fixture instance would
otherwise be resolved once and shared across every hypothesis example
generated within a single ``@given``-decorated test, letting each example's
files accumulate in the same directory as every prior example's.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st
from mrr.adapters.object_store.local import LocalFilesystemArtifactStore
from mrr.crypto.hashing import content_hash
from mrr.domain.artifacts import ArtifactDescriptor

_RUN_ID = "urn:mrr:run-manifest:01ARZ3NDEKTSV4RRFFQ69G5FAV"

#: Bounded so example generation, shrinking, and disk I/O all stay fast -
#: matching the size bounds tests/property/_json_strategies.py already uses
#: for this suite's other property tests.
_binary_payload = st.binary(max_size=4096)


def _put(store: LocalFilesystemArtifactStore, data: bytes) -> ArtifactDescriptor:
    return store.put(
        data,
        media_type="application/octet-stream",
        producer_run_id=_RUN_ID,
        classification="INTERNAL",
        created_at=datetime.now(UTC),
    )


@given(_binary_payload)
def test_arbitrary_bytes_round_trip_through_put_then_get(data: bytes) -> None:
    with tempfile.TemporaryDirectory() as root:
        store = LocalFilesystemArtifactStore(Path(root))

        descriptor = _put(store, data)

        assert store.get(descriptor.content_hash) == data


@given(_binary_payload)
def test_derived_hash_is_stable_and_equals_mrr_crypto_hashing(data: bytes) -> None:
    with tempfile.TemporaryDirectory() as root:
        store = LocalFilesystemArtifactStore(Path(root))

        descriptor = _put(store, data)

        assert descriptor.content_hash == content_hash(data)
        # Stable across a second, independent read of the same key.
        assert store.stat(descriptor.content_hash).content_hash == descriptor.content_hash


@given(_binary_payload, _binary_payload)
def test_distinct_contents_never_collide_to_the_same_key(data_a: bytes, data_b: bytes) -> None:
    if data_a == data_b:
        return

    with tempfile.TemporaryDirectory() as root:
        store = LocalFilesystemArtifactStore(Path(root))

        descriptor_a = _put(store, data_a)
        descriptor_b = _put(store, data_b)

        assert descriptor_a.content_hash != descriptor_b.content_hash
        # Each key still resolves to its own, independent bytes - no
        # cross-talk between the two stored blobs.
        assert store.get(descriptor_a.content_hash) == data_a
        assert store.get(descriptor_b.content_hash) == data_b
