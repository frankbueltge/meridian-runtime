"""Mirrors schemas/run-manifest.schema.json (docs/spec/02_DOMAIN_MODEL.md
section 2.6, "RunManifest": records an execution attempt).

--- Identity: RunManifest.id names "the run" (task-packets/E2-T05.yaml) ------

``RunManifest.id`` is minted with the URN entity segment ``run`` (i.e.
``mrr.domain.identity.new_urn("run")``), not ``run-manifest``. This mirrors
``TaskBundle.id`` using entity ``task`` (not ``task-bundle``) and
``NodeManifest.node_id`` referencing entity ``node`` (not the manifest's own
``node-manifest``): the URN entity segment names the CONCEPTUAL thing being
identified (a run, a task, a node), while ``kind`` names the immutable
object recording it. This is not a new convention invented for this task —
it is required for faithfulness to the schemas already shipped:
``schemas/evidence-crate.schema.json``'s ``run_id`` field (and
``examples/evidence-crate.example.json``'s ``"run_id":
"urn:mrr:run:01J00000000000000000000007"``) already reference an entity
called ``run`` — a RunManifest's own identity is exactly what that field
points at. ``examples/run-manifest.example.json`` reuses that exact
placeholder id for this reason.

--- Required-but-nullable vs. optional-and-nullable (round-trip note) -------

Five properties are explicitly nullable in the schema via ``anyOf [<type>,
{"type": "null"}]`` — ``ended_at``, ``run_state``, ``code_commit``, ``cost``,
``logs_ref`` — mirroring ``common.schema.json``'s own ``supersedes`` pattern.
None of the five are in the schema's ``required`` list, deliberately: every
``mrr.contracts`` model in this repository dumps with
``model_dump_json(exclude_none=True)`` (``scripts/check_contracts.py``,
every service's ``_*_to_stored_object`` helper), which drops any field whose
Python value is ``None`` from the serialized JSON entirely. A field that
were BOTH ``required`` and legitimately ``None``-valued (e.g. an active,
not-yet-sealed run's ``ended_at``) would then round-trip to JSON missing
that required key and fail re-validation — exactly the same reasoning
``mrr.contracts.common.Budget``'s docstring documents for its own five
optional fields. These five are the fields the domain model implies are
absent for an active (unsealed) run and present once terminal/sealed; the
reference recording service in ``mrr.services.node_runtime.run_manifest``
only ever builds already-sealed manifests, so it always supplies all five.
"""

from __future__ import annotations

from typing import Any, Literal

from mrr.contracts.common import BaseObject, MRRModel, Sha256, Urn
from mrr.contracts.evidence_crate import RunState
from pydantic import AwareDatetime, Field


class RunResourceUsage(MRRModel):
    """Mirrors the `resource_usage` object; only `wall_seconds` is required,
    matching `mrr.services.node_runtime.executor.ResourceUsage`'s own single
    `wall_time_seconds` field (richer CPU/memory accounting is this entity's
    own addition, per task-packets/E2-T05.yaml, not the executor's).
    """

    wall_seconds: float = Field(ge=0)
    cpu_seconds: float | None = Field(default=None, ge=0)
    memory_mb: float | None = Field(default=None, ge=0)


class RunCost(MRRModel):
    """Mirrors the `cost` object (present only when cost accounting is
    available; the top-level `cost` field itself is `RunCost | None`).
    """

    amount: float = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)


class RunManifest(BaseObject):
    """Mirrors schemas/run-manifest.schema.json.

    Every property is in the schema's top-level `required` list except the
    five explicitly-nullable fields documented in this module's docstring
    (`ended_at`, `run_state`, `code_commit`, `cost`, `logs_ref`), which
    default to `None` here exactly like `mrr.contracts.task_bundle.
    ExecutionSpec.code_revision` or `mrr.contracts.common.Budget`'s fields.

    `environment` mirrors `{"type": "object", "additionalProperties":
    {"type": "string"}}` — a flat string-to-string map (interpreter/OS/
    dependency versions, ...), deliberately distinct from
    `mrr.contracts.evidence_crate.EnvironmentInfo` (which has its own
    `image_digest`/`code_revision`/`input_hashes` fields — this entity
    carries those as its OWN top-level `image_digest`/`code_commit`/
    `input_hashes` instead, per task-packets/E2-T05.yaml's explicit field
    list). `parameters` mirrors `{"type": "object"}` with no further
    restriction (open-ended), matching `mrr.contracts.task_bundle.
    TaskBundle.instructions`, hence `dict[str, Any]`. `tool_invocations`/
    `model_invocations` mirror `{"type": "array", "items": {"type":
    "object"}}` — unconstrained objects, since MRR-FR-045's real invocation
    record shape is E4's responsibility (task-packets/E2-T05.yaml forbids
    inventing it here); both arrays are always empty for the deterministic
    reference run this task's own recording service builds.
    """

    kind: Literal["RunManifest"]
    task_id: Urn
    task_revision: int = Field(ge=1)
    research_score_id: Urn
    research_score_revision: int = Field(ge=1)
    executor_id: Urn
    executor_role: str = Field(min_length=1)
    started_at: AwareDatetime
    ended_at: AwareDatetime | None = None
    run_state: RunState | None = None
    sealed: bool
    image_digest: Sha256
    environment: dict[str, str]
    code_commit: str | None = None
    parameters: dict[str, Any]
    seeds: list[str]
    input_hashes: list[Sha256]
    tool_invocations: list[dict[str, Any]]
    model_invocations: list[dict[str, Any]]
    network_permitted: list[str]
    network_performed: list[str]
    resource_usage: RunResourceUsage
    cost: RunCost | None = None
    logs_ref: Urn | None = None
    error_refs: list[str]
    policy_decision_refs: list[Urn]
    produced_artifact_hashes: list[Sha256]
