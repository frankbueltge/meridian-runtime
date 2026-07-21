"""The capability dispatch layer (task-packets/K0-T02.yaml,
docs/spec/08_RESEARCH_METHOD_KERNEL.md section 1: "A Method Profile is an
executor task family plus a rulebook, not a researcher. It plugs into the
runtime's existing executor boundary (the `Executor` protocol and the
capability-based dispatch layer, which this kernel introduces).").

This is the "future dispatch layer" ``mrr.services.node_runtime.executor
.ReferenceTaskExecutor``'s own docstring already names and defers ("Selecting
WHICH `Executor` implementation should handle a given `TaskBundle` by its
`capability` is a future dispatch layer's responsibility ..., not this
class's"): a framework-free lookup from ``TaskBundle.capability.name`` to the
``Executor`` instance that should run it.

--- A pure function/table over caller-supplied data ------------------------

Mirrors ``mrr.domain.correction_impact.compute_impact``'s and
``mrr.domain.obligation_propagation.compute_obligation_binding``'s own "pure
function over caller-supplied data, no DB call inside it" precedent
(task-packets/E3-T06.yaml, task-packets/E6-T02.yaml): ``build_dispatch_table``
takes an already-resolved ``Sequence[MethodProfile]`` — plain
``mrr.contracts`` objects, a leaf module ``services/node_runtime`` already
legitimately imports elsewhere (e.g. ``TaskBundle``) — plus a caller-supplied
``Mapping[str, ExecutorFactory]``. It never calls
``MethodProfileService.find_accepted_by_capability`` itself, opens no
network, and makes no ``ObjectRepository``/``EventLog`` call — the CALLER
(production wiring in ``mrr.services.cli.orchestration.run_local_evidence_loop``,
itself in ``control_plane``, which already imports ``Executor``/
``ReferenceTaskExecutor``/``TerminalOutcome`` from ``node_runtime`` today) is
what bridges the K0-T01 registry read and this module's table-building step.

--- Two sources feed the table, treated uniformly ---------------------------

The dispatch table is built from (1) currently-accepted ``MethodProfile``s'
``executor_task_family`` capability names (K0-T01) paired with caller-supplied
``Executor`` factories for those names, and (2) the existing reference
capability, unchanged — a capability that predates the whole Method Profile
system and carries no ``MethodProfile`` declaration at all.
``build_dispatch_table`` therefore fills the table in two passes: first, every
accepted profile's declared capability name that also has a caller-supplied
factory (source (1)); second, any additional caller-supplied factory not
already covered by the first pass (source (2) — how the grandfathered
reference capability, and any other ungoverned-but-caller-supplied entry,
reaches the table). A future, stricter task MAY choose to require every
routable capability to be profile-backed (rejecting an ungoverned factory
outright); this task does not, since the reference capability itself is
exactly such an entry and must keep working "unchanged" — flagged as an open
question for reviewer attention, not a forced reading of the specification.

--- Table-building policy for a declared-but-unwired capability -------------

A ``MethodProfile`` that declares a capability name in ``executor_task_family``
with NO caller-supplied factory in ``executor_factories`` is simply ABSENT
from the built table (task-packets/K0-T02.yaml derived_decisions (e)) —
dispatching it later raises the identical ``UnknownCapabilityError`` as any
other unrecognized name, rather than ``build_dispatch_table`` itself raising a
distinct table-construction-time error. This keeps exactly one failure
surface for "this capability cannot be routed right now", whatever the
reason: no real profile-driven executor exists yet at all (K1-T03 builds the
first one), so today every non-reference profile-declared capability
necessarily falls into this "declared but unwired" case, and fails exactly
the same way an entirely unknown name would.

--- Fail closed, never a silent fallback ------------------------------------

``dispatch`` has exactly two outcomes: return the ``Executor`` registered
under ``task_bundle.capability.name``, or raise ``UnknownCapabilityError``.
There is no third outcome and no default/fallback ``Executor`` ever returned
for an unrecognized name — in particular, an unrouted capability is never
silently handed to ``ReferenceTaskExecutor``. ``UnknownCapabilityError``
RAISES and propagates unmodified to the caller (task-packets/K0-T02.yaml
derived_decisions (c)) — it is not converted into an
``ExecutionResult(outcome="policy_denied", ...)`` sealed through
``RunManifest``/``EvidenceCrate``, mirroring
``mrr.domain.exceptions.CapabilityNotDeclaredError``'s/``ScoreNotApprovedError``'s
identical existing precedent: both are pre-execution ADMISSION gates that
raise BEFORE any run ever starts, and neither produces a terminal execution
record either — a ``TaskBundle`` naming an unroutable capability never
reaches the executor at all, so there is no "run" for those to seal.

Dispatches by ``capability.name`` only, never ``capability.version``
(task-packets/K0-T02.yaml derivation, matching ``MethodProfile``'s own
``executor_task_family`` shape, K0-T01 derived_decisions (f) — capability
NAMES, not versioned refs).

This module carries no SQLAlchemy, driver, HTTP/FastAPI, or provider-SDK
import, matching every other framework-free ``mrr`` leaf (MRR-NFR-010) — see
``tests/unit/architecture/test_import_boundaries.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from mrr.contracts import MethodProfile, TaskBundle
from mrr.domain.exceptions import UnknownCapabilityError
from mrr.services.node_runtime.executor import Executor

__all__ = [
    "CapabilityDispatchTable",
    "ExecutorFactory",
    "build_dispatch_table",
    "dispatch",
]

#: A zero-argument factory that produces a fresh ``Executor`` instance —
#: injectable so a caller controls construction (e.g. ``ReferenceTaskExecutor``
#: itself, the bare class, satisfies this shape: calling it with no arguments
#: constructs an instance with its own defaults). A plain class reference is
#: the common case, not a lambda — mirrors how ``executor.py``'s own
#: ``ReferenceTransform``/``Clock``/``PolicyGate``/``CancellationCheck`` are
#: all "a caller-injectable callable of a fixed shape", not concrete objects.
ExecutorFactory = Callable[[], Executor]

#: capability name (``TaskBundle.capability.name``) -> the ``Executor``
#: instance that should run it. An immutable-shaped ``Mapping`` view — callers
#: read it with ``dispatch``; nothing in this module mutates a table after
#: ``build_dispatch_table`` returns it.
CapabilityDispatchTable = Mapping[str, Executor]


def build_dispatch_table(
    accepted_profiles: Sequence[MethodProfile],
    executor_factories: Mapping[str, ExecutorFactory],
) -> CapabilityDispatchTable:
    """Build a ``CapabilityDispatchTable`` from currently-accepted
    ``MethodProfile``\\s plus a caller-supplied mapping of capability name to
    ``Executor`` factory.

    Two passes, matching this module's own "two sources" docstring section:

    1. For every ``accepted_profiles`` entry, for every capability name in its
       ``executor_task_family``, include it if ``executor_factories`` also
       supplies a factory for that exact name. A declared name with no
       matching factory is silently skipped here, not raised (see the module
       docstring's "declared-but-unwired" section) — it simply never reaches
       the table, and dispatching it later fails exactly like any other
       unrecognized name.
    2. Any remaining ``executor_factories`` entry not already placed by pass 1
       is included too — this is how the grandfathered reference capability
       (which carries no ``MethodProfile`` declaration at all) reaches the
       table, and how a caller's ``run_local_evidence_loop``-style default
       call — ``build_dispatch_table([], {DEFAULT_CAPABILITY_NAME:
       ReferenceTaskExecutor})`` — reproduces today's single-entry default
       table byte-for-byte even with an empty profile list.

    Calls every included factory exactly once, to produce exactly one
    ``Executor`` instance per table entry.

    Args:
        accepted_profiles: already-resolved, currently-accepted
            ``MethodProfile`` objects (the caller's own responsibility to
            filter to "accepted" — this function does not itself query or
            re-check ``MethodProfile.status``).
        executor_factories: capability name -> zero-argument ``Executor``
            factory, caller-supplied.

    Returns:
        A ``CapabilityDispatchTable`` mapping every routable capability name
        found by either pass to its constructed ``Executor`` instance.
    """
    table: dict[str, Executor] = {}
    # PR #45 review follow-up (task-packets/K1-T03.yaml derived_decisions
    # (k)): collect the SET of distinct capability names declared across ALL
    # accepted_profiles entries first, then call factory() at most ONCE per
    # distinct name — not once per (profile, capability_name) pair. Without
    # this, two profiles (or two momentarily-both-"accepted"-looking
    # revisions of the same profile) declaring the identical capability name
    # would invoke factory() twice, silently discarding one constructed
    # Executor instance.
    declared_capability_names = {
        capability_name
        for profile in accepted_profiles
        for capability_name in profile.executor_task_family
    }
    for capability_name in declared_capability_names:
        factory = executor_factories.get(capability_name)
        if factory is not None:
            table[capability_name] = factory()
    for capability_name, factory in executor_factories.items():
        if capability_name not in table:
            table[capability_name] = factory()
    return table


def dispatch(task_bundle: TaskBundle, table: CapabilityDispatchTable) -> Executor:
    """Return the ``Executor`` registered in ``table`` under
    ``task_bundle.capability.name``, or raise ``UnknownCapabilityError``.

    Exactly two outcomes — no third outcome, and no default/fallback
    ``Executor`` (in particular, never ``ReferenceTaskExecutor``) is ever
    returned for an unrecognized name. See the module docstring's "Fail
    closed" section for why ``UnknownCapabilityError`` raises and propagates
    unmodified rather than being converted into a terminal
    ``ExecutionResult``.

    Args:
        task_bundle: the already-accepted ``TaskBundle`` about to execute;
            only ``task_bundle.capability.name`` is consulted (never
            ``.version`` — see the module docstring).
        table: the ``CapabilityDispatchTable`` to look up against, typically
            built by ``build_dispatch_table``.

    Returns:
        The exact ``Executor`` instance registered under the bundle's
        capability name.

    Raises:
        UnknownCapabilityError: ``task_bundle.capability.name`` matches no
            entry in ``table``.
    """
    capability_name = task_bundle.capability.name
    executor = table.get(capability_name)
    if executor is None:
        raise UnknownCapabilityError(capability_name)
    return executor
