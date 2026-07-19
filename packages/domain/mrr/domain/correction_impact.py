"""Pure, deterministic correction-impact traversal (task-packets/E3-T06.yaml):
given a set of seed "affected object" ids and a set of typed graph edges,
compute the transitive closure of DOWNSTREAM DEPENDENTS reachable through the
MRR-FR-091 impact edge types — the objects that BUILD ON a corrected object
and therefore need review. Sixth task of Epic E3 (claim, evidence, correction
kernel); the closest templates are ``mrr.domain.independence`` (E3-T05, "pure
decision logic, no persistence, no I/O") and ``mrr.domain.lifecycles``
(E1-T04, a frozen, framework-free declarative structure over the section-3/
section-6 vocabulary). This module carries no SQLAlchemy, driver, or
framework import, matching every other ``mrr.domain`` module (MRR-NFR-010).

The companion I/O-performing half — reading real typed edges via the E1-T05
``EdgeRepository``, persisting the ``CorrectionEvent``, and driving
``mrr.services.claim.service.ClaimService.require_review`` for each impacted
claim — is ``mrr.services.correction.service.CorrectionImpactService``, which
imports and calls ``compute_impact`` below rather than re-implementing any of
this module's traversal logic.

--- The FR-091 edge-type mapping (section 3 vocabulary -> FR-091 categories) --

docs/spec/01_SYSTEM_SPEC.md MRR-FR-091: "The impact service MUST traverse
dependency, derivation, citation, transfer, and publication edges." Those
five prose categories are not literal edge-type names — they must be mapped
onto docs/spec/02_DOMAIN_MODEL.md section 3's nineteen-member edge
vocabulary. task-packets/E3-T06.yaml's own ``derived_decisions`` already fixes
this mapping (it is not invented here, only transcribed and given a single
importable home):

    dependency  -> depends_on
    derivation  -> derived_from, adapted_from
    citation    -> uses_source
    transfer    -> transferred_from
    publication -> projected_into

``FR091_IMPACT_CATEGORY_EDGE_TYPES`` below is that mapping, verbatim;
``IMPACT_EDGE_TYPES`` is its flattened union — the single set actually
consulted by ``compute_impact``. Every edge type in the mapping is a genuine
member of ``mrr.domain.repositories.EDGE_VOCABULARY`` (checked by
``tests/unit/domain/test_correction_impact.py``, guarding against drift) — no
edge type is invented to fill a category. The remaining thirteen vocabulary
members (``supports``, ``contradicts``, ``qualifies``, ``contextualizes``,
``replicates``, ``fails_to_replicate``, ``supersedes``, ``corrects``,
``reviews``, ``verifies``, ``invalidates``, ``member_of_source_family``,
``subject_to_obligation``) carry no FR-091 category and therefore do NOT
propagate impact — an edge of one of those types whose target is impacted
does not make its source impacted (task-packets/E3-T06.yaml invariant: "only
the MRR-FR-091 impact edge types propagate; an irrelevant edge type (e.g.
contradicts, reviews) does not carry impact").

--- Traversal direction: downstream dependents, edges read target -> source --

"Impact flows to objects that BUILD ON a corrected object" (task-packets/
E3-T06.yaml derived_decisions). For four of the five categories the section-3
naming convention already states this directly: an edge
``source -depends_on-> target`` means *source* depends on *target* (see
``mrr.services.claim.service.ClaimService.add_dependency_edge``, which writes
exactly this shape: the newer, dependent claim is the edge's *source*, the
claim it depends on is the *target*); ``derived_from``/``adapted_from`` name
source as the newer artifact "derived/adapted FROM" the older target;
``uses_source`` names source as the citing object and target as the cited
one; ``transferred_from`` names source as the receiving copy and target as
the transfer's origin. In every one of these four, **source is the
downstream, dependent object and target is the upstream, original one** — so
if target is impacted, source (which builds on target) becomes impacted too.
``compute_impact`` therefore traverses each impact-typed edge *backward*:
from an already-impacted id (as a *target*) to that edge's *source*,
transitively, via a reverse (target -> sources) index built once over the
supplied edges.

**Open specification question, flagged for reviewer scrutiny** (not silently
resolved): ``projected_into`` is the one impact edge type whose name reads in
the OPPOSITE grammatical direction from the other four — "source projected
INTO target" most naturally reads as *source* being the upstream original
(e.g. a ``Claim``) and *target* being the downstream artifact it was
incorporated into (e.g. a report/projection), which would make target the
dependent and source the original — the reverse of the depends_on/derived_
from/uses_source/transferred_from polarity above. Read literally that way,
correcting the upstream claim (the edge's *source*) should propagate FORWARD
onto the report that embeds it (the edge's *target*) — the opposite lookup
direction from every other impact edge type — which would also line up with
MRR-FR-095's concern that a public projection surface unresolved corrections
it contains. This module does not special-case ``projected_into`` with a
reversed lookup: task-packets/E3-T06.yaml's own derived_decisions describes
ONE uniform traversal rule ("traverse edges whose TARGET is currently
impacted back to their SOURCE, transitively") across the whole impact
edge-type set with no per-type exception carved out, and there is no existing
codebase precedent anywhere that actually creates a ``projected_into`` edge
to check the convention against (grepped: the string appears only in the
vocabulary constant/docs, never in a service call). Special-casing one edge
type's direction against a plain, uniform, explicitly-stated rule would be
guessing at an unstated exception rather than implementing what the packet
actually specifies (AGENTS.md rule 3). Implemented literally as written;
flagged here (and in the PR body, per the packet's own required_output item
"whether the traversal direction reading is the intended one") for whoever
owns the eventual MRR-FR-095/E3-T07 public-projection work to confirm or
correct before a real ``projected_into`` edge is ever written by a caller.

--- Cycle-safety, idempotency, and seeds ------------------------------------

The traversal keeps a ``visited_targets`` set and only ever expands a given
id's incoming impact-edges once, so a cycle of any length (including a
self-loop, ``source_id == target_id``) terminates rather than looping
forever (task-packets/E3-T06.yaml invariant: "terminates on any graph
(including cycles and self-references)"). The returned ``impacted`` set is a
plain ``set`` — inherently deduplicated, so "each object appears once" and
identical/duplicate edges (same source/target/type, different edge ids)
never cause double-processing. ``compute_impact`` is a pure function of its
two arguments (no I/O, no mutation of its inputs, no caching) — calling it
twice with the same ``seed_ids``/``edges`` always yields the same set, and
the result never depends on the iteration order of either argument, which is
what makes it idempotent and order-independent (exercised by
``tests/property/test_correction_impact_properties.py``).

Seeds are tracked separately from the returned result, per the packet's own
recommendation ("return downstream dependents, seeds tracked separately"): a
seed id is included in the returned ``impacted`` set ONLY if it is actually
reached via a qualifying edge — typically only when the graph contains a
cycle that loops back onto a seed (see the module's cyclic test fixtures). A
seed that is never a *source* of a discovered impact edge never appears in
the result at all, even though it is, tautologically, "impacted" in the
sense of being the thing that was corrected — callers that need the full set
of correction-relevant ids combine ``seed_ids | compute_impact(seed_ids,
edges)`` themselves (``mrr.services.correction.service.
CorrectionImpactService`` keeps the two separate on the persisted
``CorrectionEvent`` too: ``affected_objects`` are the seeds,
``impact_objects`` is this function's result).

``compute_impact`` consumes ``edges`` in exactly one pass (building its
internal reverse index) before ever inspecting ``seed_ids`` against it, so a
single-use iterator (e.g. a generator) is a safe argument here, mirroring
``mrr.domain.independence.distinct_independent_reviews``'s identical
one-pass-consumption guarantee.
"""

