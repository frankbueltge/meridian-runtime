"""Unit tests for ``mrr.domain.correction_impact`` (task-packets/E3-T06.yaml),
run entirely DB-free and framework-free — plain ``TypedEdge`` fixtures, no
repository, no engine.

Acceptance-test mapping (task-packets/E3-T06.yaml, unit tier):

- "line graph - correction on the root impacts the whole chain, once each"
  -> ``test_line_graph_impacts_the_whole_chain_once_each``.
- "branching graph - all branches impacted, each once" ->
  ``test_branching_graph_impacts_every_branch_once_each``.
- "cyclic graph - terminates and each object appears once" ->
  ``test_cyclic_graph_terminates_and_each_object_appears_once``,
  ``test_self_loop_terminates``.
- "duplicate edges - no double-processing" ->
  ``test_duplicate_edges_do_not_cause_double_processing``.
- "disconnected graph - unrelated components are NOT impacted" ->
  ``test_disconnected_component_is_not_impacted``.
- "only impact edge types propagate; contradicts/reviews edges do not" ->
  ``test_non_impact_edge_types_do_not_propagate``,
  ``test_every_fr091_category_edge_type_propagates``.
- the FR-091 mapping itself, checked against drift from
  ``mrr.domain.repositories.EDGE_VOCABULARY`` ->
  ``test_impact_edge_types_are_a_subset_of_the_edge_vocabulary``.
- "already-reviewed object - idempotent" is exercised at the pure-traversal
  level as "compute_impact is idempotent/order-independent" here
  (``test_compute_impact_is_idempotent``,
  ``test_compute_impact_is_order_independent_over_seeds_and_edges``); the
  claim-status-specific half of that acceptance test (no duplicate
  ``review_required`` revision) is
  ``tests/unit/services/correction/test_service.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from mrr.domain.correction_impact import (
    FR091_IMPACT_CATEGORY_EDGE_TYPES,
    IMPACT_EDGE_TYPES,
    compute_impact,
)
from mrr.domain.identity import new_urn
from mrr.domain.repositories import EDGE_VOCABULARY, TypedEdge


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
# The FR-091 mapping itself.
# ---------------------------------------------------------------------------


def test_impact_edge_types_are_a_subset_of_the_edge_vocabulary() -> None:
    """Guards against drift: every edge type this module treats as
    impact-carrying must be a genuine member of the section-3 vocabulary —
    nothing is invented to fill an FR-091 category.
    """
    assert IMPACT_EDGE_TYPES <= EDGE_VOCABULARY


def test_fr091_mapping_matches_task_packet_derivation() -> None:
    assert {
        "dependency": frozenset({"depends_on"}),
        "derivation": frozenset({"derived_from", "adapted_from"}),
        "citation": frozenset({"uses_source"}),
        "transfer": frozenset({"transferred_from"}),
        "publication": frozenset({"projected_into"}),
    } == FR091_IMPACT_CATEGORY_EDGE_TYPES
    assert (
        frozenset(
            {
                "depends_on",
                "derived_from",
                "adapted_from",
                "uses_source",
                "transferred_from",
                "projected_into",
            }
        )
        == IMPACT_EDGE_TYPES
    )


# ---------------------------------------------------------------------------
# Line graph: correction on the root impacts the whole chain, once each.
# ---------------------------------------------------------------------------


def test_line_graph_impacts_the_whole_chain_once_each() -> None:
    a, b, c, d = (new_urn("claim") for _ in range(4))
    # b depends on a, c depends on b, d depends on c: a chain built on the root a.
    edges = [
        _edge(b, "depends_on", a),
        _edge(c, "depends_on", b),
        _edge(d, "depends_on", c),
    ]

    impacted = compute_impact({a}, edges)

    assert impacted == {b, c, d}


# ---------------------------------------------------------------------------
# Branching graph: all branches impacted, each once.
# ---------------------------------------------------------------------------


def test_branching_graph_impacts_every_branch_once_each() -> None:
    a, b, c, d = (new_urn("claim") for _ in range(4))
    # b and c both depend directly on a; d depends on b only.
    edges = [
        _edge(b, "depends_on", a),
        _edge(c, "depends_on", a),
        _edge(d, "depends_on", b),
    ]

    impacted = compute_impact({a}, edges)

    assert impacted == {b, c, d}


# ---------------------------------------------------------------------------
# Cyclic graph: terminates, each object appears once.
# ---------------------------------------------------------------------------


def test_cyclic_graph_terminates_and_each_object_appears_once() -> None:
    a, b, c = (new_urn("claim") for _ in range(3))
    # a -> b -> c -> a: a genuine cycle through all three impact-typed edges.
    edges = [
        _edge(b, "depends_on", a),
        _edge(c, "depends_on", b),
        _edge(a, "depends_on", c),
    ]

    impacted = compute_impact({a}, edges)

    # The cycle loops back onto the seed itself, so 'a' is genuinely
    # downstream of itself here (reached via c -> a); every id appears
    # exactly once because the result is a set.
    assert impacted == {a, b, c}


def test_self_loop_terminates() -> None:
    a = new_urn("claim")
    edges = [_edge(a, "depends_on", a)]

    impacted = compute_impact({a}, edges)

    assert impacted == {a}


# ---------------------------------------------------------------------------
# Duplicate edges: no double-processing.
# ---------------------------------------------------------------------------


def test_duplicate_edges_do_not_cause_double_processing() -> None:
    a, b = new_urn("claim"), new_urn("claim")
    # Three distinct edge rows, same (source, type, target).
    edges = [_edge(b, "depends_on", a) for _ in range(3)]

    impacted = compute_impact({a}, edges)

    assert impacted == {b}


# ---------------------------------------------------------------------------
# Disconnected graph: unrelated components are NOT impacted.
# ---------------------------------------------------------------------------


def test_disconnected_component_is_not_impacted() -> None:
    a, b = new_urn("claim"), new_urn("claim")
    unrelated_x, unrelated_y = new_urn("claim"), new_urn("claim")
    edges = [
        _edge(b, "depends_on", a),
        _edge(unrelated_y, "depends_on", unrelated_x),
    ]

    impacted = compute_impact({a}, edges)

    assert impacted == {b}
    assert unrelated_x not in impacted
    assert unrelated_y not in impacted


# ---------------------------------------------------------------------------
# Only impact edge types propagate.
# ---------------------------------------------------------------------------


def test_non_impact_edge_types_do_not_propagate() -> None:
    a, b, c = (new_urn("claim") for _ in range(3))
    edges = [
        _edge(b, "contradicts", a),
        _edge(c, "reviews", a),
    ]

    impacted = compute_impact({a}, edges)

    assert impacted == set()


def test_every_fr091_category_edge_type_propagates() -> None:
    for edge_type in sorted(IMPACT_EDGE_TYPES):
        seed = new_urn("claim")
        dependent = new_urn("claim")
        edges = [_edge(dependent, edge_type, seed)]

        impacted = compute_impact({seed}, edges)

        assert impacted == {dependent}, f"edge type {edge_type!r} did not propagate impact"


def test_mixed_impact_and_non_impact_edges_only_impact_ones_propagate() -> None:
    a, b, c = (new_urn("claim") for _ in range(3))
    edges = [
        _edge(b, "depends_on", a),  # propagates
        _edge(c, "contradicts", a),  # does not propagate
    ]

    impacted = compute_impact({a}, edges)

    assert impacted == {b}


# ---------------------------------------------------------------------------
# Determinism, idempotency, order-independence (DB-free precursor to the
# property tests in tests/property/test_correction_impact_properties.py).
# ---------------------------------------------------------------------------


def test_compute_impact_is_idempotent() -> None:
    a, b, c = (new_urn("claim") for _ in range(3))
    edges = [_edge(b, "depends_on", a), _edge(c, "derived_from", b)]

    first = compute_impact({a}, edges)
    second = compute_impact({a}, edges)

    assert first == second == {b, c}


def test_compute_impact_is_order_independent_over_seeds_and_edges() -> None:
    a, b, c = (new_urn("claim") for _ in range(3))
    edges = [_edge(b, "depends_on", a), _edge(c, "derived_from", b)]

    forward = compute_impact({a}, edges)
    reversed_edges = compute_impact({a}, list(reversed(edges)))

    assert forward == reversed_edges == {b, c}


def test_seed_not_included_unless_reached_via_an_edge() -> None:
    a, b = new_urn("claim"), new_urn("claim")
    edges = [_edge(b, "depends_on", a)]

    impacted = compute_impact({a}, edges)

    assert a not in impacted
    assert impacted == {b}


def test_empty_seeds_yields_empty_result() -> None:
    a, b = new_urn("claim"), new_urn("claim")
    edges = [_edge(b, "depends_on", a)]

    assert compute_impact(set(), edges) == set()


def test_empty_edges_yields_empty_result() -> None:
    a = new_urn("claim")
    assert compute_impact({a}, []) == set()
