"""Property tests for mrr.domain.independence (task-packets/E3-T05.yaml).

Acceptance-test mapping (task-packets/E3-T05.yaml): "property - independence
verdict and distinct-count are deterministic and order-independent over
arbitrary sets of profiles" ->
``test_is_independent_of_producer_is_deterministic``,
``test_distinct_independent_reviews_is_deterministic_and_order_independent``;
"property - adding a duplicate-profile verification never increases the
distinct independent count" ->
``test_adding_a_duplicate_profile_never_increases_the_distinct_count``. A
third property named in the task's own Tests section ("distinct count never
exceeds the number of inputs") -> ``test_distinct_independent_reviews_never_exceeds_input_count``.
"""

from __future__ import annotations

from hypothesis import assume, given
from hypothesis import strategies as st
from mrr.contracts.verification_result import IndependenceProfile
from mrr.domain.independence import distinct_independent_reviews, is_independent_of_producer

#: A small, deliberately overlapping pool of values per dimension so that
#: hypothesis-generated profiles collide with each other (and with the
#: producer) often enough to actually exercise dedup and the disqualification
#: rule, not just produce uniformly-distinct fixtures on every draw.
_VALUES = ("alpha", "beta", "gamma")

_profile_strategy = st.builds(
    IndependenceProfile,
    principal=st.sampled_from(_VALUES),
    model_family=st.sampled_from(_VALUES),
    prompt_family=st.sampled_from(_VALUES),
    retrieval_path=st.sampled_from(_VALUES),
    code_path=st.sampled_from(_VALUES),
    data_access_path=st.sampled_from(_VALUES),
)

_profiles_list_strategy = st.lists(_profile_strategy, max_size=12)


@given(producer=_profile_strategy, verifier=_profile_strategy)
def test_is_independent_of_producer_is_deterministic(
    producer: IndependenceProfile, verifier: IndependenceProfile
) -> None:
    first = is_independent_of_producer(verifier, producer)
    second = is_independent_of_producer(verifier, producer)
    assert first == second


@given(producer=_profile_strategy, verifiers=_profiles_list_strategy, data=st.data())
def test_distinct_independent_reviews_is_deterministic_and_order_independent(
    producer: IndependenceProfile, verifiers: list[IndependenceProfile], data: st.DataObject
) -> None:
    permuted = data.draw(st.permutations(verifiers))

    original_count = distinct_independent_reviews(producer, verifiers)
    permuted_count = distinct_independent_reviews(producer, permuted)
    repeat_count = distinct_independent_reviews(producer, verifiers)

    assert original_count == permuted_count == repeat_count


@given(producer=_profile_strategy, verifiers=_profiles_list_strategy)
def test_distinct_independent_reviews_never_exceeds_input_count(
    producer: IndependenceProfile, verifiers: list[IndependenceProfile]
) -> None:
    assert distinct_independent_reviews(producer, verifiers) <= len(verifiers)


@given(producer=_profile_strategy, verifiers=_profiles_list_strategy, data=st.data())
def test_adding_a_duplicate_profile_never_increases_the_distinct_count(
    producer: IndependenceProfile, verifiers: list[IndependenceProfile], data: st.DataObject
) -> None:
    assume(len(verifiers) >= 1)
    duplicate = data.draw(st.sampled_from(verifiers))

    baseline = distinct_independent_reviews(producer, verifiers)
    with_duplicate = distinct_independent_reviews(producer, [*verifiers, duplicate])

    assert with_duplicate == baseline
