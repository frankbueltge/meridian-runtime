"""Unit tests for
``mrr.services.research_decision.service.ResearchDecisionService``
(task-packets/K1-T03.yaml) — entirely DB-free, no PostgreSQL, no
``sqlalchemy.Engine``: like ``SourceFamilyService`` (E3-T03), this service
never reads prior state, so the fake unit-of-work below is the same shape as
``tests/unit/services/source_family/test_service.py``'s own ``_FakeUnitOfWork``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from mrr.contracts import ResearchDecision
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.research_decision.service import ResearchDecisionService

_POLICY_VERSION = "policy-2026-07-01"


class _FakeUnitOfWork:
    def __init__(self) -> None:
        self.stored: list[StoredObject] = []
        self.events: list[DomainEvent] = []

    def __call__(
        self, obj: StoredObject, expected_current_revision: int | None, event: DomainEvent
    ) -> tuple[StoredObject, AppendedEvent]:
        assert expected_current_revision is None, (
            "ResearchDecisionService always writes a brand-new object at revision 1"
        )
        self.stored.append(obj)
        self.events.append(event)
        appended = AppendedEvent(
            event=event,
            sequence=len(self.events),
            content_hash=f"sha256:{'c' * 64}",
            prev_hash=None,
        )
        return obj, appended


def _service() -> tuple[ResearchDecisionService, _FakeUnitOfWork]:
    uow = _FakeUnitOfWork()
    return ResearchDecisionService(uow), uow


def _decision(*, revision: int = 1, **overrides: Any) -> ResearchDecision:
    data: dict[str, Any] = {
        "id": new_urn("research-decision"),
        "api_version": "mrr/v1alpha1",
        "kind": "ResearchDecision",
        "practice_id": new_urn("practice"),
        "revision": revision,
        "created_at": datetime.now(UTC),
        "created_by": new_urn("agent"),
        "content_hash": "sha256:" + "7" * 64,
        "decision_type": "stop_insufficient_evidence",
        "protocol_id": new_urn("method-protocol"),
        "applies_to_analysis": "instantiation-vs-reference-classification",
        "rationale": "2 included source(s), below the declared minimum of 5",
        "status": "issued",
    }
    data.update(overrides)
    return ResearchDecision.model_validate(data)


def test_create_persists_revision_one_and_one_event() -> None:
    service, uow = _service()
    decision = _decision()

    stored = service.create(
        decision,
        actor=new_urn("agent"),
        policy_version=_POLICY_VERSION,
        correlation_id=new_urn("research-run"),
    )

    assert stored.revision == 1
    assert stored.id == decision.id
    assert stored.body["decision_type"] == "stop_insufficient_evidence"
    assert len(uow.stored) == 1
    assert len(uow.events) == 1


def test_create_rejects_non_one_revision() -> None:
    service, _ = _service()
    decision = _decision(revision=2)

    with pytest.raises(ValueError, match="revision"):
        service.create(
            decision,
            actor=new_urn("agent"),
            policy_version=_POLICY_VERSION,
            correlation_id=new_urn("research-run"),
        )


def test_event_provenance_is_complete_and_causation_is_root() -> None:
    service, uow = _service()
    decision = _decision()
    actor = new_urn("agent")
    correlation_id = new_urn("research-run")

    stored = service.create(
        decision, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    event = uow.events[0]
    assert event.event_type == "research_decision.created"
    assert event.actor == actor
    assert event.policy_version == _POLICY_VERSION
    assert event.correlation_id == correlation_id
    assert event.causation_id is None
    assert event.object_id == stored.id
    assert event.object_revision == 1
    assert event.occurred_at.tzinfo is not None
    assert event.payload["decision_type"] == "stop_insufficient_evidence"


def test_service_exposes_no_transition_method() -> None:
    """RESEARCH_DECISION_LIFECYCLE has exactly one state and zero
    transitions (append-only) — this class's only public callable is
    ``create``.
    """
    public_methods = {
        name
        for name in dir(ResearchDecisionService)
        if not name.startswith("_") and callable(getattr(ResearchDecisionService, name))
    }
    assert public_methods == {"create"}


@pytest.mark.parametrize(
    "decision_type",
    [
        "continue",
        "revise",
        "narrow_scope",
        "kill_branch",
        "replicate",
        "escalate_human_review",
        "stop_insufficient_evidence",
    ],
)
def test_create_accepts_every_decision_type(decision_type: str) -> None:
    service, uow = _service()
    decision = _decision(decision_type=decision_type)

    stored = service.create(
        decision,
        actor=new_urn("agent"),
        policy_version=_POLICY_VERSION,
        correlation_id=new_urn("research-run"),
    )

    assert stored.body["decision_type"] == decision_type
    assert len(uow.stored) == 1
