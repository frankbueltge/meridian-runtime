"""Unit tests for mrr.provenance.events (E1-T06): the DomainEvent field
validation surface, and the purity/chaining properties of the pure
compute_event_hash function — covered here without any database
(task-packets/E1-T06.yaml: "Expose a pure function for this so unit tests
cover it without a DB").
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from mrr.domain.identity import new_urn
from mrr.provenance.events import DomainEvent, compute_event_hash, event_to_hashable_dict


def _domain_event(**overrides: Any) -> DomainEvent:
    defaults: dict[str, Any] = {
        "id": new_urn("domain-event"),
        "event_type": "claim.status_changed",
        "occurred_at": datetime.now(UTC),
        "actor": new_urn("agent-role"),
        "policy_version": "policy-2026-07-01",
        "causation_id": None,
        "correlation_id": new_urn("research-run"),
        "object_id": new_urn("claim"),
        "object_revision": 1,
        "payload": {"status": "under_review"},
    }
    defaults.update(overrides)
    return DomainEvent(**defaults)


# ---------------------------------------------------------------------------
# Construction — valid shapes.
# ---------------------------------------------------------------------------


def test_root_event_allows_none_causation_id() -> None:
    event = _domain_event(causation_id=None)
    assert event.causation_id is None


def test_non_root_event_carries_a_causation_id() -> None:
    causation_id = new_urn("domain-event")
    event = _domain_event(causation_id=causation_id)
    assert event.causation_id == causation_id


def test_domain_event_is_frozen() -> None:
    event = _domain_event()
    with pytest.raises(AttributeError):
        event.event_type = "changed"  # type: ignore[misc]


def test_domain_event_defensively_copies_payload() -> None:
    payload = {"status": "draft"}
    event = _domain_event(payload=payload)

    payload["status"] = "mutated-after-construction"

    assert event.payload == {"status": "draft"}


# ---------------------------------------------------------------------------
# Construction — field validation surface.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field_name",
    ["id", "actor", "correlation_id", "object_id"],
)
def test_invalid_urn_fields_raise_value_error(field_name: str) -> None:
    with pytest.raises(ValueError, match=field_name):
        _domain_event(**{field_name: "not-a-urn"})


def test_invalid_causation_id_raises_value_error_when_not_none() -> None:
    with pytest.raises(ValueError, match="causation_id"):
        _domain_event(causation_id="not-a-urn")


@pytest.mark.parametrize("event_type", ["", "   "])
def test_empty_event_type_raises_value_error(event_type: str) -> None:
    with pytest.raises(ValueError, match="event_type"):
        _domain_event(event_type=event_type)


@pytest.mark.parametrize("policy_version", ["", "   "])
def test_empty_policy_version_raises_value_error(policy_version: str) -> None:
    with pytest.raises(ValueError, match="policy_version"):
        _domain_event(policy_version=policy_version)


def test_naive_occurred_at_raises_value_error() -> None:
    naive = datetime(2026, 7, 18, 12, 0, 0)
    assert naive.tzinfo is None
    with pytest.raises(ValueError, match="occurred_at"):
        _domain_event(occurred_at=naive)


def test_aware_occurred_at_in_a_non_utc_timezone_is_accepted() -> None:
    plus_five = timezone(timedelta(hours=5))
    aware = datetime.now(UTC).astimezone(plus_five)
    event = _domain_event(occurred_at=aware)
    assert event.occurred_at.tzinfo is not None


@pytest.mark.parametrize("object_revision", [0, -1, -100])
def test_object_revision_below_one_raises_value_error(object_revision: int) -> None:
    with pytest.raises(ValueError, match="object_revision"):
        _domain_event(object_revision=object_revision)


def test_object_revision_of_exactly_one_is_accepted() -> None:
    event = _domain_event(object_revision=1)
    assert event.object_revision == 1


def test_non_dict_payload_raises_value_error() -> None:
    with pytest.raises(ValueError, match="payload"):
        _domain_event(payload=["not", "a", "dict"])


# ---------------------------------------------------------------------------
# event_to_hashable_dict / compute_event_hash — purity and chaining.
# ---------------------------------------------------------------------------


def test_event_to_hashable_dict_includes_prev_hash() -> None:
    event = _domain_event()
    hashable = event_to_hashable_dict(event, "sha256:" + "a" * 64)
    assert hashable["prev_hash"] == "sha256:" + "a" * 64


def test_event_to_hashable_dict_serializes_occurred_at_to_iso_string() -> None:
    event = _domain_event()
    hashable = event_to_hashable_dict(event, None)
    assert hashable["occurred_at"] == event.occurred_at.isoformat()


def test_compute_event_hash_is_deterministic_for_identical_input() -> None:
    event = _domain_event()
    assert compute_event_hash(event, None) == compute_event_hash(event, None)


def test_compute_event_hash_matches_sha256_format() -> None:
    event = _domain_event()
    digest = compute_event_hash(event, None)
    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64


def test_compute_event_hash_changes_with_prev_hash() -> None:
    event = _domain_event()
    hash_a = compute_event_hash(event, None)
    hash_b = compute_event_hash(event, "sha256:" + "a" * 64)
    hash_c = compute_event_hash(event, "sha256:" + "b" * 64)
    assert len({hash_a, hash_b, hash_c}) == 3


def test_compute_event_hash_changes_with_payload() -> None:
    event_a = _domain_event(payload={"status": "draft"})
    # Keep every other field identical except the payload.
    event_b = DomainEvent(
        id=event_a.id,
        event_type=event_a.event_type,
        occurred_at=event_a.occurred_at,
        actor=event_a.actor,
        policy_version=event_a.policy_version,
        causation_id=event_a.causation_id,
        correlation_id=event_a.correlation_id,
        object_id=event_a.object_id,
        object_revision=event_a.object_revision,
        payload={"status": "sealed"},
    )

    assert compute_event_hash(event_a, None) != compute_event_hash(event_b, None)
