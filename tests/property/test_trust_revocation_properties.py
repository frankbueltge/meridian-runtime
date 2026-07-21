"""Property tests for mrr.domain.trust_revocation (task-packets/
E5-T07b.yaml).

Acceptance-test mapping: "property (no Postgres) — for arbitrary aware
datetimes signed_at and revoked_at, trust_revoked_after_creation(signed_at,
RevocationRecord(..., revoked_at=revoked_at)) equals exactly (signed_at <
revoked_at), and is False for every signed_at when record is None — a total,
deterministic function over every instant pair" ->
``test_matches_strict_less_than_for_arbitrary_instant_pairs``,
``test_always_false_when_record_is_none``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from hypothesis import given
from hypothesis import strategies as st
from mrr.domain.trust_revocation import RevocationRecord, trust_revoked_after_creation

#: A wide but finite range so this stays independent of any specific
#: envelope/bundle validity window, mirroring
#: tests/property/test_replay_retention_properties.py's own
#: ``_expires_at_strategy``.
_instant_strategy = st.datetimes(
    min_value=datetime(2000, 1, 1),
    max_value=datetime(2100, 1, 1),
    timezones=st.just(UTC),
)

_kid_strategy = st.text(min_size=1, max_size=64)
_practice_id_strategy = st.text(min_size=1, max_size=64)


@given(
    signed_at=_instant_strategy,
    revoked_at=_instant_strategy,
    kid=_kid_strategy,
    practice_id=_practice_id_strategy,
)
def test_matches_strict_less_than_for_arbitrary_instant_pairs(
    signed_at: datetime, revoked_at: datetime, kid: str, practice_id: str
) -> None:
    record = RevocationRecord(kid=kid, practice_id=practice_id, revoked_at=revoked_at)
    assert trust_revoked_after_creation(signed_at, record) == (signed_at < revoked_at)


@given(signed_at=_instant_strategy)
def test_always_false_when_record_is_none(signed_at: datetime) -> None:
    assert trust_revoked_after_creation(signed_at, None) is False
