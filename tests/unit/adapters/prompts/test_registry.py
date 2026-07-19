"""Unit tests for mrr.adapters.prompts.registry.PromptRegistry (task-packets/
E4-T06.yaml), against fixture template files written under ``tmp_path`` --
no database, no network, no model, fully local, mirroring
tests/unit/adapters/object_store/test_local.py's own precedent for an
injectable-root file adapter.

Covers the packet's named acceptance tests: resolve returns content/kind/
variables/hash and is repeatable; two versions of one name resolve with
distinct hashes; an unknown name or version raises an explicit error; the
content hash matches an independently recomputed SHA-256 of the exact
committed bytes; render fills declared variables deterministically and
raises on a missing or an unknown extra variable; a system template and a
task template each resolve and expose their kind; and the real templates
seeded under the repository's own ``prompts/`` directory resolve and
render. The property test for deterministic rendering across arbitrary
variable maps lives in tests/property/test_prompt_registry_properties.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mrr.adapters.prompts.registry import (
    DEFAULT_PROMPTS_ROOT,
    MalformedPromptTemplateError,
    PromptNotFoundError,
    PromptRegistry,
    PromptVariableError,
    RegisteredPrompt,
)
from mrr.crypto.hashing import content_hash

# ---------------------------------------------------------------------------
# Fixture helpers.
# ---------------------------------------------------------------------------


def _write_template(root: Path, name: str, version: str, text: str) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{version}.md"
    path.write_text(text, encoding="utf-8")
    return path


_SYSTEM_TEMPLATE = '+++\nkind = "system"\nvariables = []\n+++\nYou are a helpful assistant.\n'

_TASK_TEMPLATE_V1 = (
    '+++\nkind = "task"\nvariables = ["goal", "context"]\n+++\n'
    "Goal: {{goal}}\nContext: {{context}}\n"
)

_TASK_TEMPLATE_V2 = (
    '+++\nkind = "task"\nvariables = ["goal", "context"]\n+++\n'
    "Objective: {{goal}}\nBackground: {{context}}\n"
)


def _registry(tmp_path: Path) -> PromptRegistry:
    return PromptRegistry(prompts_root=tmp_path / "prompts")


# ---------------------------------------------------------------------------
# resolve: content, hash, kind, variables; repeatability.
# ---------------------------------------------------------------------------


def test_resolve_returns_content_kind_variables_and_hash(tmp_path: Path) -> None:
    root = tmp_path / "prompts"
    _write_template(root, "greeter", "v1", _TASK_TEMPLATE_V1)
    registry = _registry(tmp_path)

    registered = registry.resolve("greeter", "v1")

    assert isinstance(registered, RegisteredPrompt)
    assert registered.name == "greeter"
    assert registered.version == "v1"
    assert registered.kind == "task"
    assert registered.variables == ("goal", "context")
    assert registered.content == _TASK_TEMPLATE_V1
    assert registered.content_hash == content_hash(_TASK_TEMPLATE_V1.encode("utf-8"))


def test_resolving_the_same_name_and_version_twice_yields_identical_content_and_hash(
    tmp_path: Path,
) -> None:
    root = tmp_path / "prompts"
    _write_template(root, "greeter", "v1", _TASK_TEMPLATE_V1)
    registry = _registry(tmp_path)

    first = registry.resolve("greeter", "v1")
    second = registry.resolve("greeter", "v1")

    assert first.content == second.content
    assert first.content_hash == second.content_hash
    assert first.variables == second.variables
    assert first.kind == second.kind


# ---------------------------------------------------------------------------
# addressing: distinct versions, distinct hashes; unknown name/version.
# ---------------------------------------------------------------------------


def test_two_versions_of_the_same_name_are_both_resolvable_with_distinct_hashes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "prompts"
    _write_template(root, "greeter", "v1", _TASK_TEMPLATE_V1)
    _write_template(root, "greeter", "v2", _TASK_TEMPLATE_V2)
    registry = _registry(tmp_path)

    v1 = registry.resolve("greeter", "v1")
    v2 = registry.resolve("greeter", "v2")

    assert v1.content_hash != v2.content_hash
    assert v1.content != v2.content
    assert v1.name == v2.name == "greeter"


def test_resolve_unknown_name_raises_explicit_error(tmp_path: Path) -> None:
    registry = _registry(tmp_path)

    with pytest.raises(PromptNotFoundError) as exc_info:
        registry.resolve("does-not-exist", "v1")

    assert exc_info.value.name == "does-not-exist"
    assert exc_info.value.version == "v1"


def test_resolve_unknown_version_raises_explicit_error(tmp_path: Path) -> None:
    root = tmp_path / "prompts"
    _write_template(root, "greeter", "v1", _TASK_TEMPLATE_V1)
    registry = _registry(tmp_path)

    with pytest.raises(PromptNotFoundError) as exc_info:
        registry.resolve("greeter", "v99")

    assert exc_info.value.name == "greeter"
    assert exc_info.value.version == "v99"


# ---------------------------------------------------------------------------
# content_hash equals an independently recomputed SHA-256 of committed bytes.
# ---------------------------------------------------------------------------


def test_content_hash_equals_independently_recomputed_sha256_of_committed_bytes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "prompts"
    path = _write_template(root, "greeter", "v1", _TASK_TEMPLATE_V1)
    registry = _registry(tmp_path)

    registered = registry.resolve("greeter", "v1")

    recomputed = content_hash(path.read_bytes())
    assert registered.content_hash == recomputed


# ---------------------------------------------------------------------------
# render: deterministic fill; missing/extra variable errors.
# ---------------------------------------------------------------------------


def test_render_fills_declared_variables_deterministically(tmp_path: Path) -> None:
    root = tmp_path / "prompts"
    _write_template(root, "greeter", "v1", _TASK_TEMPLATE_V1)
    registry = _registry(tmp_path)
    variables = {"goal": "win the game", "context": "final round"}

    first = registry.render("greeter", "v1", variables)
    second = registry.render("greeter", "v1", dict(variables))

    assert first.text == "Goal: win the game\nContext: final round\n"
    assert first.text == second.text
    assert first.rendered_hash == second.rendered_hash
    assert first.rendered_hash == content_hash(first.text.encode("utf-8"))
    assert first.kind == "task"


def test_render_missing_declared_variable_raises_explicit_error(tmp_path: Path) -> None:
    root = tmp_path / "prompts"
    _write_template(root, "greeter", "v1", _TASK_TEMPLATE_V1)
    registry = _registry(tmp_path)

    with pytest.raises(PromptVariableError) as exc_info:
        registry.render("greeter", "v1", {"goal": "win the game"})

    assert exc_info.value.missing == ("context",)
    assert exc_info.value.extra == ()


def test_render_unknown_extra_variable_raises_explicit_error(tmp_path: Path) -> None:
    root = tmp_path / "prompts"
    _write_template(root, "greeter", "v1", _TASK_TEMPLATE_V1)
    registry = _registry(tmp_path)

    with pytest.raises(PromptVariableError) as exc_info:
        registry.render(
            "greeter",
            "v1",
            {"goal": "win the game", "context": "final round", "unexpected": "value"},
        )

    assert exc_info.value.missing == ()
    assert exc_info.value.extra == ("unexpected",)


def test_render_missing_and_extra_variable_together_raises_one_error_naming_both(
    tmp_path: Path,
) -> None:
    root = tmp_path / "prompts"
    _write_template(root, "greeter", "v1", _TASK_TEMPLATE_V1)
    registry = _registry(tmp_path)

    with pytest.raises(PromptVariableError) as exc_info:
        registry.render("greeter", "v1", {"goal": "win the game", "unexpected": "value"})

    assert exc_info.value.missing == ("context",)
    assert exc_info.value.extra == ("unexpected",)


def test_render_unknown_name_or_version_raises_explicit_error(tmp_path: Path) -> None:
    registry = _registry(tmp_path)

    with pytest.raises(PromptNotFoundError):
        registry.render("does-not-exist", "v1", {})


# ---------------------------------------------------------------------------
# kind: system vs task, both resolvable and expose their kind.
# ---------------------------------------------------------------------------


def test_system_template_resolves_and_exposes_its_kind(tmp_path: Path) -> None:
    root = tmp_path / "prompts"
    _write_template(root, "system-brief", "v1", _SYSTEM_TEMPLATE)
    registry = _registry(tmp_path)

    registered = registry.resolve("system-brief", "v1")

    assert registered.kind == "system"
    assert registered.variables == ()


def test_task_template_resolves_and_exposes_its_kind(tmp_path: Path) -> None:
    root = tmp_path / "prompts"
    _write_template(root, "greeter", "v1", _TASK_TEMPLATE_V1)
    registry = _registry(tmp_path)

    registered = registry.resolve("greeter", "v1")

    assert registered.kind == "task"


def test_render_a_zero_variable_system_template(tmp_path: Path) -> None:
    root = tmp_path / "prompts"
    _write_template(root, "system-brief", "v1", _SYSTEM_TEMPLATE)
    registry = _registry(tmp_path)

    rendered = registry.render("system-brief", "v1", {})

    assert rendered.text == "You are a helpful assistant.\n"
    assert rendered.kind == "system"


# ---------------------------------------------------------------------------
# malformed templates.
# ---------------------------------------------------------------------------


def test_missing_front_matter_delimiter_raises_malformed_error(tmp_path: Path) -> None:
    root = tmp_path / "prompts"
    _write_template(root, "broken", "v1", "just a plain body with no front matter\n")
    registry = _registry(tmp_path)

    with pytest.raises(MalformedPromptTemplateError):
        registry.resolve("broken", "v1")


def test_invalid_kind_raises_malformed_error(tmp_path: Path) -> None:
    root = tmp_path / "prompts"
    _write_template(root, "broken", "v1", '+++\nkind = "not-a-kind"\nvariables = []\n+++\nBody\n')
    registry = _registry(tmp_path)

    with pytest.raises(MalformedPromptTemplateError):
        registry.resolve("broken", "v1")


def test_undeclared_body_placeholder_raises_malformed_error(tmp_path: Path) -> None:
    root = tmp_path / "prompts"
    _write_template(
        root, "broken", "v1", '+++\nkind = "task"\nvariables = ["goal"]\n+++\n{{goal}} {{typo}}\n'
    )
    registry = _registry(tmp_path)

    with pytest.raises(MalformedPromptTemplateError):
        registry.resolve("broken", "v1")


def test_declared_variable_unused_in_body_raises_malformed_error(tmp_path: Path) -> None:
    root = tmp_path / "prompts"
    _write_template(
        root, "broken", "v1", '+++\nkind = "task"\nvariables = ["goal", "unused"]\n+++\n{{goal}}\n'
    )
    registry = _registry(tmp_path)

    with pytest.raises(MalformedPromptTemplateError):
        registry.resolve("broken", "v1")


# ---------------------------------------------------------------------------
# list_names / list_versions.
# ---------------------------------------------------------------------------


def test_list_names_and_list_versions(tmp_path: Path) -> None:
    root = tmp_path / "prompts"
    _write_template(root, "greeter", "v1", _TASK_TEMPLATE_V1)
    _write_template(root, "greeter", "v2", _TASK_TEMPLATE_V2)
    _write_template(root, "system-brief", "v1", _SYSTEM_TEMPLATE)
    registry = _registry(tmp_path)

    assert registry.list_names() == ("greeter", "system-brief")
    assert registry.list_versions("greeter") == ("v1", "v2")
    assert registry.list_versions("system-brief") == ("v1",)


def test_list_versions_of_unknown_name_is_empty_not_an_error(tmp_path: Path) -> None:
    registry = _registry(tmp_path)

    assert registry.list_versions("does-not-exist") == ()


def test_list_names_on_a_missing_root_is_empty_not_an_error(tmp_path: Path) -> None:
    registry = PromptRegistry(prompts_root=tmp_path / "does-not-exist")

    assert registry.list_names() == ()


# ---------------------------------------------------------------------------
# the seeded real templates under the repository's own prompts/ directory.
# ---------------------------------------------------------------------------


def test_default_prompts_root_points_at_the_repository_prompts_directory() -> None:
    assert DEFAULT_PROMPTS_ROOT.name == "prompts"
    assert DEFAULT_PROMPTS_ROOT.is_dir()


def test_seeded_system_template_resolves_and_renders() -> None:
    registry = PromptRegistry()

    registered = registry.resolve("planner-system-brief", "v1")
    assert registered.kind == "system"

    rendered = registry.render(
        "planner-system-brief", "v1", dict.fromkeys(registered.variables, "")
    )
    assert rendered.text
    assert rendered.kind == "system"


def test_seeded_task_template_resolves_and_renders() -> None:
    registry = PromptRegistry()

    registered = registry.resolve("skeptic-challenge-task", "v1")
    assert registered.kind == "task"
    assert set(registered.variables) == {"target_claim_assertion", "target_claim_scope"}

    rendered = registry.render(
        "skeptic-challenge-task",
        "v1",
        {
            "target_claim_assertion": "the sample claim under review",
            "target_claim_scope": '{"practice_id": "urn:mrr:practice:example"}',
        },
    )
    assert "the sample claim under review" in rendered.text
    assert "{{target_claim_assertion}}" not in rendered.text
    assert "{{target_claim_scope}}" not in rendered.text
