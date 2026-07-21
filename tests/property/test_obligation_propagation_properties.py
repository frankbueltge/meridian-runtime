"""Property tests for ``mrr.domain.obligation_propagation`` (task-packets/
E6-T02.yaml).

Acceptance-test mapping: "property test — for arbitrary graphs,
compute_obligation_binding's result is order-independent and idempotent
with respect to both seed ids and edge iteration order (delegates to, and
therefore inherits, compute_impact's own already-property-tested
guarantee — exercised again at this module's own entry point)" ->
every test below. ``test_matches_compute_impact_over_arbitrary_graphs`` is
the direct "delegates rather than diverges" property: for ANY generated
seed/edge combination, the wrapper's result is byte-for-byte the same set
``compute_impact`` itself would return.
"""

from __future__ import annotations

from datetime import UTC, datetime

from hypothesis import given
from hypothesis import strategies as st
from mrr.domain.correction_impact import compute_impact
from mrr.domain.obligation_propagation import compute_obligation_binding
from mrr.domain.repositories import EDGE_VOCABULARY, TypedEdge

#: Mirrors tests/property/test_correction_impact_properties.py's own pool —
#: small and deliberately overlapping so generated edges collide into
#: cycles, self-loops, and shared dependents often.
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
def test_matches_compute_impact_over_arbitrary_graphs(
    seed_ids: set[str], edges: list[TypedEdge]
) -> None:
    assert compute_obligation_binding(seed_ids, edges) == compute_impact(seed_ids, edges)


@given(seed_ids=_seed_ids_strategy, edges=_edges_list_strategy)
def test_terminates_and_is_cycle_safe_over_arbitrary_graphs(
    seed_ids: set[str], edges: list[TypedEdge]
) -> None:
    bound = compute_obligation_binding(seed_ids, edges)
    assert isinstance(bound, set)


@given(seed_ids=_seed_ids_strategy, edges=_edges_list_strategy)
def test_is_idempotent(seed_ids: set[str], edges: list[TypedEdge]) -> None:
    first = compute_obligation_binding(seed_ids, edges)
    second = compute_obligation_binding(seed_ids, edges)
    assert first == second


@given(seed_ids=_seed_ids_strategy, edges=_edges_list_strategy, data=st.data())
def test_is_order_independent_over_edge_permutations(
    seed_ids: set[str], edges: list[TypedEdge], data: st.DataObject
) -> None:
    permuted = data.draw(st.permutations(edges))

    original = compute_obligation_binding(seed_ids, edges)
    from_permuted = compute_obligation_binding(seed_ids, permuted)

    assert original == from_permuted


@given(seed_ids=_seed_ids_strategy, edges=_edges_list_strategy)
def test_result_is_always_a_subset_of_the_graph_nodes(
    seed_ids: set[str], edges: list[TypedEdge]
) -> None:
    nodes = set(seed_ids) | {edge.source_id for edge in edges} | {edge.target_id for edge in edges}
    bound = compute_obligation_binding(seed_ids, edges)
    assert bound <= nodes
