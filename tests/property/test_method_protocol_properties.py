"""Property test for ``mrr.contracts.method_protocol.MethodProtocol``'s
lock/amendment co-occurrence invariants (task-packets/K1-T01.yaml).

Acceptance-test mapping: "MethodProtocol — property test: for randomly
generated combinations of (status, locked_at is-None, locked_by is-None,
amendment is-None), construction succeeds if and only if the combination
matches one of the two legal co-occurrence patterns declared above; every
other combination raises pydantic.ValidationError, never a partial
success."

The two legal patterns, independently checked (MRR-MTH-007/008):

- `locked_at`/`locked_by`: both non-null when `status` is one of
  `locked`/`amended`/`executed`; both null when `status` is
  `draft`/`reviewed`. Any other combination (e.g. only one of the two
  non-null) is illegal regardless of status.
- `amendment`: non-null exactly when `status == "amended"`; null for every
  other status.

This is a pure contract-level test (no lifecycle transition involved) —
it belongs here, in tests/property/, rather than tests/contract/, because
it uses Hypothesis to generate arbitrary field-presence combinations,
mirroring this directory's own existing convention (e.g.
test_lifecycle_properties.py) rather than tests/contract/'s fixed-example
style.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from mrr.contracts.method_protocol import MethodProtocol, MethodProtocolStatus
from pydantic import ValidationError

_VALID_HASH = "sha256:" + "3" * 64
_STATUSES: tuple[MethodProtocolStatus, ...] = ("draft", "reviewed", "locked", "amended", "executed")


def _base_document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "id": "urn:mrr:method-protocol:01J00000000000000000000230",
        "api_version": "mrr/v1alpha1",
        "kind": "MethodProtocol",
        "practice_id": "urn:mrr:practice:01J00000000000000000000001",
        "revision": 1,
        "created_at": "2026-07-21T09:10:00Z",
        "created_by": "urn:mrr:person:01J00000000000000000000002",
        "content_hash": _VALID_HASH,
        "profile_id": "urn:mrr:method-profile:01J00000000000000000000110",
        "extraction_fields": ["claim_relevant_finding"],
        "inclusion_criteria": ["provenance-verified"],
        "exclusion_criteria": ["secondary commentary only"],
        "sensitivity_variations": [],
        "planned_analyses": ["instantiation-vs-reference-classification"],
        "kill_conditions": ["fewer than 5 included sources -> stop_insufficient_evidence"],
        "locked_at": None,
        "locked_by": None,
        "amendment": None,
        "status": "draft",
    }
    document.update(overrides)
    return document


_VALID_AMENDMENT = {
    "reason": "outcome-informed correction to extraction_fields",
    "actor": "urn:mrr:person:01J00000000000000000000002",
    "amended_at": "2026-07-21T12:00:00Z",
    "outcome_information_observed": True,
    "amended_locked_content_hash": _VALID_HASH,
}


def _expected_success(
    status: MethodProtocolStatus,
    locked_at_is_none: bool,
    locked_by_is_none: bool,
    amendment_is_none: bool,
) -> bool:
    lock_expected = status in ("locked", "amended", "executed")
    lock_present = not locked_at_is_none and not locked_by_is_none
    lock_absent = locked_at_is_none and locked_by_is_none
    lock_ok = (lock_expected and lock_present) or (not lock_expected and lock_absent)

    amendment_expected = status == "amended"
    amendment_ok = amendment_expected != amendment_is_none

    return lock_ok and amendment_ok


@given(
    status=st.sampled_from(_STATUSES),
    locked_at_is_none=st.booleans(),
    locked_by_is_none=st.booleans(),
    amendment_is_none=st.booleans(),
)
def test_construction_succeeds_iff_lock_and_amendment_cooccurrence_is_legal(
    status: MethodProtocolStatus,
    locked_at_is_none: bool,
    locked_by_is_none: bool,
    amendment_is_none: bool,
) -> None:
    document = _base_document(
        status=status,
        locked_at=None if locked_at_is_none else "2026-07-21T09:30:00Z",
        locked_by=None if locked_by_is_none else "urn:mrr:person:01J00000000000000000000002",
        amendment=None if amendment_is_none else _VALID_AMENDMENT,
    )

    expected = _expected_success(status, locked_at_is_none, locked_by_is_none, amendment_is_none)

    if expected:
        protocol = MethodProtocol.model_validate(document)
        assert protocol.status == status
    else:
        with pytest.raises(ValidationError):
            MethodProtocol.model_validate(document)
