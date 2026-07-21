"""Unit tests for ``mrr.domain.obligation_propagation`` (task-packets/
E6-T02.yaml derived_decisions (b)), run entirely DB-free and framework-free
— plain ``TypedEdge`` fixtures, no repository, no engine.

``compute_obligation_binding`` is a thin, unconditional wrapper over
``mrr.domain.correction_impact.compute_impact`` (see that module's own
already-exhaustive fixture suite, ``tests/unit/domain/
test_correction_impact.py`` — line/branching/cyclic/self-loop/disconnected/
duplicate-edge/order-independence/idempotency), so this module's own tests
focus on two things: (1) that the wrapper genuinely delegates rather than
diverging (mirroring a representative subset of the same fixtures at this
module's own entry point, per task-packets/E6-T02.yaml's acceptance test
wording, "exercised again at this module's own entry point"), and (2) that
``IMPACT_EDGE_TYPES`` is re-exported unchanged, not redeclared.

Acceptance-test mapping (task-packets/E6-T02.yaml):

- "line graph, branching graph, cyclic graph, disconnected graph" (unit-level
  slice) -> ``test_line_graph_binds_the_whole_chain``,
  ``test_branching_graph_binds_every_branch``,
  ``test_cyclic_graph_terminates``, ``test_disconnected_component_is_not_bound``.
- "duplicate edges - no double-processing" ->
  ``test_duplicate_edges_do_not_cause_double_processing``.
- delegation, not divergence -> ``test_delegates_directly_to_compute_impact``,
  ``test_impact_edge_types_is_the_same_object_as_correction_impacts``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from mrr.domain.correction_impact import IMPACT_EDGE_TYPES as _CORRECTION_IMPACT_EDGE_TYPES
from mrr.domain.correction_impact import compute_impact
from mrr.domain.identity import new_urn
from mrr.domain.obligation_propagation import IMPACT_EDGE_TYPES, compute_obligation_binding
from mrr.domain.repositories import TypedEdge


def _edge(source_id: str, edge_type: str, target_id: str) -> TypedEdge:
    return TypedEdge(
        id=new_urn("edge"),
        source_id=source_id,
        target_id=target_id,
        edge_type=edge_type,
        created_at=datetime.now(UTC),
        created_by=new_urn("agent-role"),
        scope=None,
        status="active",
        practice_id=new_urn("practice"),
    )


# ---------------------------------------------------------------------------
# Re-export, not redeclaration.
# ---------------------------------------------------------------------------


def test_impact_edge_types_is_the_same_object_as_correction_impacts() -> None:
    """A future FR-091 mapping change cannot silently drift the two apart —
    this module imports, rather than redeclares, the flattened edge-type set.
    """
    assert IMPACT_EDGE_TYPES is _CORRECTION_IMPACT_EDGE_TYPES


# ---------------------------------------------------------------------------
# Delegation: compute_obligation_binding == compute_impact, always.
# ---------------------------------------------------------------------------


def test_delegates_directly_to_compute_impact() -> None:
    a, b, c = (new_urn("claim") for _ in range(3))
    edges = [_edge(b, "depends_on", a), _edge(c, "derived_from", b)]
    seed_ids = {a}

    assert compute_obligation_binding(seed_ids, edges) == compute_impact(seed_ids, edges)


# ---------------------------------------------------------------------------
# Line graph: binds the whole chain, once each.
# ---------------------------------------------------------------------------


def test_line_graph_binds_the_whole_chain() -> None:
    a, b, c, d = (new_urn("claim") for _ in range(4))
    edges = [
        _edge(b, "depends_on", a),
        _edge(c, "depends_on", b),
        _edge(d, "depends_on", c),
    ]

    bound = compute_obligation_binding({a}, edges)

    assert bound == {b, c, d}


# ---------------------------------------------------------------------------
# Branching graph: every branch bound, once each.
# ---------------------------------------------------------------------------


def test_branching_graph_binds_every_branch() -> None:
    a, b, c, d = (new_urn("claim") for _ in range(4))
    edges = [
        _edge(b, "depends_on", a),
        _edge(c, "depends_on", a),
        _edge(d, "depends_on", b),
    ]

    bound = compute_obligation_binding({a}, edges)

    assert bound == {b, c, d}


# ---------------------------------------------------------------------------
# Cyclic graph: terminates, each object appears once.
# ---------------------------------------------------------------------------


def test_cyclic_graph_terminates() -> None:
    a, b, c = (new_urn("claim") for _ in range(3))
    edges = [
        _edge(b, "depends_on", a),
        _edge(c, "depends_on", b),
        _edge(a, "depends_on", c),
    ]

    bound = compute_obligation_binding({a}, edges)

    assert bound == {a, b, c}


def test_self_loop_terminates() -> None:
    a = new_urn("claim")
    edges = [_edge(a, "depends_on", a)]

    assert compute_obligation_binding({a}, edges) == {a}


# ---------------------------------------------------------------------------
# Duplicate edges: no double-processing.
# ---------------------------------------------------------------------------


def test_duplicate_edges_do_not_cause_double_processing() -> None:
    a, b = new_urn("claim"), new_urn("claim")
    edges = [_edge(b, "depends_on", a) for _ in range(3)]

    assert compute_obligation_binding({a}, edges) == {b}


# ---------------------------------------------------------------------------
# Disconnected graph: unrelated components are NOT bound.
# ---------------------------------------------------------------------------


def test_disconnected_component_is_not_bound() -> None:
    a, b = new_urn("claim"), new_urn("claim")
    unrelated_x, unrelated_y = new_urn("claim"), new_urn("claim")
    edges = [
        _edge(b, "depends_on", a),
        _edge(unrelated_y, "depends_on", unrelated_x),
    ]

    bound = compute_obligation_binding({a}, edges)

    assert bound == {b}
    assert unrelated_x not in bound
    assert unrelated_y not in bound


# ---------------------------------------------------------------------------
# adapted_from propagates (the exact edge type TransferService.respond
# writes for decision="adapted" — task-packets/E6-T02.yaml derived_decisions
# (b): "no separate adaptation-lookup path is needed").
# ---------------------------------------------------------------------------


def test_adapted_from_edge_propagates_binding() -> None:
    transferred_object_id = new_urn("claim")
    adapted_object_id = new_urn("claim")
    edges = [_edge(adapted_object_id, "adapted_from", transferred_object_id)]

    bound = compute_obligation_binding({transferred_object_id}, edges)

    assert bound == {adapted_object_id}


# ---------------------------------------------------------------------------
# Only impact edge types propagate.
# ---------------------------------------------------------------------------


def test_non_impact_edge_types_do_not_bind() -> None:
    a, b = new_urn("claim"), new_urn("claim")
    edges = [_edge(b, "contradicts", a)]

    assert compute_obligation_binding({a}, edges) == set()


def test_subject_to_obligation_edges_themselves_do_not_bind() -> None:
    """subject_to_obligation is not an IMPACT_EDGE_TYPES member — a prior
    binding edge (from a DIFFERENT Obligation, say) must not itself be
    treated as a propagation path.
    """
    a, obligation_id = new_urn("claim"), new_urn("obligation")
    edges = [_edge(a, "subject_to_obligation", obligation_id)]

    assert compute_obligation_binding({a}, edges) == set()
