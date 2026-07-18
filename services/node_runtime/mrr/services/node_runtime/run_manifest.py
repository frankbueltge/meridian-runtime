"""``RunManifestRecorder`` (task-packets/E2-T05.yaml): turns an executor
``ExecutionResult`` (E2-T04) plus the ``TaskBundle`` (E2-T03) it executed
into a persisted, sealed ``RunManifest`` (docs/spec/02_DOMAIN_MODEL.md
section 2.6; MRR-FR-042/043/053) — recorded before any evidence crate is
sealed (E2-T06, out of this task's scope).

Scope, precisely: this module records the immutable run manifest. It does
NOT:

- seal an evidence crate (E2-T06);
- invoke a model/LLM or populate ``tool_invocations``/``model_invocations``
  beyond the empty placeholder arrays MRR-FR-044/045 defer to E4;
- expose HTTP/FastAPI;
- offer any update/mutate method — see "Immutability" below.

--- Why this recorder needs no ObjectRepository/EventLog reads ------------

Every other service in this codebase (``ResearchScoreService``,
``CapabilityRegistry``, ``TaskBundleService``) manages the lifecycle of an
EXISTING object across multiple calls, so its constructor takes an
``ObjectRepository`` (to read the latest revision) and an event journal (to
compute ``causation_id``). ``RunManifestRecorder`` has exactly one
operation, ``record``, and it always mints a brand-new run identity
(``mrr.domain.identity.new_urn("run")``) written once, at revision 1 — there
is no prior revision to read and no prior event for that fresh id to chain
from (``causation_id`` is always ``None``, exactly like every other
service's own ``create()``/``register()`` for a brand-new object). The
constructor therefore only needs the bound ``RecordRevisionWithEvent``
write callable, not a read-capable repository or event journal.

--- Identity: RunManifest.id names "the run" -------------------------------

Per ``mrr.contracts.run_manifest``'s own docstring, ``RunManifest.id`` is
minted with URN entity segment ``run`` (not ``run-manifest``), matching what
``schemas/evidence-crate.schema.json``'s ``run_id`` field already
references. A future E2-T06 evidence-crate sealing step's own ``run_id``
should be exactly the ``id`` this recorder returns.

--- Deviation from a literal "caller mints id/content_hash/created_*" ------

task-packets/E2-T05.yaml's illustrative method signature says "Caller mints
id/content_hash/created_* (consistent with the other services)" — true for
``create()``/``register()``, which receive an ALREADY fully-authored
contract object (the caller built the whole thing, typically because it
also had to sign it). ``RunManifest`` carries no signature (per
``mrr.contracts.run_manifest``'s own derived_decisions note) and this
recorder's whole job is to ASSEMBLE the object from ``ExecutionResult`` +
``TaskBundle`` pieces the caller does not otherwise combine — requiring the
caller to pre-mint ``content_hash`` would mean either the caller duplicates
this module's entire assembly logic just to hash it (real duplication risk:
the two copies could drift), or this recorder exposes its draft body as a
public pre-step. Neither is worth the ceremony for an unsigned object, so
this recorder mints ``id``, ``created_at``, and ``content_hash`` internally
after assembling the full body — exactly the pattern
``mrr.services.research_score.service.ResearchScoreService._transition``
already uses for its own lifecycle-transition writes (also unsigned, also
computes ``compute_content_hash(new_body)`` itself). ``created_by`` is not a
separate parameter; it is always ``actor`` (also matching ``_transition``),
avoiding a redundant field that could silently diverge from ``actor``.
``practice_id`` IS caller-supplied (a required keyword argument) — unlike
``id``/``content_hash``/``created_at``, it cannot be derived from
``ExecutionResult``/``TaskBundle`` without guessing (the executing node's own
practice is not necessarily ``TaskBundle.origin_practice_id``, and no
node-to-practice lookup is in this task's scope). Flagged in the PR for
reviewer scrutiny, per the task's own instruction.

--- Immutability: no update/mutate method -----------------------------------

docs/spec/02_DOMAIN_MODEL.md section 2.6's own invariant: "a run manifest is
append-only while active and sealed at terminal state. Corrections create
annotations or superseding manifests." This class offers exactly one public
method, ``record``, and it is called exactly once per run (mirroring
``ReferenceTaskExecutor``'s own one-attempt-per-``execution_attempt``
idempotency, one level up the stack). There is no ``update``/``seal``/
``correct`` method anywhere on this class, by construction — a correction to
an already-recorded manifest is a NEW ``RunManifest`` (a superseding
manifest, presumably carrying ``supersedes`` set to the original's ``id``),
which is out of this task's scope and left for a future task, exactly as
task-packets/E2-T05.yaml's derived_decisions directs.

--- Always sealed, every terminal outcome ------------------------------------

Since the reference executor runs synchronously (E2-T04: ``execute()``
already returns a terminal ``ExecutionResult`` before this recorder is ever
called), every manifest this recorder builds has ``sealed=True`` and a
non-``None`` ``ended_at``/``run_state`` from the moment it is written —
there is no "active, not yet sealed" manifest anywhere in this task's own
code path. ``run_state`` is set to ``execution_result.outcome`` unconditionally
for ALL six ``TerminalOutcome`` values, not only ``completed`` (MRR-FR-043):
a failed, timed-out, cancelled, or policy-denied run is recorded with that
exact terminal state, with no special-casing anywhere in this module. A
future asynchronous executor would need a genuinely different code path
(create active with ``sealed=False``, later a distinct seal step) — out of
this task's scope, and noted as an open specification question in the PR.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime

from mrr.contracts import RunCost, RunManifest, RunResourceUsage, TaskBundle, Urn
from mrr.domain.hashing_policy import compute_content_hash
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.persistence.unit_of_work import record_object_revision_with_event
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.node_runtime.executor import ExecutionResult
from sqlalchemy import Engine

#: docs/spec/01_SYSTEM_SPEC.md MRR-FR-042's event: every recorded manifest
#: writes exactly one of these (task-packets/E2-T05.yaml invariant:
#: "recording a manifest writes exactly one domain event with full NFR-001
#: provenance, atomically with the persisted revision").
_EVENT_RECORDED = "run_manifest.recorded"

#: The callable shape ``mrr.persistence.unit_of_work.record_object_revision_with_event``
#: takes once its ``engine``/``object_repository``/``event_log`` arguments
#: are bound. Identical in shape to every other service's own
#: ``RecordRevisionWithEvent`` — see the module docstring for why this is a
#: local copy, not a shared import, across separate service modules.
RecordRevisionWithEvent = Callable[
    [StoredObject, int | None, DomainEvent], tuple[StoredObject, AppendedEvent]
]


def bind_unit_of_work(
    engine: Engine,
    object_repository: PostgresObjectRepository,
    event_log: PostgresEventLog,
) -> RecordRevisionWithEvent:
    """Bind ``record_object_revision_with_event`` to a concrete
    ``sqlalchemy.Engine``/``PostgresObjectRepository``/``PostgresEventLog``
    triple, producing the ``RecordRevisionWithEvent`` callable
    ``RunManifestRecorder`` depends on for its one atomic write. Production
    wiring and integration tests call this once; DB-free unit tests pass
    their own trivial callable of the same shape, backed by an in-memory
    fake, instead.
    """

    def _record(
        obj: StoredObject,
        expected_current_revision: int | None,
        event: DomainEvent,
    ) -> tuple[StoredObject, AppendedEvent]:
        return record_object_revision_with_event(
            engine, object_repository, event_log, obj, expected_current_revision, event
        )

    return _record


def _manifest_to_stored_object(manifest: RunManifest) -> StoredObject:
    """Convert an already-valid ``RunManifest`` into the generic
    ``StoredObject`` ``mrr.domain.repositories.ObjectRepository`` persists.
    ``body`` is a plain ``model_dump_json(exclude_none=True)`` round trip —
    no added keys — matching every other service's own ``_*_to_stored_object``
    helper.
    """
    body = json.loads(manifest.model_dump_json(exclude_none=True))
    return StoredObject(
        id=manifest.id,
        api_version=manifest.api_version,
        kind=manifest.kind,
        practice_id=manifest.practice_id,
        revision=manifest.revision,
        created_at=manifest.created_at,
        created_by=manifest.created_by,
        content_hash=manifest.content_hash,
        supersedes=manifest.supersedes,
        labels=manifest.labels,
        body=body,
    )


def _network_permitted(task_bundle: TaskBundle) -> list[str]:
    """The network accesses this run was PERMITTED (not necessarily
    performed): ``task_bundle.network_policy.allowlist`` when the policy
    mode is ``"allowlist"``, else an empty list (``"deny_all"`` permits
    nothing, regardless of any stray allowlist entries).
    """
    if task_bundle.network_policy.mode == "allowlist":
        return list(task_bundle.network_policy.allowlist)
    return []


class RunManifestRecorder:
    """docs/spec/01_SYSTEM_SPEC.md section 7.5 / MRR-FR-042: records an
    immutable ``RunManifest`` for one executor ``ExecutionResult``. See the
    module docstring for the full design (why no read dependency, the
    identity-minting deviation, immutability, and "every terminal outcome,
    not only completed").
    """

    def __init__(self, record: RecordRevisionWithEvent) -> None:
        self._record = record

    def record(
        self,
        execution_result: ExecutionResult,
        task_bundle: TaskBundle,
        *,
        practice_id: Urn,
        executor_id: Urn,
        executor_role: str,
        started_at: datetime,
        ended_at: datetime,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
        environment: Mapping[str, str] | None = None,
        seeds: Sequence[str] = (),
        cost: RunCost | None = None,
        logs_ref: Urn | None = None,
        error_refs: Sequence[str] = (),
        policy_decision_refs: Sequence[Urn] = (),
    ) -> StoredObject:
        """Build a schema-valid, already-sealed ``RunManifest`` from
        ``execution_result``/``task_bundle`` and persist it as revision 1
        plus a ``run_manifest.recorded`` event, atomically.

        Every field derivable from ``execution_result``/``task_bundle`` is
        derived here, not accepted as a parameter (so it can never disagree
        with the run it actually describes):

        - ``task_id``/``task_revision`` <- ``task_bundle.id``/``.revision``.
        - ``research_score_id``/``research_score_revision`` <-
          ``task_bundle.research_score_id``/``.research_score_revision`` (the
          score reference the bundle itself declares, not a value a caller
          could pass out of step with the bundle actually executed).
        - ``image_digest``/``code_commit`` <-
          ``task_bundle.execution.image_digest``/``.code_revision``.
        - ``parameters`` <- ``task_bundle.instructions``.
        - ``input_hashes`` <- ``[ref.content_hash for ref in
          task_bundle.inputs]`` (the declared input references, regardless
          of whether every one was actually resolved — see
          ``ExecutionResult``'s own ``partial`` outcome for the case where
          some were not).
        - ``network_permitted`` <- ``task_bundle.network_policy`` (see
          ``_network_permitted``). ``network_performed`` is always ``[]``:
          the reference executor never performs network I/O (a pure,
          in-process transform), so recording anything else would be
          fabricated telemetry, not an observation.
        - ``run_state`` <- ``execution_result.outcome`` (all six
          ``TerminalOutcome`` values, unconditionally).
        - ``resource_usage.wall_seconds`` <-
          ``execution_result.resource_usage.wall_time_seconds``.
        - ``produced_artifact_hashes`` <- ``[execution_result.output_hash]``
          when not ``None``, else ``[]``.
        - ``tool_invocations``/``model_invocations`` are always ``[]`` (E4
          scope; this task never populates them).
        - ``sealed`` is always ``True``.

        Everything else this task cannot derive is caller-supplied:
        ``practice_id``, ``executor_id``, ``executor_role``,
        ``started_at``/``ended_at`` (real wall-clock timestamps bracketing
        the executor call — the executor's own ``resource_usage`` is a
        monotonic-clock duration, not convertible to absolute timestamps),
        and the optional ``environment``/``seeds``/``cost``/``logs_ref``/
        ``error_refs``/``policy_decision_refs`` (all default to "not
        available" — empty/``None`` — for the reference executor, which
        does not itself produce any of these).

        Raises:
            ValueError: ``execution_result.task_id``/``.task_revision``
                does not match ``task_bundle.id``/``.revision`` (the caller
                passed a result/bundle pair that do not describe the same
                execution), or ``ended_at`` is before ``started_at``.
        """
        if execution_result.task_id != task_bundle.id:
            raise ValueError(
                f"execution_result.task_id ({execution_result.task_id!r}) does not match "
                f"task_bundle.id ({task_bundle.id!r})"
            )
        if execution_result.task_revision != task_bundle.revision:
            raise ValueError(
                f"execution_result.task_revision ({execution_result.task_revision!r}) does "
                f"not match task_bundle.revision ({task_bundle.revision!r})"
            )
        if ended_at < started_at:
            raise ValueError(
                f"ended_at ({ended_at.isoformat()!r}) is before started_at "
                f"({started_at.isoformat()!r})"
            )

        manifest_id = new_urn("run")
        now = datetime.now(UTC)
        produced_artifact_hashes = (
            [execution_result.output_hash] if execution_result.output_hash is not None else []
        )

        draft = RunManifest(
            id=manifest_id,
            api_version="mrr/v1alpha1",
            kind="RunManifest",
            practice_id=practice_id,
            revision=1,
            created_at=now,
            created_by=actor,
            content_hash="sha256:" + "0" * 64,  # placeholder; recomputed below
            task_id=task_bundle.id,
            task_revision=task_bundle.revision,
            research_score_id=task_bundle.research_score_id,
            research_score_revision=task_bundle.research_score_revision,
            executor_id=executor_id,
            executor_role=executor_role,
            started_at=started_at,
            ended_at=ended_at,
            run_state=execution_result.outcome,
            sealed=True,
            image_digest=task_bundle.execution.image_digest,
            environment=dict(environment) if environment is not None else {},
            code_commit=task_bundle.execution.code_revision,
            parameters=dict(task_bundle.instructions),
            seeds=list(seeds),
            input_hashes=[ref.content_hash for ref in task_bundle.inputs],
            tool_invocations=[],
            model_invocations=[],
            network_permitted=_network_permitted(task_bundle),
            network_performed=[],
            resource_usage=RunResourceUsage(
                wall_seconds=execution_result.resource_usage.wall_time_seconds
            ),
            cost=cost,
            logs_ref=logs_ref,
            error_refs=list(error_refs),
            policy_decision_refs=list(policy_decision_refs),
            produced_artifact_hashes=produced_artifact_hashes,
        )

        body = json.loads(draft.model_dump_json(exclude_none=True))
        real_content_hash = compute_content_hash(body)
        body["content_hash"] = real_content_hash
        manifest = RunManifest.model_validate(body)

        obj = _manifest_to_stored_object(manifest)
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=_EVENT_RECORDED,
            occurred_at=now,
            actor=actor,
            policy_version=policy_version,
            causation_id=None,
            correlation_id=correlation_id,
            object_id=manifest_id,
            object_revision=1,
            payload={
                "task_id": task_bundle.id,
                "task_revision": task_bundle.revision,
                "executor_id": executor_id,
                "run_state": execution_result.outcome,
                "sealed": True,
            },
        )
        stored, _ = self._record(obj, None, event)
        return stored
