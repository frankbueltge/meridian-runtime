"""Unit tests for mrr.crypto.canonical (E1-T02)."""

from __future__ import annotations

import pytest
from mrr.crypto.canonical import canonicalize
from mrr.crypto.exceptions import CanonicalizationError


def test_canonicalize_returns_deterministic_bytes() -> None:
    payload = {"b": 1, "a": 2}
    assert canonicalize(payload) == b'{"a":2,"b":1}'


def test_canonicalize_is_insensitive_to_insertion_order() -> None:
    first = {"b": 1, "a": 2}
    second = {"a": 2, "b": 1}
    assert canonicalize(first) == canonicalize(second)


def test_canonicalize_wraps_non_finite_float_as_canonicalization_error() -> None:
    with pytest.raises(CanonicalizationError):
        canonicalize({"value": float("nan")})


def test_canonicalize_wraps_unsupported_type_as_canonicalization_error() -> None:
    with pytest.raises(CanonicalizationError):
        canonicalize({"value": object()})  # type: ignore[dict-item]
