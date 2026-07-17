"""Property tests for canonicalization and content hashing (E1-T02).

Covers the packet's named properties:

- "map-key insertion order never changes the canonical bytes or hash"
  (task-packets/E1-T02.yaml invariant "map key insertion order cannot
  change a content hash");
- "any semantic mutation (changed value, added key, removed key) changes
  the hash" (invariant "semantic mutation must change a content hash").
"""

from __future__ import annotations

from _json_strategies import json_objects, json_text, json_values
from hypothesis import given
from hypothesis import strategies as st
from mrr.crypto.canonical import JSONValue, canonicalize
from mrr.crypto.hashing import content_hash


@st.composite
def _object_with_shuffled_keys(draw: st.DrawFn) -> tuple[dict[str, JSONValue], list[str]]:
    obj = draw(json_objects(min_size=2))
    shuffled_keys = draw(st.permutations(list(obj.keys())))
    return obj, list(shuffled_keys)


@given(_object_with_shuffled_keys())
def test_insertion_order_does_not_change_canonical_bytes_or_hash(
    obj_and_order: tuple[dict[str, JSONValue], list[str]],
) -> None:
    obj, shuffled_keys = obj_and_order
    reordered = {key: obj[key] for key in shuffled_keys}

    assert canonicalize(obj) == canonicalize(reordered)
    assert content_hash(canonicalize(obj)) == content_hash(canonicalize(reordered))


@given(json_objects(min_size=1))
def test_removing_a_key_changes_the_hash(obj: dict[str, JSONValue]) -> None:
    removed_key = next(iter(obj))
    mutated = {key: value for key, value in obj.items() if key != removed_key}

    assert content_hash(canonicalize(obj)) != content_hash(canonicalize(mutated))


@st.composite
def _object_with_new_key(draw: st.DrawFn) -> tuple[dict[str, JSONValue], str, JSONValue]:
    obj = draw(json_objects())
    new_key = draw(json_text().filter(lambda key: key not in obj))
    new_value = draw(json_values())
    return obj, new_key, new_value


@given(_object_with_new_key())
def test_adding_a_key_changes_the_hash(
    obj_key_value: tuple[dict[str, JSONValue], str, JSONValue],
) -> None:
    obj, new_key, new_value = obj_key_value
    mutated = dict(obj)
    mutated[new_key] = new_value

    assert content_hash(canonicalize(obj)) != content_hash(canonicalize(mutated))


@st.composite
def _object_with_changed_value(draw: st.DrawFn) -> tuple[dict[str, JSONValue], str, JSONValue]:
    obj = draw(json_objects(min_size=1))
    key = draw(st.sampled_from(sorted(obj.keys())))
    original_value = obj[key]
    new_value = draw(json_values().filter(lambda value: value != original_value))
    return obj, key, new_value


@given(_object_with_changed_value())
def test_changing_a_value_changes_the_hash(
    obj_key_value: tuple[dict[str, JSONValue], str, JSONValue],
) -> None:
    obj, key, new_value = obj_key_value
    mutated = dict(obj)
    mutated[key] = new_value

    assert content_hash(canonicalize(obj)) != content_hash(canonicalize(mutated))
