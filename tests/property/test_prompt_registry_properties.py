"""Property tests for mrr.adapters.prompts.registry.PromptRegistry
(task-packets/E4-T06.yaml invariant "deterministic rendering": "render fills
EXACTLY the template's declared variables; a missing declared variable and
an unknown extra variable are each an explicit error; ... identical
(template, variables) yields identical rendered text and hash").

A single fixed template (kind=task, declared variables ``alpha``/``beta``)
is written once, into a module-scoped fixture directory, and reused across
every generated example -- read-only after creation, so reusing it across
hypothesis examples is safe (unlike a function-scoped fixture a test would
mutate per example).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from mrr.adapters.prompts.registry import PromptRegistry, PromptVariableError
from mrr.crypto.hashing import content_hash

_TEMPLATE = (
    '+++\nkind = "task"\nvariables = ["alpha", "beta"]\n+++\nAlpha: {{alpha}}\nBeta: {{beta}}\n'
)

_DECLARED = frozenset({"alpha", "beta"})

#: Plain text values with no control characters and no literal braces, so
#: the substituted text never accidentally forms a new ``{{...}}`` token.
_VARIABLE_VALUE = st.text(
    alphabet=st.characters(blacklist_categories=("Cs", "Cc"), blacklist_characters="{}"),
    max_size=40,
)

#: A small closed vocabulary -- two names the template declares (alpha,
#: beta) and two it does not (gamma, delta) -- so generated subsets exercise
#: every combination of missing/extra/exact-match against a fixed template.
_CANDIDATE_NAMES = ("alpha", "beta", "gamma", "delta")


@pytest.fixture(scope="module")
def registry(tmp_path_factory: pytest.TempPathFactory) -> PromptRegistry:
    root = tmp_path_factory.mktemp("prompt_registry_properties")
    directory = root / "greeter"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "v1.md").write_text(_TEMPLATE, encoding="utf-8")
    return PromptRegistry(prompts_root=root)


@given(alpha=_VARIABLE_VALUE, beta=_VARIABLE_VALUE)
def test_render_is_deterministic_for_arbitrary_variable_values(
    registry: PromptRegistry, alpha: str, beta: str
) -> None:
    variables = {"alpha": alpha, "beta": beta}

    first = registry.render("greeter", "v1", variables)
    second = registry.render("greeter", "v1", dict(variables))

    assert first.text == second.text
    assert first.rendered_hash == second.rendered_hash
    assert first.text == f"Alpha: {alpha}\nBeta: {beta}\n"
    assert first.rendered_hash == content_hash(first.text.encode("utf-8"))


@given(provided_names=st.sets(st.sampled_from(_CANDIDATE_NAMES), max_size=len(_CANDIDATE_NAMES)))
def test_render_raises_iff_provided_names_do_not_exactly_match_declared(
    registry: PromptRegistry, provided_names: set[str]
) -> None:
    variables = dict.fromkeys(provided_names, "value")

    if provided_names == _DECLARED:
        rendered = registry.render("greeter", "v1", variables)
        assert "{{alpha}}" not in rendered.text
        assert "{{beta}}" not in rendered.text
    else:
        with pytest.raises(PromptVariableError) as exc_info:
            registry.render("greeter", "v1", variables)
        assert set(exc_info.value.missing) == _DECLARED - provided_names
        assert set(exc_info.value.extra) == provided_names - _DECLARED
