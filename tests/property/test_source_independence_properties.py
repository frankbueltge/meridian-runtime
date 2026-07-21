"""Property tests for mrr.domain.source_independence (task-packets/
K1-T03.yaml). Mirrors tests/property/test_independence_properties.py's own
shape for the analogous acceptance-test wording: "property test confirms the
count is order-independent and never exceeds the number of 'verified' rows
supplied".
"""

from __future__ import annotations

from dataclasses import dataclass

from hypothesis import given
from hypothesis import strategies as st
from mrr.domain.source_independence import distinct_independent_source_family_count


@dataclass(frozen=True, slots=True)
class _Row:
    source_family_id: str | None
    source_record_id: str
    verification_status: str


#: A small, deliberately overlapping pool of values so hypothesis-generated
#: rows collide with each other often enough to exercise dedup, not just
#: produce uniformly-distinct fixtures on every draw.
_SOURCE_IDS = ("s1", "s2", "s3", "s4")
_FAMILY_IDS = (None, "family-a", "family-b")
_STATUSES = ("verified", "unverifiable", "pending")

_row_strategy = st.builds(
    _Row,
    source_family_id=st.sampled_from(_FAMILY_IDS),
    source_record_id=st.sampled_from(_SOURCE_IDS),
    verification_status=st.sampled_from(_STATUSES),
)

_rows_list_strategy = st.lists(_row_strategy, max_size=12)


@given(rows=_rows_list_strategy, data=st.data())
def test_count_is_deterministic_and_order_independent(
    rows: list[_Row], data: st.DataObject
) -> None:
    permuted = data.draw(st.permutations(rows))

    original = distinct_independent_source_family_count(rows)
    permuted_count = distinct_independent_source_family_count(permuted)
    repeat = distinct_independent_source_family_count(rows)

    assert original == permuted_count == repeat


@given(rows=_rows_list_strategy)
def test_count_never_exceeds_verified_row_count(rows: list[_Row]) -> None:
    verified_count = sum(1 for row in rows if row.verification_status == "verified")
    assert distinct_independent_source_family_count(rows) <= verified_count


@given(rows=_rows_list_strategy)
def test_count_is_never_negative(rows: list[_Row]) -> None:
    assert distinct_independent_source_family_count(rows) >= 0
