"""Unit tests for mrr.crypto.hashing (E1-T02).

Acceptance-test mapping: "content_hash format matches the schema regex"
(task-packets/E1-T02.yaml).
"""

from __future__ import annotations

import re

import pytest
from mrr.crypto.exceptions import ContentHashMismatchError
from mrr.crypto.hashing import SHA256_PATTERN, content_hash, verify_content_hash

#: The exact `$defs.sha256` pattern from schemas/common.schema.json, kept
#: independent of `mrr.crypto.hashing.SHA256_PATTERN` so this test does not
#: just compare a constant against itself.
_SCHEMA_SHA256_PATTERN = re.compile(r"^sha256:[a-f0-9]{64}$")


def test_content_hash_matches_schema_sha256_pattern() -> None:
    digest = content_hash(b"hello world")
    assert _SCHEMA_SHA256_PATTERN.match(digest)
    assert SHA256_PATTERN.match(digest)


def test_content_hash_is_deterministic() -> None:
    assert content_hash(b"same bytes") == content_hash(b"same bytes")


def test_content_hash_changes_with_input() -> None:
    assert content_hash(b"first") != content_hash(b"second")


def test_verify_content_hash_accepts_matching_hash() -> None:
    data = b"payload bytes"
    verify_content_hash(data, content_hash(data))  # must not raise


def test_verify_content_hash_rejects_mismatch() -> None:
    with pytest.raises(ContentHashMismatchError):
        verify_content_hash(b"payload bytes", content_hash(b"different bytes"))
