"""Git-backed prompt/version registry (task-packets/E4-T06.yaml).

``PromptRegistry`` resolves a named, versioned prompt template committed as
a file under a ``prompts/`` directory to a ``RegisteredPrompt`` value object
(content, content hash, kind, declared variables), and renders a template
deterministically by filling exactly its declared variables. This satisfies
MRR-FR-045 ("record ... a prompt/configuration hash") and docs/spec/
04_SECURITY_AND_POLICY.md section 6.4 ("system and task prompt hashes";
"Sensitive prompt bodies MAY remain sealed at the local node while hashes
... travel").

--- On-disk layout ----------------------------------------------------------

Templates live under ``<prompts_root>/<name>/<version>.md`` — one committed
file per ``(name, version)``. ``<prompts_root>`` defaults to the top-level
``prompts/`` directory at the repository root (computed relative to this
module's own location, mirroring ``scripts/check_contracts.py``'s
``REPO_ROOT`` pattern), but is INJECTABLE via the constructor so tests can
point a ``PromptRegistry`` at an arbitrary fixture directory with no
packaging dependency.

Each template file is TOML front matter (delimited by ``+++`` lines, a
plain-text convention parseable with the standard library's ``tomllib`` —
no new dependency) followed by the prompt body:

    +++
    kind = "system"
    variables = []
    +++
    You are the Meridian planner. ...

The front matter declares exactly two keys: ``kind`` (``"system"`` or
``"task"``, section 6.4's "system and task prompt hashes") and ``variables``
(the list of placeholder names, e.g. ``["target_claim_assertion"]``, that
the body uses as ``{{target_claim_assertion}}``). A template whose body
placeholders do not exactly match its declared ``variables`` list is
rejected as malformed AT RESOLUTION TIME (see ``_load`` below) — this is
what makes the "no placeholder is left silently unfilled" invariant hold
structurally at render time too: ``render`` separately requires the
caller's supplied variables to exactly match the SAME declared list, so by
transitivity every placeholder the body actually contains always has a
supplied value at substitution time. No exception ever needs to be raised
mid-substitution for a missing key.

--- Hashing and immutability ------------------------------------------------

``content_hash`` is ``mrr.crypto.hashing.content_hash`` (SHA-256, the
existing hashing used everywhere else in this codebase — never a new hash)
computed over the EXACT committed bytes of the template file, front matter
included. This registry never writes or edits a template file: it only
reads. A change to a prompt's text is therefore always a NEW ``(name,
version)`` with new committed bytes and a different hash — the same
``(name, version)`` resolves to identical bytes and an identical hash every
time it is resolved, for as long as the committed file is unchanged.

``RegisteredPrompt`` carries ``content_hash`` as its own field, distinct
from ``content`` — a caller that wants to record or transmit only the hash
(section 6.4: "Sensitive prompt bodies MAY remain sealed at the local node
while hashes ... travel") reads ``.content_hash`` without needing to touch
or forward ``.content`` at all.

--- What this module deliberately does NOT do -------------------------------

It imports no provider SDK, no network client, and no model adapter (this
package does not call a model at all — see this package's own
``__init__.py`` docstring and tests/unit/architecture/
test_prompts_adapter_boundary.py). It does not rewire
``mrr.services.planner``, ``mrr.services.skeptic``, or
``mrr.adapters.llm.structured_generation`` onto this registry — that is a
later integration task (task-packets/E4-T06.yaml forbidden_changes). It
adds no new persisted entity or JSON Schema: ``RegisteredPrompt`` and
``RenderedPrompt`` are in-memory, frozen value objects, not
``mrr.contracts`` ``BaseObject`` instances.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from mrr.crypto.hashing import content_hash as _compute_content_hash

#: The kind vocabulary section 6.4 distinguishes ("system and task prompt
#: hashes"). Closed to exactly these two values.
PromptKind = Literal["system", "task"]

#: The two-file-per-version layout's fixed suffix (task-packets/E4-T06.yaml
#: derived_decisions: "prompts/<name>/<version>.<ext>").
_TEMPLATE_SUFFIX = ".md"

#: The literal front-matter delimiter line. A well-formed template's raw
#: text is exactly ``_FRONT_MATTER_DELIMITER + <toml> + _FRONT_MATTER_DELIMITER
#: + <body>`` (see ``_split_front_matter``).
_FRONT_MATTER_DELIMITER = "+++\n"

#: Matches exactly ``{{name}}`` (no internal whitespace tolerated — a
#: deterministic, single accepted spelling, not a general templating
#: language). Used both to find declared-variable placeholders in a body at
#: resolution time and to substitute them at render time, so the two checks
#: can never drift out of sync with each other.
_PLACEHOLDER_PATTERN = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")

#: This module's own file, three directories below the repository root
#: (adapters/prompts/mrr/adapters/prompts/registry.py) — mirrors
#: scripts/check_contracts.py's ``REPO_ROOT = Path(__file__).resolve()
#: .parent.parent`` pattern, generalized to this file's actual depth.
_REPO_ROOT = Path(__file__).resolve().parents[5]

#: The default prompts root: the top-level ``prompts/`` directory at the
#: repository root. Callers (in particular tests) may override this via
#: ``PromptRegistry(prompts_root=...)`` to point at an arbitrary fixture
#: directory with no packaging dependency.
DEFAULT_PROMPTS_ROOT = _REPO_ROOT / "prompts"


class PromptRegistryError(Exception):
    """Base class for every error this module raises."""


class PromptNotFoundError(PromptRegistryError):
    """Raised by ``resolve``/``render`` when ``(name, version)`` does not
    resolve to a committed template file — an unknown prompt name, or a
    known name at an unknown version. Never a silent ``None`` or empty
    value (task-packets/E4-T06.yaml invariant "addressing").
    """

    def __init__(self, name: str, version: str) -> None:
        self.name = name
        self.version = version
        super().__init__(f"no committed prompt template for name={name!r} version={version!r}")


class MalformedPromptTemplateError(PromptRegistryError):
    """Raised when a committed template file itself cannot be parsed as a
    valid prompt: missing/malformed front matter, a ``kind`` other than
    ``"system"``/``"task"``, a non-list or non-unique ``variables`` entry, or
    body placeholders that do not exactly match the declared ``variables``
    list. This is a template-authoring defect, never silently tolerated or
    partially applied — carries ``reason`` describing exactly what failed.
    """

    def __init__(self, name: str, version: str, *, reason: str) -> None:
        self.name = name
        self.version = version
        self.reason = reason
        super().__init__(f"malformed prompt template name={name!r} version={version!r}: {reason}")


class PromptVariableError(PromptRegistryError):
    """Raised by ``render`` when the caller-supplied ``variables`` mapping
    does not exactly match the template's declared variable set — a
    declared variable with no supplied value (``missing``), a supplied
    value for a name the template never declared (``extra``), or both.
    Never a silently unfilled placeholder and never a silently ignored
    extra input (task-packets/E4-T06.yaml invariant "deterministic
    rendering").
    """

    def __init__(
        self, name: str, version: str, *, missing: tuple[str, ...], extra: tuple[str, ...]
    ) -> None:
        self.name = name
        self.version = version
        self.missing = missing
        self.extra = extra
        super().__init__(
            f"variables for prompt name={name!r} version={version!r} do not match its "
            f"declared variables: missing={list(missing)!r}, extra={list(extra)!r}"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class RegisteredPrompt:
    """The result of ``PromptRegistry.resolve``: a committed template's
    exact content, its content hash, its kind, and its declared variables.

    ``content`` is the exact committed file text (front matter included) —
    the same bytes ``content_hash`` was computed from
    (``content_hash == mrr.crypto.hashing.content_hash(content.encode())``
    always holds; task-packets/E4-T06.yaml acceptance test "the content
    hash equals the SHA-256 of the exact committed template bytes,
    recomputed independently"). ``content_hash`` is exposed as its own
    field, independent of ``content`` — a caller that only needs the hash
    (section 6.4: "hashes ... travel") reads it without needing to forward
    or even inspect the body.

    ``variables`` is the declared placeholder-name tuple in the order the
    template's front matter lists them (deterministic: fixed by the
    committed file's own content, not derived at runtime).
    """

    name: str
    version: str
    kind: PromptKind
    content: str
    content_hash: str
    variables: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class RenderedPrompt:
    """The result of ``PromptRegistry.render``: the rendered text (the
    template body with every declared variable substituted) and its
    content hash. Identical ``(name, version, variables)`` always yields an
    identical ``text`` and an identical ``rendered_hash`` — rendering has no
    hidden state and no non-determinism (task-packets/E4-T06.yaml invariant
    "deterministic rendering").
    """

    name: str
    version: str
    kind: PromptKind
    text: str
    rendered_hash: str


@dataclass(frozen=True, slots=True)
class _LoadedTemplate:
    """Internal: the fully parsed, validated form of one committed template
    file. Never returned to a caller directly — ``resolve`` and ``render``
    each project the fields they need onto their own public value object.
    """

    kind: PromptKind
    variables: tuple[str, ...]
    raw_text: str
    content_hash: str
    body: str


def _require_valid_path_segment(value: str, *, what: str) -> None:
    """Reject a ``name``/``version`` that could not possibly be a single
    path segment under ``prompts_root`` — empty, containing a path
    separator, or a ``.``/``..`` traversal segment. A defensive, local check
    (this registry never receives untrusted network input in this task's
    scope, but a malformed identifier should fail closed rather than be
    silently joined into a filesystem path).
    """
    if not value or "/" in value or "\\" in value or value in (".", ".."):
        raise ValueError(f"{what} must be a non-empty, single path-segment string, got {value!r}")


def _split_front_matter(raw_text: str, *, name: str, version: str) -> tuple[str, str]:
    """Split a template file's raw text into its TOML front matter source
    and its body, on the literal ``+++`` delimiter lines.
    """
    parts = raw_text.split(_FRONT_MATTER_DELIMITER, 2)
    if len(parts) != 3 or parts[0] != "":
        raise MalformedPromptTemplateError(
            name,
            version,
            reason=(
                "template must start with a '+++' line, contain TOML front matter, "
                "and a second '+++' line before the body"
            ),
        )
    return parts[1], parts[2]


def _placeholder_names(body: str) -> tuple[str, ...]:
    """Return every distinct ``{{name}}`` placeholder found in ``body``, in
    first-occurrence order. Uses the SAME pattern ``render`` substitutes
    with, so "what resolution validates" and "what render fills" can never
    drift apart.
    """
    seen: dict[str, None] = {}
    for match in _PLACEHOLDER_PATTERN.finditer(body):
        seen.setdefault(match.group(1), None)
    return tuple(seen)


def _parse_front_matter(
    front_matter_text: str, *, name: str, version: str
) -> tuple[PromptKind, tuple[str, ...]]:
    """Parse and validate the TOML front matter: exactly ``kind`` (one of
    ``"system"``/``"task"``) and ``variables`` (a list of unique strings).
    """
    try:
        front_matter = tomllib.loads(front_matter_text)
    except tomllib.TOMLDecodeError as exc:
        raise MalformedPromptTemplateError(
            name, version, reason=f"front matter is not valid TOML: {exc}"
        ) from exc

    kind_raw = front_matter.get("kind")
    if kind_raw not in ("system", "task"):
        raise MalformedPromptTemplateError(
            name,
            version,
            reason=f"front matter 'kind' must be 'system' or 'task', got {kind_raw!r}",
        )
    kind = cast(PromptKind, kind_raw)

    variables_raw = front_matter.get("variables")
    if not isinstance(variables_raw, list) or not all(isinstance(v, str) for v in variables_raw):
        raise MalformedPromptTemplateError(
            name,
            version,
            reason=f"front matter 'variables' must be a list of strings, got {variables_raw!r}",
        )
    if len(set(variables_raw)) != len(variables_raw):
        raise MalformedPromptTemplateError(
            name,
            version,
            reason=f"front matter 'variables' must not contain duplicates: {variables_raw!r}",
        )
    return kind, tuple(variables_raw)


class PromptRegistry:
    """Resolves and renders named, versioned prompt templates committed
    under a ``prompts_root`` directory (default: the repository's top-level
    ``prompts/``). Read-only: never writes, edits, or deletes a template.
    """

    def __init__(self, prompts_root: Path | None = None) -> None:
        self._root = Path(prompts_root) if prompts_root is not None else DEFAULT_PROMPTS_ROOT

    def resolve(self, name: str, version: str) -> RegisteredPrompt:
        """Resolve ``(name, version)`` to its committed content, content
        hash, kind, and declared variables.

        Raises:
            PromptNotFoundError: if no committed template file exists for
                ``(name, version)`` — never a silent ``None``.
            MalformedPromptTemplateError: if the committed file exists but
                cannot be parsed as a valid template (see this module's
                docstring).
        """
        loaded = self._load(name, version)
        return RegisteredPrompt(
            name=name,
            version=version,
            kind=loaded.kind,
            content=loaded.raw_text,
            content_hash=loaded.content_hash,
            variables=loaded.variables,
        )

    def render(self, name: str, version: str, variables: Mapping[str, str]) -> RenderedPrompt:
        """Render ``(name, version)`` by filling exactly its declared
        variables with ``variables``.

        Raises:
            PromptNotFoundError: if ``(name, version)`` is unknown.
            MalformedPromptTemplateError: if the committed file cannot be
                parsed as a valid template.
            PromptVariableError: if ``variables`` omits a declared variable,
                supplies a name the template never declared, or both — never
                a silently unfilled placeholder and never a silently
                ignored extra input.
        """
        loaded = self._load(name, version)
        declared = set(loaded.variables)
        provided = set(variables.keys())
        missing = tuple(sorted(declared - provided))
        extra = tuple(sorted(provided - declared))
        if missing or extra:
            raise PromptVariableError(name, version, missing=missing, extra=extra)

        # Every placeholder in `loaded.body` is, by construction, a member
        # of `loaded.variables` (validated in `_load` at resolution time),
        # and `provided == declared` was just confirmed above, so every
        # placeholder substitution below is guaranteed to find a value —
        # no placeholder is ever left silently unfilled, and this holds
        # structurally rather than by a runtime post-check.
        rendered_text = _PLACEHOLDER_PATTERN.sub(
            lambda match: variables[match.group(1)], loaded.body
        )
        rendered_hash = _compute_content_hash(rendered_text.encode("utf-8"))
        return RenderedPrompt(
            name=name,
            version=version,
            kind=loaded.kind,
            text=rendered_text,
            rendered_hash=rendered_hash,
        )

    def list_names(self) -> tuple[str, ...]:
        """Return every prompt name with at least one committed version
        under ``prompts_root``, sorted. An empty or absent ``prompts_root``
        yields an empty tuple — listing is a query, not an addressed
        lookup, so it is not subject to ``resolve``'s "unknown is an
        explicit error" rule.
        """
        if not self._root.is_dir():
            return ()
        return tuple(sorted(p.name for p in self._root.iterdir() if p.is_dir()))

    def list_versions(self, name: str) -> tuple[str, ...]:
        """Return every version committed for ``name``, sorted. An unknown
        ``name`` yields an empty tuple (see ``list_names``'s docstring for
        why listing does not raise).
        """
        _require_valid_path_segment(name, what="name")
        name_dir = self._root / name
        if not name_dir.is_dir():
            return ()
        return tuple(
            sorted(
                p.stem for p in name_dir.iterdir() if p.is_file() and p.suffix == _TEMPLATE_SUFFIX
            )
        )

    def _path_for(self, name: str, version: str) -> Path:
        _require_valid_path_segment(name, what="name")
        _require_valid_path_segment(version, what="version")
        return self._root / name / f"{version}{_TEMPLATE_SUFFIX}"

    def _load(self, name: str, version: str) -> _LoadedTemplate:
        path = self._path_for(name, version)
        if not path.is_file():
            raise PromptNotFoundError(name, version)

        raw_bytes = path.read_bytes()
        raw_text = raw_bytes.decode("utf-8")
        hash_ = _compute_content_hash(raw_bytes)

        front_matter_text, body = _split_front_matter(raw_text, name=name, version=version)
        kind, declared_variables = _parse_front_matter(
            front_matter_text, name=name, version=version
        )

        body_placeholders = set(_placeholder_names(body))
        declared_set = set(declared_variables)
        if body_placeholders != declared_set:
            undeclared = sorted(body_placeholders - declared_set)
            unused = sorted(declared_set - body_placeholders)
            raise MalformedPromptTemplateError(
                name,
                version,
                reason=(
                    "body placeholders do not exactly match declared 'variables': "
                    f"placeholders with no declaration={undeclared!r}, "
                    f"declared but unused in body={unused!r}"
                ),
            )

        return _LoadedTemplate(
            kind=kind,
            variables=declared_variables,
            raw_text=raw_text,
            content_hash=hash_,
            body=body,
        )


__all__ = [
    "DEFAULT_PROMPTS_ROOT",
    "MalformedPromptTemplateError",
    "PromptKind",
    "PromptNotFoundError",
    "PromptRegistry",
    "PromptRegistryError",
    "PromptVariableError",
    "RegisteredPrompt",
    "RenderedPrompt",
]
