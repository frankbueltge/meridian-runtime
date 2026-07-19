"""Property tests for ``mrr.domain.correction_impact`` (task-packets/
E3-T06.yaml).

Acceptance-test mapping: "property - traversal is order-independent and
idempotent over arbitrary graphs; result size never exceeds the node count"
-> ``test_compute_impact_is_order_independent_over_edge_permutations``,
``test_compute_impact_is_idempotent``,
``test_result_never_exceeds_the_node_count``,
``test_result_is_always_a_subset_of_the_graph_nodes``. Cycle-safety over
arbitrary (hypothesis-generated, possibly cyclic) graphs ->
``test_terminates_and_is_cycle_safe_over_arbitrary_graphs`` (the fact that
this test completes at all, across many generated examples, is itself the
termination assertion).
"""

from __future__ import annotations

from datetime import UTC, datetime

from hypothesis import given
from hypothesis import strategies as st
from mrr.domain.correction_impact import IMPACT_EDGE_TYPES, compute_impact
from mrr.domain.repositories import EDGE_VOCABULARY, TypedEdge

#: A small, deliberately overlapping pool of node ids so that
#: hypothesis-generated edges collide into cycles, self-loops, and shared
#: dependents often enough to actually exercise the traversal's cycle-safety
#: and dedup behavior, not just uniformly disconnected fixtures.
_NODE_IDS = tuple(f"urn:mrr:claim:{i:026d}" for i in range(6))

_ALL_EDGE_TYPES = sorted(EDGE_VOCABULARY)


def _make_edge(source_id: str, edge_type: str, target_id: str, suffix: int) -> TypedEdge:
    return TypedEdge(
        id=f"urn:mrr:edge:{suffix:026d}",
        source_id=source_id,
        target_id=target_id,
        edge_type=edge_type,
        created_at=datetime.now(UTC),
        created_by="urn:mrr:agent-role:00000000000000000000000000",
        scope=None,
        status="active",
        practice_id="urn:mrr:practice:00000000000000000000000000",
    )


_edge_strategy = st.builds(
    _make_edge,
    source_id=st.sampled_from(_NODE_IDS),
    edge_type=st.sampled_from(_ALL_EDGE_TYPES),
    target_id=st.sampled_from(_NODE_IDS),
    suffix=st.integers(min_value=0, max_value=10_000),
)

_edges_list_strategy = st.lists(_edge_strategy, max_size=25)
_seed_ids_strategy = st.sets(st.sampled_from(_NODE_IDS), max_size=4)


@given(seed_ids=_seed_ids_strategy, edges=_edges_list_strategy)
def test_terminates_and_is_cycle_safe_over_arbitrary_graphs(
    seed_ids: set[str], edges: list[TypedEdge]
) -> None:
    # The assertion is simply that this returns at all (hypothesis would
    # time out / hang on a genuine infinite loop) plus the basic sanity
    # invariant that the result is always a plain set.
    impacted = compute_impact(seed_ids, edges)
    assert isinstance(impacted, set)


@given(seed_ids=_seed_ids_strategy, edges=_edges_list_strategy)
def test_compute_impact_is_idempotent(seed_ids: set[str], edges: list[TypedEdge]) -> None:
    first = compute_impact(seed_ids, edges)
    second = compute_impact(seed_ids, edges)
    assert first == second


@given(seed_ids=_seed_ids_strategy, edges=_edges_list_strategy, data=st.data())
def test_compute_impact_is_order_independent_over_edge_permutations(
    seed_ids: set[str], edges: list[TypedEdge], data: st.DataObject
) -> None:
    permuted = data.draw(st.permutations(edges))

    original = compute_impact(seed_ids, edges)
    from_permuted = compute_impact(seed_ids, permuted)

    assert original == from_permuted


@given(seed_ids=_seed_ids_strategy, edges=_edges_list_strategy)
def test_result_is_always_a_subset_of_the_graph_nodes(
    seed_ids: set[str], edges: list[TypedEdge]
) -> None:
    nodes = set(seed_ids) | {edge.source_id for edge in edges} | {edge.target_id for edge in edges}
    impacted = compute_impact(seed_ids, edges)
    assert impacted <= nodes


@given(seed_ids=_seed_ids_strategy, edges=_edges_list_strategy)
def test_result_never_exceeds_the_node_count(seed_ids: set[str], edges: list[TypedEdge]) -> None:
    nodes = set(seed_ids) | {edge.source_id for edge in edges} | {edge.target_id for edge in edges}
    impacted = compute_impact(seed_ids, edges)
    assert len(impacted) <= len(nodes)


@given(seed_ids=_seed_ids_strategy, edges=_edges_list_strategy)
def test_only_impact_edge_types_ever_contribute(seed_ids: set[str], edges: list[TypedEdge]) -> None:
    """No id reachable ONLY via non-impact edge types (e.g. contradicts,
    reviews, supports) is ever included — removing every non-impact edge
    from the graph must never change the result.
    """
    impact_only_edges = [edge for edge in edges if edge.edge_type in IMPACT_EDGE_TYPES]

    with_all_edges = compute_impact(seed_ids, edges)
    with_impact_only_edges = compute_impact(seed_ids, impact_only_edges)

    assert with_all_edges == with_impact_only_edges
