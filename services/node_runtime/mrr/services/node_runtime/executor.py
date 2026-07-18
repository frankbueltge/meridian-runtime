"""``Executor`` (the framework-free execution Protocol) and
``ReferenceTaskExecutor`` (its deterministic reference implementation) —
task-packets/E2-T04.yaml, docs/spec/01_SYSTEM_SPEC.md section 7.5 ("Node
Runtime: ... executes approved work ..."), MRR-FR-035/040/041/043/044.

Scope, precisely: this module turns an already-accepted, signed
``TaskBundle`` (E2-T03) plus already-resolved input bytes into exactly one
explicit terminal ``ExecutionResult`` (MRR-FR-043: "Failed, cancelled,
timed-out, partially completed, and policy-denied runs MUST produce
explicit terminal records"). It does NOT:

- persist an immutable ``RunManifest`` (MRR-FR-042 — E2-T05);
- seal an evidence crate (E2-T06);
- invoke a model/LLM (E4 — MRR-FR-044 is exactly why the reference task here
  is a pure, deterministic transformation, not a stochastic one);
- resolve ``TaskBundle.inputs`` (``list[ArtifactRef]``, pointers with a
  declared content hash) into bytes — that is an artifact-store concern
  (E1-T07's ``mrr.domain.artifacts.ArtifactStore``), out of this task's
  scope. Callers hand this module already-resolved bytes via the ``inputs``
  parameter, keyed by ``ArtifactRef.artifact_id``.
- expose HTTP/FastAPI.

See ``ReferenceTaskExecutor``'s own docstring for the full HONESTY BOUNDARY:
this reference implementation is emphatically NOT a security sandbox for
untrusted code (MRR-FR-041 is the deferred OCI-executor adapter's job).
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import ClassVar, Protocol

from mrr.contracts import TaskBundle, Urn
from mrr.contracts.evidence_crate import RunState
from mrr.crypto.hashing import content_hash
from mrr.domain.exceptions import UntrustedIsolationNotAvailableError

#: The evidence-crate ``run_state`` enum (schemas/evidence-crate.schema.json),
#: reused verbatim as this module's outcome vocabulary — an alias, not a
#: redeclaration, so the two can never drift silently apart (task-packets/
#: E2-T04.yaml: "EXACTLY the evidence-crate run_state enum values (reuse; do
#: not invent)"). Six values: completed, failed, cancelled, timed_out,
#: policy_denied, partial.
TerminalOutcome = RunState


@dataclass(frozen=True, slots=True)
class ResourceUsage:
    """Minimal resource-usage record attached to an ``ExecutionResult``.

    Only ``wall_time_seconds`` for this task — richer resource/cost
    accounting (CPU seconds, memory high-water mark, disk I/O, ...) is
    ``RunManifest``'s job (E2-T05, out of this task's scope; docs/spec/
    02_DOMAIN_MODEL.md section 2.6 lists "resource and cost usage" as one of
    its own fields, not this in-memory result's).
    """

    wall_time_seconds: float


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """The single, explicit outcome of one execution attempt (MRR-FR-043).

    There is deliberately no generic error object anywhere on this type —
    ``outcome`` (a ``TerminalOutcome``) always names exactly what happened,
    and ``detail`` carries human-readable context for the non-``completed``
    (or ``partial``) cases. A caller that wants to know "did this run
    succeed" reads ``outcome``, never catches an exception and never checks
    a boolean.

    Fields:
        outcome: exactly one of ``TerminalOutcome``'s six values.
        output: the produced bytes, or ``None`` when no output was produced
            (``failed``/``timed_out``/``cancelled``/``policy_denied``).
            ``completed``/``partial`` always carry bytes.
        output_hash: ``mrr.crypto.hashing.content_hash(output)`` when
            ``output`` is not ``None``, else ``None``. Never computed by the
            caller — this is the one place a result's own hash is minted, so
            it can never disagree with ``output``.
        is_deterministic: MRR-FR-044 — whether this execution was a
            deterministic transformation (``True``) or a stochastic,
            model-assisted operation (``False``, never produced by
            ``ReferenceTaskExecutor``, which is always ``True``). Describes
            the TASK's nature, not whether it happened to succeed — a
            failed or timed-out run of a deterministic task is still a fact
            about a deterministic task.
        execution_attempt: the caller-supplied attempt number this result is
            for (MRR-FR-035 idempotency key, together with ``task_id`` and
            ``task_revision``).
        task_id: the executed ``TaskBundle.id``.
        task_revision: the executed ``TaskBundle.revision``.
        resource_usage: minimal wall-clock accounting (see ``ResourceUsage``).
        detail: human-readable context for a non-``completed`` outcome (or
            for ``partial``, naming what was missing); ``None`` for a clean
            ``completed`` run with nothing further to say.
    """

    outcome: TerminalOutcome
    output: bytes | None
    output_hash: str | None
    is_deterministic: bool
    execution_attempt: int
    task_id: Urn
    task_revision: int
    resource_usage: ResourceUsage
    detail: str | None = None


class Executor(Protocol):
    """The provider-neutral execution boundary (docs/spec/01_SYSTEM_SPEC.md
    section 7.5). Framework-free by construction: this module imports no
    FastAPI/Temporal/model-provider SDK (enforced by the same import-linter
    "Core packages stay framework- and provider-free" contract's sibling —
    see ``tests/unit/architecture/test_import_boundaries.py``), and this
    Protocol's signature has no opinion about *how* an implementation runs a
    task, only that it returns an explicit ``ExecutionResult``.

    An implementation MAY provide real MRR-FR-041 sandbox isolation
    (non-root, read-only base filesystem, explicit writable mounts,
    deny-by-default network egress, cgroup CPU/memory/disk limits) — that is
    the deferred OCI-executor adapter's job. ``ReferenceTaskExecutor`` below,
    this module's only implementation today, deliberately does NOT provide
    such isolation and says so at its own interface; see its docstring's
    HONESTY BOUNDARY section before treating any ``Executor`` as a security
    boundary for untrusted code.
    """

    def execute(
        self,
        task_bundle: TaskBundle,
        inputs: Mapping[str, bytes],
        *,
        execution_attempt: int,
    ) -> ExecutionResult:
        """Run ``task_bundle`` against already-resolved ``inputs`` (bytes
        keyed by ``ArtifactRef.artifact_id``, resolved by the caller — this
        Protocol has no opinion about artifact resolution) for the given
        ``execution_attempt``, and return exactly one explicit
        ``ExecutionResult`` (MRR-FR-043) — never raise for a task-level
        outcome. Idempotent per ``(task_bundle.id, task_bundle.revision,
        execution_attempt)`` (MRR-FR-035): implementations MUST return the
        same result, without re-running side effects, when called again
        with the same triple.
        """
        ...


#: The pure transform an ``Executor`` implementation runs over already
#: resolved input bytes to produce deterministic output bytes. Injectable on
#: ``ReferenceTaskExecutor`` so tests can substitute a call-counting variant
#: (idempotency) or a deliberately slow variant (genuinely exercising the
#: ``timed_out`` path) without changing the executor's own logic.
ReferenceTransform = Callable[[Mapping[str, bytes]], bytes]

#: A monotonic wall-clock reader, ``time.monotonic`` by default. Injectable
#: so tests can assert on ``resource_usage.wall_time_seconds`` without
#: depending on real timing noise.
Clock = Callable[[], float]

#: A local-policy pre-check: given the ``TaskBundle`` about to run, return
#: ``True`` to allow execution, ``False`` to deny it before anything runs.
#: This is this task's own minimal modeling of "a policy pre-check that
#: denies returns policy_denied without executing" (task-packets/
#: E2-T04.yaml derived_decisions) — a full Policy Gateway (docs/spec/
#: 01_SYSTEM_SPEC.md section 7.4, with recorded policy-decision objects) is
#: a later task's responsibility, flagged as an open specification question
#: in this task's PR.
PolicyGate = Callable[[TaskBundle], bool]

#: A cancellation pre-check, queried once immediately before the reference
#: computation runs: ``True`` means this specific execution attempt is
#: cancelled and must not run. This task's own minimal modeling of "a
#: cancellation signal returns cancelled" (task-packets/E2-T04.yaml
#: derived_decisions) — there is no mid-run cancellation of an in-flight
#: reference computation (it is bounded and fast; genuinely killing a
#: running untrusted task is the deferred OCI adapter's job, same honesty
#: boundary as the wall-clock timeout below).
CancellationCheck = Callable[[], bool]


def default_reference_transform(inputs: Mapping[str, bytes]) -> bytes:
    """The default deterministic reference computation (MRR-FR-044): a pure,
    canonical transform over already-resolved input bytes.

    Sorts ``inputs`` by key (never relies on ``dict`` iteration/insertion
    order — two callers resolving the same artifacts in a different order
    must still agree on the output) and, for each key, hashes its bytes with
    SHA-256, then joins ``"<key>:<hex digest>"`` lines with ``\\n``. No
    wall-clock reads, no randomness, no filesystem access, no locale- or
    platform-dependent behavior: byte-identical ``inputs`` always produce
    byte-identical output, on any run, any machine, any Python 3.12+
    process — the replayability MRR-FR-044 and this task's idempotency
    invariant both depend on.
    """
    lines = [f"{key}:{hashlib.sha256(value).hexdigest()}" for key, value in sorted(inputs.items())]
    return ("\n".join(lines) + "\n").encode("utf-8")


class ReferenceTaskExecutor:
    """The deterministic reference ``Executor`` implementation (MRR-FR-044):
    a bounded, pure, in-process computation over already-resolved input
    bytes, returning an explicit ``ExecutionResult`` for every call.

    --- What this executor actually runs -----------------------------------

    ``task_bundle.execution.image_digest``/``entrypoint`` name an OCI
    container image and entrypoint meant to run under real sandbox isolation
    (MRR-FR-041). This reference implementation does NOT run that container
    — no OCI runtime is available in this environment, and pulling/running
    an arbitrary image in-process would itself be a fabricated isolation
    claim, exactly what the honesty boundary below forbids. Instead it runs
    one fixed, pure Python computation — ``default_reference_transform``, or
    an injected ``transform`` of the same shape — over the ``inputs``
    mapping. Selecting WHICH ``Executor`` implementation should handle a
    given ``TaskBundle`` by its ``capability`` is a future dispatch layer's
    responsibility (e.g. the deferred OCI-executor adapter's own capability
    check), not this class's: a caller must itself only route genuinely
    reference-shaped bundles here.

    --- Determinism (MRR-FR-044) --------------------------------------------

    ``is_deterministic=True`` on every returned ``ExecutionResult``,
    including non-``completed`` ones — a failed, timed-out, cancelled, or
    policy-denied run of a deterministic task is still a fact about a
    deterministic task, not a stochastic one. See
    ``default_reference_transform`` for why it is genuinely replayable
    across runs and machines.

    --- Wall-clock bound (MRR-FR-040) ---------------------------------------

    ``task_bundle.resource_limits.timeout_seconds`` (schema-required,
    integer ``>= 1``) bounds a real wall-clock budget. The transform runs on
    a single worker thread; ``concurrent.futures.Future.result(timeout=...)``
    detects an overrun and this method returns ``timed_out`` promptly at the
    bound, rather than blocking until the slow call finishes. Python cannot
    forcibly kill a running thread, so a genuinely stuck transform keeps
    running to completion in the background after ``execute`` has already
    returned ``timed_out`` — harmless for this executor's own pure,
    side-effect-free reference transform, but exactly the gap real process
    isolation (killable, resource-limited) closes for untrusted code. That
    gap is this class's own reason to defer real isolation to the OCI
    adapter, not a bug to paper over here.

    --- Idempotency (MRR-FR-035) ---------------------------------------------

    Keyed by ``(task_bundle.id, task_bundle.revision, execution_attempt)``:
    an in-memory ``dict`` memoizes every outcome (not only ``completed``)
    the first time that exact triple executes; any later call with the same
    triple returns the memoized ``ExecutionResult`` directly, without
    invoking the policy gate, the cancellation check, or the transform
    again. "Does not double-execute side effects" holds trivially for a pure
    reference task, but the memo makes that true by construction, not by
    accident — a test with a call-counting ``transform`` pins it. A
    *different* ``execution_attempt`` for the same ``(id, revision)`` is a
    fresh, still-deterministic run. The memo is process-local and lives only
    for this instance's lifetime — durable idempotency across process
    restarts is ``RunManifest`` persistence's job (E2-T05, out of scope
    here).

    --- HONESTY BOUNDARY (critical — do not soften) --------------------------

    This executor is for the TRUSTED, deterministic reference task ONLY. It
    is NOT a security sandbox and MUST NOT be presented or used as one:

    - no non-root / read-only-base-filesystem / explicit-writable-mount
      enforcement;
    - no deny-by-default network egress enforcement (the reference
      transform never touches the network, but nothing here would stop one
      that did);
    - no cgroup CPU/memory/disk limits — only the wall-clock bound above,
      and even that cannot forcibly kill an overrunning thread.

    All of that (MRR-FR-041) is the deferred OCI-executor adapter's
    responsibility, exactly as E1-T07 deferred its MinIO object-store
    adapter. ``provides_untrusted_isolation`` is ``False`` on this class,
    always — a documented, pinned contract (see
    ``tests/unit/services/node_runtime/test_executor.py::
    test_reference_executor_does_not_claim_isolation``), not a runtime
    capability probe. Constructing an instance with ``require_isolation=True``
    — an explicit, generic request for isolation guarantees — raises
    ``UntrustedIsolationNotAvailableError`` immediately, so this class
    refuses to hand out an instance that would silently pretend to isolate
    untrusted code.
    """

    #: See the HONESTY BOUNDARY section above. Always ``False`` for this
    #: class — pinned by a dedicated test so it cannot silently regress into
    #: implying a security sandbox.
    provides_untrusted_isolation: ClassVar[bool] = False

    def __init__(
        self,
        *,
        transform: ReferenceTransform = default_reference_transform,
        clock: Clock = time.monotonic,
        policy_gate: PolicyGate | None = None,
        is_cancelled: CancellationCheck | None = None,
        require_isolation: bool = False,
    ) -> None:
        """
        Args:
            transform: the deterministic computation to run over resolved
                inputs. Defaults to ``default_reference_transform``; tests
                inject call-counting or deliberately slow variants.
            clock: a monotonic wall-clock reader used to measure
                ``resource_usage.wall_time_seconds``. Defaults to
                ``time.monotonic``.
            policy_gate: an optional local-policy pre-check (see
                ``PolicyGate``). ``None`` (the default) means "no policy
                gate configured" — every execution is allowed through to the
                cancellation check and the transform.
            is_cancelled: an optional cancellation pre-check (see
                ``CancellationCheck``). ``None`` (the default) means
                "cancellation is never signaled".
            require_isolation: an explicit, generic request for
                untrusted-code isolation guarantees. Always raises
                ``UntrustedIsolationNotAvailableError`` when ``True`` — see
                the HONESTY BOUNDARY above.

        Raises:
            UntrustedIsolationNotAvailableError: ``require_isolation`` is
                ``True``.
        """
        if require_isolation:
            raise UntrustedIsolationNotAvailableError()
        self._transform = transform
        self._clock = clock
        self._policy_gate = policy_gate
        self._is_cancelled = is_cancelled
        self._memo: dict[tuple[str, int, int], ExecutionResult] = {}

    def execute(
        self,
        task_bundle: TaskBundle,
        inputs: Mapping[str, bytes],
        *,
        execution_attempt: int,
    ) -> ExecutionResult:
        """See ``Executor.execute`` for the general contract. Raises
        ``ValueError`` only for the programmer error of a non-positive
        ``execution_attempt`` — every other outcome, including a raising
        reference task, a wall-clock overrun, a policy denial, or a
        cancellation signal, is reported as an explicit ``ExecutionResult``,
        never an exception.
        """
        if execution_attempt < 1:
            raise ValueError(f"execution_attempt must be >= 1, got {execution_attempt!r}")

        key = (task_bundle.id, task_bundle.revision, execution_attempt)
        memoized = self._memo.get(key)
        if memoized is not None:
            return memoized

        result = self._execute_uncached(task_bundle, inputs, execution_attempt=execution_attempt)
        self._memo[key] = result
        return result

    def _execute_uncached(
        self,
        task_bundle: TaskBundle,
        inputs: Mapping[str, bytes],
        *,
        execution_attempt: int,
    ) -> ExecutionResult:
        def _result(
            outcome: TerminalOutcome,
            *,
            output: bytes | None = None,
            output_hash: str | None = None,
            wall_time_seconds: float = 0.0,
            detail: str | None = None,
        ) -> ExecutionResult:
            return ExecutionResult(
                outcome=outcome,
                output=output,
                output_hash=output_hash,
                is_deterministic=True,
                execution_attempt=execution_attempt,
                task_id=task_bundle.id,
                task_revision=task_bundle.revision,
                resource_usage=ResourceUsage(wall_time_seconds=wall_time_seconds),
                detail=detail,
            )

        # Policy pre-check (MRR-FR-040: "under the target node's local
        # policy") — denied before anything executes, no side effects.
        if self._policy_gate is not None and not self._policy_gate(task_bundle):
            return _result(
                "policy_denied",
                detail="local policy denied execution of this task bundle",
            )

        # Cancellation pre-check, queried once, before running.
        if self._is_cancelled is not None and self._is_cancelled():
            return _result(
                "cancelled",
                detail="execution attempt was cancelled before running",
            )

        declared_artifact_ids = {ref.artifact_id for ref in task_bundle.inputs}
        missing_artifact_ids = declared_artifact_ids - set(inputs.keys())

        timeout_seconds = task_bundle.resource_limits.timeout_seconds
        start = self._clock()
        pool: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=1)
        try:
            future: Future[bytes] = pool.submit(self._transform, inputs)
            try:
                output = future.result(timeout=timeout_seconds)
            except FutureTimeoutError:
                elapsed = self._clock() - start
                return _result(
                    "timed_out",
                    wall_time_seconds=elapsed,
                    detail=(
                        f"execution exceeded the {timeout_seconds}s wall-clock bound "
                        "(resource_limits.timeout_seconds)"
                    ),
                )
            except Exception as exc:
                elapsed = self._clock() - start
                return _result(
                    "failed",
                    wall_time_seconds=elapsed,
                    detail=f"{type(exc).__name__}: {exc}",
                )
        finally:
            # cancel_futures=True only affects not-yet-started work (there is
            # none, max_workers=1); wait=False lets execute() return promptly
            # at the timeout bound instead of blocking on a stuck transform —
            # see the class docstring's wall-clock-bound section for why this
            # cannot forcibly kill an overrunning thread.
            pool.shutdown(wait=False, cancel_futures=True)

        elapsed = self._clock() - start
        output_hash = content_hash(output)
        if missing_artifact_ids:
            return _result(
                "partial",
                output=output,
                output_hash=output_hash,
                wall_time_seconds=elapsed,
                detail=(
                    "reference task ran on the resolved inputs only; declared "
                    f"artifact_id(s) not resolved: {sorted(missing_artifact_ids)!r}"
                ),
            )
        return _result(
            "completed",
            output=output,
            output_hash=output_hash,
            wall_time_seconds=elapsed,
        )
