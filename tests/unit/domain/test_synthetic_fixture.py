"""Unit tests for ``mrr.domain.synthetic_fixture`` (task-packets/K1-T02.yaml,
MRR-MTH-012), run entirely DB-free and framework-free — a plain
``str | None`` argument, no repository, no engine, no real stored object
(see that module's own docstring for the ADR-0010 dependency this
deliberately stays unit-tier-only for).

Acceptance-test mapping: "[re-pins method-synthetic-fixture-isolation.feature,
MRR-MTH-012, UNIT TIER ONLY per the ADR-0010 dependency]
assert_not_synthetic_fixture_evidence(source_classification=
'SYNTHETIC_TEST_FIXTURE') raises SyntheticFixtureNotEvidenceError (error_code
== 'SYNTHETIC_FIXTURE_NOT_EVIDENCE'); every other input, including None and
every one of the five EXISTING Classification values ..., passes" ->
``test_synthetic_test_fixture_classification_raises``,
``test_every_other_classification_and_none_pass``.
"""

from __future__ import annotations

import pytest
from mrr.contracts.common import Classification
from mrr.domain.exceptions import SyntheticFixtureNotEvidenceError
from mrr.domain.synthetic_fixture import (
    SYNTHETIC_TEST_FIXTURE_CLASSIFICATION,
    assert_not_synthetic_fixture_evidence,
)

_EXISTING_CLASSIFICATIONS: tuple[Classification, ...] = (
    "PUBLIC",
    "INTERNAL",
    "RESTRICTED",
    "SENSITIVE",
    "PARTICIPANT_IDENTIFIABLE",
)


def test_synthetic_test_fixture_classification_raises() -> None:
    with pytest.raises(SyntheticFixtureNotEvidenceError) as excinfo:
        assert_not_synthetic_fixture_evidence(
            source_classification=SYNTHETIC_TEST_FIXTURE_CLASSIFICATION
        )
    assert excinfo.value.error_code == "SYNTHETIC_FIXTURE_NOT_EVIDENCE"
    assert excinfo.value.source_classification == "SYNTHETIC_TEST_FIXTURE"


def test_none_passes() -> None:
    assert_not_synthetic_fixture_evidence(source_classification=None)  # must not raise


@pytest.mark.parametrize("classification", _EXISTING_CLASSIFICATIONS)
def test_every_existing_classification_value_passes(classification: str) -> None:
    assert_not_synthetic_fixture_evidence(source_classification=classification)  # must not raise


def test_an_arbitrary_unrelated_string_also_passes() -> None:
    """Only the exact literal 'SYNTHETIC_TEST_FIXTURE' triggers the gate —
    this is not a fuzzy or case-insensitive match.
    """
    assert_not_synthetic_fixture_evidence(source_classification="synthetic_test_fixture")
    assert_not_synthetic_fixture_evidence(source_classification="something-else-entirely")
