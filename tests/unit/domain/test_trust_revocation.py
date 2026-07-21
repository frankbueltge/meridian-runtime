"""Unit tests for mrr.domain.trust_revocation (task-packets/E5-T07b.yaml),
run entirely DB-free — no PostgreSQL.

Acceptance-test mapping (task-packets/E5-T07b.yaml):

- "unit (no Postgres) — trust_revoked_after_creation returns True for
  signed_at strictly before record.revoked_at, False for signed_at ==
  revoked_at (exact boundary), False for signed_at strictly after
  revoked_at, and False when record is None" ->
  ``test_true_when_signed_strictly_before_revoked_at``,
  ``test_false_at_the_exact_boundary``,
  ``test_false_when_signed_strictly_after_revoked_at``,
  ``test_false_when_record_is_none``.
- "unit (no Postgres) — constructing a RevocationRecord with a naive
  (non-timezone-aware) revoked_at, or an empty kid/practice_id, raises
  ValueError — mirrors PublicKeyDescriptor's own __post_init__ discipline"
  -> ``test_naive_revoked_at_is_rejected``, ``test_empty_kid_is_rejected``,
  ``test_empty_practice_id_is_rejected``.

The property-level generalization of the boundary behavior lives in
tests/property/test_trust_revocation_properties.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from mrr.domain.trust_revocation import (
    TRUST_REVOKED_AFTER_CREATION_ANNOTATION,
    RevocationRecord,
    trust_revoked_after_creation,
)

_REVOKED_AT = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)


def _record(*, revoked_at: datetime = _REVOKED_AT, reason: str | None = None) -> RevocationRecord:
    return RevocationRecord(
        kid="kid:" + "a" * 43,
        practice_id="urn:mrr:practice:" + "0" * 26,
        revoked_at=revoked_at,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# trust_revoked_after_creation
# ---------------------------------------------------------------------------


def test_true_when_signed_strictly_before_revoked_at() -> None:
    record = _record()
    signed_at = _REVOKED_AT - timedelta(seconds=1)
    assert trust_revoked_after_creation(signed_at, record) is True


def test_false_at_the_exact_boundary() -> None:
    record = _record()
    assert trust_revoked_after_creation(_REVOKED_AT, record) is False


def test_false_when_signed_strictly_after_revoked_at() -> None:
    record = _record()
    signed_at = _REVOKED_AT + timedelta(seconds=1)
    assert trust_revoked_after_creation(signed_at, record) is False


def test_false_when_record_is_none() -> None:
    assert trust_revoked_after_creation(_REVOKED_AT - timedelta(days=365), None) is False


def test_annotation_constant_is_the_exact_spec_string() -> None:
    assert TRUST_REVOKED_AFTER_CREATION_ANNOTATION == "trust_revoked_after_creation"


# ---------------------------------------------------------------------------
# RevocationRecord.__post_init__
# ---------------------------------------------------------------------------


def test_naive_revoked_at_is_rejected() -> None:
    with pytest.raises(ValueError, match="aware datetime"):
        RevocationRecord(
            kid="kid:" + "a" * 43,
            practice_id="urn:mrr:practice:" + "0" * 26,
            revoked_at=_REVOKED_AT.replace(tzinfo=None),
        )


def test_empty_kid_is_rejected() -> None:
    with pytest.raises(ValueError, match="kid must not be empty"):
        RevocationRecord(
            kid="",
            practice_id="urn:mrr:practice:" + "0" * 26,
            revoked_at=_REVOKED_AT,
        )


def test_empty_practice_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="practice_id must not be empty"):
        RevocationRecord(
            kid="kid:" + "a" * 43,
            practice_id="",
            revoked_at=_REVOKED_AT,
        )


def test_reason_defaults_to_none_and_is_optional() -> None:
    record = _record()
    assert record.reason is None
    reasoned = _record(reason="key compromise reported by the owning practice")
    assert reasoned.reason == "key compromise reported by the owning practice"


def test_record_is_frozen() -> None:
    record = _record()
    with pytest.raises(AttributeError):
        record.kid = "kid:" + "b" * 43  # type: ignore[misc]