from __future__ import annotations

from collections.abc import Iterable

from mrr.domain.repositories import TypedEdge

#: The FR-091 prose categories mapped onto their section-3 vocabulary
#: members. See the module docstring's "The FR-091 edge-type mapping"
#: section for the full derivation. Frozen as a dict of frozensets so a
#: caller can inspect which category a given edge type belongs to, in
#: addition to the flattened ``IMPACT_EDGE_TYPES`` below.
FR091_IMPACT_CATEGORY_EDGE_TYPES: dict[str, frozenset[str]] = {
    "dependency": frozenset({"depends_on"}),
    "derivation": frozenset({"derived_from", "adapted_from"}),
    "citation": frozenset({"uses_source"}),
    "transfer": frozenset({"transferred_from"}),
    "publication": frozenset({"projected_into"}),
}

#: The flattened union of every edge type across all five FR-091 categories
#: — the one set ``compute_impact`` actually consults. A vocabulary member
#: not in this set (e.g. ``contradicts``, ``reviews``, ``supports``) carries
#: no impact regardless of direction.
IMPACT_EDGE_TYPES: frozenset[str] = frozenset(
    edge_type
    for edge_types in FR091_IMPACT_CATEGORY_EDGE_TYPES.values()
    for edge_type in edge_types
)


def compute_impact(seed_ids: set[str], edges: Iterable[TypedEdge]) -> set[str]:
    """Compute the transitive closure of downstream dependents of
    ``seed_ids`` reachable via ``IMPACT_EDGE_TYPES`` edges.

    See the module docstring's "Traversal direction" section for why an
    edge's *target* being impacted propagates impact backward onto its
    *source*, and its "Cycle-safety, idempotency, and seeds" section for why
    this always terminates, never double-counts, and never returns a seed
    id unless a cycle actually loops back onto it.

    Args:
        seed_ids: the ids the correction directly names as affected
            (typically a ``CorrectionEvent.affected_objects`` id set). Not
            mutated; not included in the result unless reached via a
            qualifying edge.
        edges: every typed edge to traverse. Only entries whose
            ``edge_type`` is in ``IMPACT_EDGE_TYPES`` are consulted; every
            other edge (including a genuine vocabulary member outside the
            FR-091 categories) is read and discarded. Consumed in exactly
            one pass, so a single-use iterator is a safe argument.

    Returns:
        the set of ids downstream of ``seed_ids`` — every id reachable by
        one or more impact-typed edges, each exactly once, cycle-safe,
        deterministic, and order-independent with respect to both
        ``seed_ids`` and ``edges``.
    """
    targets_to_sources: dict[str, set[str]] = {}
    for edge in edges:
        if edge.edge_type not in IMPACT_EDGE_TYPES:
            continue
        targets_to_sources.setdefault(edge.target_id, set()).add(edge.source_id)

    impacted: set[str] = set()
    visited_targets: set[str] = set()
    frontier: set[str] = set(seed_ids)
    while frontier:
        next_frontier: set[str] = set()
        for target_id in frontier:
            if target_id in visited_targets:
                continue
            visited_targets.add(target_id)
            for source_id in targets_to_sources.get(target_id, ()):
                if source_id not in impacted:
                    impacted.add(source_id)
                    next_frontier.add(source_id)
        frontier = next_frontier
    return impacted
