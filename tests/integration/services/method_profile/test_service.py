"""Integration tests for
``mrr.services.method_profile.service.MethodProfileService``
(task-packets/K0-T01.yaml), run against a real PostgreSQL via the
``postgres_engine`` fixture in tests/integration/conftest.py — wired exactly
as production code would: ``PostgresObjectRepository``/``PostgresEventLog``
over the fixture's engine, with ``bind_unit_of_work`` closing over all
three. Mirrors
``tests/integration/services/capability_registry/test_service.py``'s own
wiring shape (the packet names that module's pattern as the template for
the registry service itself).

Acceptance-test mapping (task-packets/K0-T01.yaml, integration tier):

- "creating a MethodProfile (draft) persists it as revision 1 plus a
  method_profile.proposed event" ->
  ``test_propose_persists_one_revision_and_one_event_atomically``.
- "draft -> accepted succeeds via MethodProfileService and records a
  method_profile.accepted event, atomically with the new revision
  (MRR-MTH-019)" -> ``test_accept_persists_revision_2_and_accepted_event``.
- "accepted -> superseded succeeds and records a method_profile.superseded
  event" -> ``test_supersede_persists_revision_3_and_superseded_event``.
- "an illegal transition ... raises InvalidTransitionError and persists
  nothing" -> ``test_illegal_transition_persists_nothing``.
- "find_accepted_by_capability returns only currently-accepted profiles
  declaring the queried capability name, excluding a profile whose current
  status has since moved to superseded (integration test, real PostgreSQL,
  mirroring CapabilityRegistry.find_nodes_with_capability's own precedent)"
  -> ``test_find_accepted_by_capability_excludes_a_since_superseded_profile``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from mrr.contracts import MethodProfile
from mrr.domain.exceptions import InvalidTransitionError
from mrr.domain.identity import new_urn
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.persistence.tables import domain_events_table, objects_table
from mrr.services.method_profile.service import MethodProfileService, bind_unit_of_work
from sqlalchemy import Engine

_POLICY_VERSION = "policy-2026-07-01"


def _profile(*, id: str | None = None, **overrides: Any) -> MethodProfile:
    data: dict[str, Any] = {
        "id": id or new_urn("method-profile"),
        "api_version": "mrr/v1alpha1",
        "kind": "MethodProfile",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": datetime.now(UTC),
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "profile_key": "systematic_evidence_synthesis",
        "version": "1.0.0",
        "claim_types": ["observational", "interpretive"],
        "max_claim_ceiling": "associational_unadjusted",
        "protocol_form": "synthesis_protocol",
        "executor_task_family": ["mrr.method.systematic_evidence_synthesis/1"],
        "executor_steps": [
            {"name": "snapshot_loading", "kind": "deterministic"},
            {"name": "extraction", "kind": "model_assisted"},
        ],
        "inappropriate_uses": ["causal claims beyond associational_unadjusted"],
        "status": "draft",
    }
    data.update(overrides)
    return MethodProfile.model_validate(data)


def _service_for(
    engine: Engine,
) -> tuple[MethodProfileService, PostgresObjectRepository, PostgresEventLog]:
    object_repository = PostgresObjectRepository(engine)
    event_log = PostgresEventLog(engine)
    record = bind_unit_of_work(engine, object_repository, event_log)
    service = MethodProfileService(object_repository, event_log, record)
    return service, object_repository, event_log


def test_propose_persists_one_revision_and_one_event_atomically(
    postgres_engine: Engine,
) -> None:
    service, _, _ = _service_for(postgres_engine)
    profile = _profile()
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")

    stored = service.propose(
        profile, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    assert stored.revision == 1
    assert stored.id == profile.id

    with postgres_engine.connect() as conn:
        object_rows = conn.execute(
            sa.select(objects_table).where(objects_table.c.id == profile.id)
        ).fetchall()
        event_rows = conn.execute(
            sa.select(domain_events_table).where(domain_events_table.c.object_id == profile.id)
        ).fetchall()

    assert len(object_rows) == 1
    assert len(event_rows) == 1
    assert event_rows[0].event_type == "method_profile.proposed"
    assert event_rows[0].actor == actor
    assert event_rows[0].policy_version == _POLICY_VERSION
    assert event_rows[0].correlation_id == correlation_id
    assert event_rows[0].object_revision == 1
    assert event_rows[0].causation_id is None


def test_accept_persists_revision_2_and_accepted_event(postgres_engine: Engine) -> None:
    service, object_repository, _ = _service_for(postgres_engine)
    profile = _profile()
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")
    service.propose(
        profile, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    stored = service.accept(
        profile.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    assert stored.revision == 2
    assert stored.body["status"] == "accepted"
    rev1 = object_repository.get_revision(profile.id, 1)
    assert rev1.body["status"] == "draft"

    with postgres_engine.connect() as conn:
        event_rows = conn.execute(
            sa.select(domain_events_table)
            .where(domain_events_table.c.object_id == profile.id)
            .order_by(domain_events_table.c.sequence)
        ).fetchall()

    assert len(event_rows) == 2
    assert event_rows[1].event_type == "method_profile.accepted"
    assert event_rows[1].object_revision == 2
    assert event_rows[1].causation_id == event_rows[0].id


def test_supersede_persists_revision_3_and_superseded_event(postgres_engine: Engine) -> None:
    service, _, _ = _service_for(postgres_engine)
    profile = _profile()
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")
    service.propose(
        profile, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    service.accept(
        profile.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    stored = service.supersede(
        profile.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    assert stored.revision == 3
    assert stored.body["status"] == "superseded"

    with postgres_engine.connect() as conn:
        event_rows = conn.execute(
            sa.select(domain_events_table).where(
                domain_events_table.c.object_id == profile.id,
                domain_events_table.c.event_type == "method_profile.superseded",
            )
        ).fetchall()
    assert len(event_rows) == 1
    assert event_rows[0].object_revision == 3


def test_illegal_transition_persists_nothing(postgres_engine: Engine) -> None:
    service, _, _ = _service_for(postgres_engine)
    profile = _profile()
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")
    service.propose(
        profile, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    # draft -> superseded skips the required draft -> accepted step.
    with pytest.raises(InvalidTransitionError):
        service.supersede(
            profile.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
        )

    with postgres_engine.connect() as conn:
        object_rows = conn.execute(
            sa.select(objects_table).where(objects_table.c.id == profile.id)
        ).fetchall()
        event_rows = conn.execute(
            sa.select(domain_events_table).where(domain_events_table.c.object_id == profile.id)
        ).fetchall()

    # Only the original propose() revision/event survive — nothing new.
    assert len(object_rows) == 1
    assert len(event_rows) == 1
    assert event_rows[0].event_type == "method_profile.proposed"


def test_find_accepted_by_capability_excludes_a_since_superseded_profile(
    postgres_engine: Engine,
) -> None:
    service, _, _ = _service_for(postgres_engine)
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")

    accepted_profile = _profile(executor_task_family=["mrr.method.systematic_evidence_synthesis/1"])
    superseded_profile = _profile(
        executor_task_family=["mrr.method.systematic_evidence_synthesis/1"]
    )
    other_capability_profile = _profile(executor_task_family=["mrr.method.other/1"])

    for profile in (accepted_profile, superseded_profile, other_capability_profile):
        service.propose(
            profile, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
        )
        service.accept(
            profile.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
        )
    service.supersede(
        superseded_profile.id,
        actor=actor,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    matches = service.find_accepted_by_capability("mrr.method.systematic_evidence_synthesis/1")

    assert matches == [accepted_profile.id]
