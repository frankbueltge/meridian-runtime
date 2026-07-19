"""Property tests for ``mrr.contracts.model_profile.compute_config_hash``
(task-packets/E4-T01.yaml): the ``ModelProfile.config_hash`` is invariant to
map-key insertion order and changes on any semantic byte change — the same
invariant family E1-T02 already established for canonical hashing generally
(tests/property/test_canonical_hash_properties.py), reused here rather than
re-implemented, per the packet's own "reusing the existing hashing policy"
instruction: ``compute_config_hash`` composes
``mrr.domain.hashing_policy.compute_content_hash``, which itself composes
``mrr.crypto.canonical.canonicalize`` (RFC 8785) and
``mrr.crypto.hashing.content_hash`` (SHA-256).
"""

from __future__ import annotations

from typing import Any

from _json_strategies import json_objects, json_text, json_values
from hypothesis import given
from hypothesis import strategies as st
from mrr.contracts.model_profile import compute_config_hash
from mrr.crypto.canonical import JSONValue

_BASE_KWARGS: dict[str, Any] = {
    "provider": "anthropic",
    "model_family": "claude-3",
    "model_identifier": "claude-3-5-sonnet-20241022",
    "decoding_parameters": {"temperature": 0.7, "top_p": 0.9},
    "determinism": "stochastic",
    "seed": 7,
    "prompt_family": "test-fixture-v1",
    "tool_permissions": ["web_search", "code_execution"],
}


def _hash_with(**overrides: Any) -> str:
    kwargs = dict(_BASE_KWARGS)
    kwargs.update(overrides)
    return compute_config_hash(**kwargs)


# ---------------------------------------------------------------------------
# Map-key insertion order never changes the hash — decoding_parameters is the
# one field on ModelProfile that is itself an open, string-keyed object.
# ---------------------------------------------------------------------------


@st.composite
def _decoding_parameters_with_shuffled_keys(
    draw: st.DrawFn,
) -> tuple[dict[str, JSONValue], list[str]]:
    obj = draw(json_objects(min_size=2, max_size=6))
    shuffled_keys = draw(st.permutations(list(obj.keys())))
    return obj, list(shuffled_keys)


@given(_decoding_parameters_with_shuffled_keys())
def test_config_hash_invariant_to_decoding_parameters_key_order(
    obj_and_order: tuple[dict[str, JSONValue], list[str]],
) -> None:
    obj, shuffled_keys = obj_and_order
    reordered = {key: obj[key] for key in shuffled_keys}

    assert _hash_with(decoding_parameters=obj) == _hash_with(decoding_parameters=reordered)


# ---------------------------------------------------------------------------
# Any semantic byte change to decoding_parameters changes the hash: adding a
# key, removing a key, or changing a value.
# ---------------------------------------------------------------------------


@given(json_objects(min_size=1, max_size=6))
def test_config_hash_changes_when_a_decoding_parameter_key_is_removed(
    decoding_parameters: dict[str, JSONValue],
) -> None:
    removed_key = next(iter(decoding_parameters))
    mutated = {k: v for k, v in decoding_parameters.items() if k != removed_key}

    assert _hash_with(decoding_parameters=decoding_parameters) != _hash_with(
        decoding_parameters=mutated
    )


@st.composite
def _decoding_parameters_with_new_key(
    draw: st.DrawFn,
) -> tuple[dict[str, JSONValue], str, JSONValue]:
    obj = draw(json_objects())
    new_key = draw(json_text().filter(lambda key: key not in obj))
    new_value = draw(json_values())
    return obj, new_key, new_value


@given(_decoding_parameters_with_new_key())
def test_config_hash_changes_when_a_decoding_parameter_key_is_added(
    obj_key_value: tuple[dict[str, JSONValue], str, JSONValue],
) -> None:
    obj, new_key, new_value = obj_key_value
    mutated = dict(obj)
    mutated[new_key] = new_value

    assert _hash_with(decoding_parameters=obj) != _hash_with(decoding_parameters=mutated)


@st.composite
def _decoding_parameters_with_changed_value(
    draw: st.DrawFn,
) -> tuple[dict[str, JSONValue], str, JSONValue]:
    obj = draw(json_objects(min_size=1))
    key = draw(st.sampled_from(sorted(obj.keys())))
    original_value = obj[key]
    new_value = draw(json_values().filter(lambda value: value != original_value))
    return obj, key, new_value


@given(_decoding_parameters_with_changed_value())
def test_config_hash_changes_when_a_decoding_parameter_value_changes(
    obj_key_value: tuple[dict[str, JSONValue], str, JSONValue],
) -> None:
    obj, key, new_value = obj_key_value
    mutated = dict(obj)
    mutated[key] = new_value

    assert _hash_with(decoding_parameters=obj) != _hash_with(decoding_parameters=mutated)


# ---------------------------------------------------------------------------
# Any semantic change to the OTHER seven config fields also changes the hash.
# ---------------------------------------------------------------------------


def test_config_hash_changes_when_provider_changes() -> None:
    assert _hash_with(provider="anthropic") != _hash_with(provider="openai")


def test_config_hash_changes_when_model_family_changes() -> None:
    assert _hash_with(model_family="claude-3") != _hash_with(model_family="claude-4")


def test_config_hash_changes_when_model_identifier_changes() -> None:
    assert _hash_with(model_identifier="a") != _hash_with(model_identifier="b")


def test_config_hash_changes_when_determinism_changes() -> None:
    assert _hash_with(determinism="deterministic") != _hash_with(determinism="stochastic")


def test_config_hash_changes_when_seed_changes() -> None:
    assert _hash_with(seed=1) != _hash_with(seed=2)


def test_config_hash_changes_when_seed_becomes_none() -> None:
    assert _hash_with(seed=1) != _hash_with(seed=None)


def test_config_hash_changes_when_prompt_family_changes() -> None:
    assert _hash_with(prompt_family="v1") != _hash_with(prompt_family="v2")


def test_config_hash_changes_when_tool_permissions_change() -> None:
    assert _hash_with(tool_permissions=["a"]) != _hash_with(tool_permissions=["a", "b"])


def test_config_hash_is_deterministic_for_identical_input() -> None:
    assert _hash_with() == _hash_with()
