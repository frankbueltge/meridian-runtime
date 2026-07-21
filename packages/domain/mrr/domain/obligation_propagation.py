"""Pure, deterministic obligation-propagation traversal (task-packets/
E6-T02.yaml derived_decisions (b)): a thin, explicitly-named wrapper —
``compute_obligation_binding`` — over ``mrr.domain.correction_impact.
compute_impact``, reusing its ``IMPACT_EDGE_TYPES`` traversal VERBATIM
rather than defining a second, divergent notion of "what does an object
built on X inherit from X".

--- Why reuse compute_impact rather than invent a second traversal ----------

No FR text defines a distinct edge set for obligation propagation the way
MRR-FR-091 names one for correction impact — this is a DERIVED choice
(task-packets/E6-T02.yaml derived_decisions (b), flagged for reviewer
confirmation in that packet's own specification_gaps item 4), not a literal
requirement. But ``mrr.domain.correction_impact``'s own module docstring
already frames its traversal as the codebase's one authoritative "builds-on"
closure and explicitly warns against a second, divergent notion of it
drifting in ("to keep this from silently drifting into a SECOND, divergent
notion of impact") — ``CorrectionImpactService._gather_impact_edges``'s own
module docstring repeats the same warning for its own query-driving BFS.
Obligation propagation asks the identical structural question — "which
objects were built on top of a bound object, transitively, cycle-safely,
idempotently" — over the identical typed-edge graph, so this module answers
it by calling the existing function directly, inheriting every one of its
already-proven guarantees (cycle-safety, idempotency, order-independence,
termination) rather than re-proving them for a second implementation.

Because ``adapted_from`` is already inside ``IMPACT_EDGE_TYPES`` (an
adaptation is one of ``compute_impact``'s five FR-091 categories,
"derivation"), seeding ``compute_obligation_binding`` from a
TransferContract's own ``transferred_objects`` ids is sufficient to also
discover an adapted local object automatically, via the ``adapted_from``
edge ``mrr.services.transfer.service.TransferService.respond`` already
writes for ``decision == "adapted"`` — no separate adaptation-lookup path
is needed anywhere in ``mrr.services.obligation.service``.

This module carries no SQLAlchemy, driver, or framework import, matching
every other ``mrr.domain`` module (MRR-NFR-010).
"""

from __future__ import annotations

from collections.abc import Iterable

from mrr.domain.correction_impact import IMPACT_EDGE_TYPES, compute_impact
from mrr.domain.repositories import TypedEdge

#: Re-exported, not redefined — the identical frozenset object
#: ``mrr.domain.correction_impact`` already declares. Importing it here
#: (rather than re-declaring an equal-but-separate frozenset) means a future
#: change to the FR-091 mapping cannot silently drift the two apart, and lets
#: a caller of this module inspect the edge-type set it uses without also
#: importing ``mrr.domain.correction_impact`` directly.
__all__ = ["IMPACT_EDGE_TYPES", "compute_obligation_binding"]


def compute_obligation_binding(seed_ids: set[str], edges: Iterable[TypedEdge]) -> set[str]:
    """Compute the transitive closure of downstream dependents of
    ``seed_ids`` (an Obligation's currently ``bound_objects``) that this
    Obligation should ALSO propagate onto — objects later found to be built
    on a bound object via adaptation, further derivation, citation,
    dependency, or projection (``IMPACT_EDGE_TYPES``).

    A direct, unconditional call to ``mrr.domain.correction_impact.
    compute_impact`` — see the module docstring for why this is a thin
    wrapper rather than a second traversal. Every one of that function's own
    guarantees (cycle-safe, idempotent, order-independent, terminates on any
    graph including cycles/self-references, a seed id is included in the
    result only if reached via a qualifying edge) applies unchanged here.

    Args:
        seed_ids: the Obligation's own ``bound_objects`` id set (never
            mutated; not included in the result unless reached via a
            qualifying edge — a caller that needs the full "everything this
            Obligation now binds" set combines ``seed_ids |
            compute_obligation_binding(seed_ids, edges)`` itself, mirroring
            ``mrr.services.correction.service.CorrectionImpactService``'s
            identical seeds-vs-computed-closure split for
            ``CorrectionEvent.affected_objects``/``impact_objects``).
        edges: every typed edge to traverse. Consumed in exactly one pass
            (``compute_impact``'s own contract), so a single-use iterator is
            a safe argument here too.

    Returns:
        the set of ids downstream of ``seed_ids`` that this Obligation
        should propagate onto, via one or more ``IMPACT_EDGE_TYPES`` edges,
        each exactly once, cycle-safe, deterministic, and order-independent
        with respect to both ``seed_ids`` and ``edges``.
    """
    return compute_impact(seed_ids, edges)
