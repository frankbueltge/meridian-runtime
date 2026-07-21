"""Unit tests for ``mrr.services.obligation.service.ObligationService``
(task-packets/E6-T02.yaml), run entirely DB-free against in-memory fakes of
``mrr.domain.repositories.ObjectRepository``/``EdgeRepository`` and the
event-log read surface — no PostgreSQL, no ``sqlalchemy.Engine``.

E6-T01's ``TransferService`` is deliberately NOT constructed here: a fixture
``TransferContract`` is seeded directly against the fake ``ObjectRepository``,
and its ``transfer.responded`` event is appended directly to the fake event
log — exactly what task-packets/E6-T02.yaml's own acceptance_tests describe
("built directly against the generic objects/edges tables ... standing in
for E6-T01's own service"), since ``ObligationService`` reads a
TransferContract's stored body and event log directly and never calls
``TransferService`` itself (forbidden_changes).

Acceptance-test mapping (task-packets/E6-T02.yaml):

- "happy path, single stub" -> ``test_materialize_single_stub_happy_path``.
- "multiple stubs" ->
  ``test_materialize_multiple_stubs_each_bound_to_the_same_transferred_objects``.
- "caveats materialize a retain_caveat Obligation" ->
  ``test_materialize_caveats_produce_one_retain_caveat_obligation``,
  ``test_materialize_empty_caveats_produce_no_retain_caveat_obligation``.
- "no materialization on non-accepting decisions" ->
  ``test_materialize_rejects_non_accepting_decision``,
  ``test_materialize_rejects_a_transfer_never_responded_to``.
- ``ObligationSourceTransferNotFoundError`` ->
  ``test_materialize_missing_transfer_raises_source_not_found``.
- materialization is at-most-once per transfer (reviewer-driven amendment) ->
  ``test_materialize_is_at_most_once_per_transfer``.
- "adaptation is discovered by propagation, not by materialization" ->
  ``test_adaptation_is_discovered_by_propagation_not_materialization``.
- "line graph, branching graph, cyclic graph, disconnected graph" ->
  ``test_propagate_line_graph_binds_the_whole_chain``,
  ``test_propagate_branching_graph_binds_every_branch``,
  ``test_propagate_cyclic_graph_terminates``,
  ``test_propagate_disconnected_component_is_not_bound``.
- "duplicate edges" -> ``test_propagate_duplicate_edges_do_not_cause_a_duplicate_binding_edge``.
- "idempotency" -> ``test_propagate_is_idempotent_on_repeated_calls``,
  ``test_propagate_on_a_fresh_obligation_with_no_downstream_is_a_no_op``.
- "re-propagation after new derivative work" ->
  ``test_propagate_discovers_a_new_derivative_added_after_a_prior_call``.
- "resolve/defer happy paths" -> ``test_resolve_happy_path``,
  ``test_defer_happy_path``.
- "illegal-transition matrix" -> ``test_resolve_or_defer_on_a_non_open_obligation_fails_closed``.
- "never-silently-dropped" ->
  ``test_resolve_never_touches_prior_subject_to_obligation_edges_or_revisions``.
- ``resolve`` rejects empty ``resolution_evidence`` (reviewer-driven
  amendment) -> ``test_resolve_rejects_empty_resolution_evidence``.
- property test is ``tests/property/test_obligation_propagation_properties.py``
  (this module exercises ``ObligationService.propagate``, the SERVICE'S own
  query-driven BFS, not the pure ``compute_obligation_binding`` directly).
- contract tier is ``tests/contract/test_examples.py`` (parametrized over
  ``scripts.check_contracts.ENTITY_MODELS``, which now includes
  ``"obligation"``).
- event provenance -> ``test_events_carry_complete_provenance_across_the_causal_chain``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from mrr.contracts import TransferContract
from mrr.domain.exceptions import (
    InvalidTransitionError,
    MissingResolutionEvidenceError,
    ObjectNotFoundError,
    ObligationNotFoundError,
    ObligationsAlreadyMaterializedError,
    ObligationSourceTransferNotFoundError,
    RevisionConflictError,
    TransferNotAcceptedError,
    UnknownEdgeTypeError,
)
from mrr.domain.identity import new_urn
from mrr.domain.repositories import EDGE_VOCABULARY, StoredObject, TypedEdge
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.obligation.service import ObligationService

# ---------------------------------------------------------------------------
# In-memory fakes — identical in spirit to
# tests/unit/services/correction/test_service.py's own fakes.
# ---------------------------------------------------------------------------


class FakeObjectRepository:
    def __init__(self) -> None:
        self._revisions: dict[str, list[StoredObject]] = {}

    def insert_revision(
        self, obj: StoredObject, expected_current_revision: int | None
    ) -> StoredObject:
        current = self._revisions.get(obj.id, [])
        current_max = current[-1].revision if current else None
        if current_max != expected_current_revision:
            raise RevisionConflictError(obj.id, expected_current_revision, current_max)
        expected_new_revision = (
            1 if expected_current_revision is None else expected_current_revision + 1
        )
        if obj.revision != expected_new_revision:
            raise ValueError(
                f"obj.revision ({obj.revision!r}) does not match the revision implied by "
                f"expected_current_revision ({expected_current_revision!r}): expected "
                f"{expected_new_revision!r}"
            )
        self._revisions.setdefault(obj.id, []).append(obj)
        return obj

    def get_latest(self, id: str) -> StoredObject:
        revisions = self._revisions.get(id)
        if not revisions:
            raise ObjectNotFoundError(id)
        return revisions[-1]

    def get_revision(self, id: str, revision: int) -> StoredObject:
        for rev in self._revisions.get(id, []):
            if rev.revision == revision:
                return rev
        raise ObjectNotFoundError(id, revision)

    def list_revisions(self, id: str) -> list[StoredObject]:
        return list(self._revisions.get(id, []))


class FakeEdgeRepository:
    def __init__(self) -> None:
        self._edges: list[TypedEdge] = []

    def add_edge(self, edge: TypedEdge) -> TypedEdge:
        if edge.edge_type not in EDGE_VOCABULARY:
            raise UnknownEdgeTypeError(edge.edge_type)
        self._edges.append(edge)
        return edge

    def edges_from(self, id: str, edge_type: str | None = None) -> list[TypedEdge]:
        return [
            e
            for e in self._edges
            if e.source_id == id and (edge_type is None or e.edge_type == edge_type)
        ]

    def edges_to(self, id: str, edge_type: str | None = None) -> list[TypedEdge]:
        return [
            e
            for e in self._edges
            if e.target_id == id and (edge_type is None or e.edge_type == edge_type)
        ]


class FakeEventLog:
    def __init__(self) -> None:
        self.appended: list[AppendedEvent] = []

    def append_for_test(self, event: DomainEvent) -> AppendedEvent:
        appended = AppendedEvent(
            event=event,
            sequence=len(self.appended) + 1,
            content_hash=f"sha256:{'d' * 64}",
            prev_hash=self.appended[-1].content_hash if self.appended else None,
        )
        self.appended.append(appended)
        return appended

    def read_all(self) -> list[AppendedEvent]:
        return list(self.appended)


def _fake_record(object_repository: FakeObjectRepository, event_log: FakeEventLog) -> Any:
    def _record(
        obj: StoredObject,
        expected_current_revision: int | None,
        event: DomainEvent,
    ) -> tuple[StoredObject, AppendedEvent]:
        stored = object_repository.insert_revision(obj, expected_current_revision)
        appended = event_log.append_for_test(event)
        return stored, appended

    return _record


def _fake_record_revision_with_edges(
    object_repository: FakeObjectRepository,
    edge_repository: FakeEdgeRepository,
    event_log: FakeEventLog,
) -> Any:
    def _record(
        obj: StoredObject,
        expected_current_revision: int | None,
        edges: list[TypedEdge],
        event: DomainEvent,
    ) -> tuple[StoredObject, list[TypedEdge], AppendedEvent]:
        stored = object_repository.insert_revision(obj, expected_current_revision)
        stored_edges = [edge_repository.add_edge(edge) for edge in edges]
        appended = event_log.append_for_test(event)
        return stored, stored_edges, appended

    return _record


def _harness() -> tuple[ObligationService, FakeObjectRepository, FakeEdgeRepository, FakeEventLog]:
    object_repository = FakeObjectRepository()
    edge_repository = FakeEdgeRepository()
    event_log = FakeEventLog()
    service = ObligationService(
        object_repository,
        edge_repository,
        event_log,
        _fake_record(object_repository, event_log),
        _fake_record_revision_with_edges(object_repository, edge_repository, event_log),
    )
    return service, object_repository, edge_repository, event_log


_ACTOR = new_urn("agent-role")
_POLICY_VERSION = "policy-2026-07-01"


def _correlation_id() -> str:
    return new_urn("research-run")


# ---------------------------------------------------------------------------
# Fixture builders.
# ---------------------------------------------------------------------------


def _transfer_contract(
    *,
    sender_practice_id: str | None = None,
    receiver_practice_id: str | None = None,
    transferred_object_ids: list[str] | None = None,
    obligations: list[dict[str, Any]] | None = None,
    caveats: list[str] | None = None,
    **overrides: Any,
) -> TransferContract:
    now = datetime.now(UTC)
    sender_practice_id = sender_practice_id or new_urn("practice")
    receiver_practice_id = receiver_practice_id or new_urn("practice")
    transferred_object_ids = transferred_object_ids or [new_urn("claim")]
    data: dict[str, Any] = {
        "id": new_urn("transfer-contract"),
        "api_version": "mrr/v1alpha1",
        "kind": "TransferContract",
        "practice_id": sender_practice_id,
        "revision": 1,
        "created_at": now,
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "sender_practice_id": sender_practice_id,
        "receiver_practice_id": receiver_practice_id,
        "transferred_objects": [
            {"id": object_id, "content_hash": "sha256:" + "b" * 64}
            for object_id in transferred_object_ids
        ],
        "purpose": "Fixture transfer for ObligationService tests.",
        "permitted_uses": ["replication_analysis"],
        "disclosure_rules": {"max_disclosure": "INTERNAL"},
        "attribution_rules": {"cite_as": "Fixture Practice"},
        "caveats": caveats if caveats is not None else [],
        "correction_subscription": True,
        "obligations": (
            obligations if obligations is not None else [{"kind": "preserve_attribution"}]
        ),
        "nonce": "n" * 16,
        "expires_at": now + timedelta(days=7),
        "signature": {
            "signer_practice_id": sender_practice_id,
            "key_id": "key-2026-01",
            "algorithm": "Ed25519",
            "signed_at": now,
            "value": "0" * 44,
        },
        "status": "created",
    }
    data.update(overrides)
    return TransferContract.model_validate(data)


def _stored_object_from_model(model: Any) -> StoredObject:
    body: dict[str, Any] = json.loads(model.model_dump_json(exclude_none=True))
    return StoredObject(
        id=model.id,
        api_version=model.api_version,
        kind=model.kind,
        practice_id=model.practice_id,
        revision=model.revision,
        created_at=model.created_at,
        created_by=model.created_by,
        content_hash=model.content_hash,
        supersedes=model.supersedes,
        labels=model.labels,
        body=body,
    )


def _seed(object_repository: FakeObjectRepository, model: Any) -> StoredObject:
    return object_repository.insert_revision(
        _stored_object_from_model(model), expected_current_revision=None
    )


def _seed_local_object(object_repository: FakeObjectRepository, *, id: str) -> StoredObject:
    obj = StoredObject(
        id=id,
        api_version="mrr/v1alpha1",
        kind="Claim",
        practice_id=new_urn("practice"),
        revision=1,
        created_at=datetime.now(UTC),
        created_by=_ACTOR,
        content_hash="sha256:" + "c" * 64,
        supersedes=None,
        labels=None,
        body={"status": "draft"},
    )
    object_repository.insert_revision(obj, expected_current_revision=None)
    return obj


def _respond(event_log: FakeEventLog, transfer_id: str, decision: str) -> AppendedEvent:
    event = DomainEvent(
        id=new_urn("domain-event"),
        event_type="transfer.responded",
        occurred_at=datetime.now(UTC),
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        causation_id=None,
        correlation_id=_correlation_id(),
        object_id=transfer_id,
        object_revision=1,
        payload={"from_status": "offered", "to_status": decision, "decision": decision},
    )
    return event_log.append_for_test(event)


def _offer(event_log: FakeEventLog, transfer_id: str) -> AppendedEvent:
    event = DomainEvent(
        id=new_urn("domain-event"),
        event_type="transfer.offered",
        occurred_at=datetime.now(UTC),
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        causation_id=None,
        correlation_id=_correlation_id(),
        object_id=transfer_id,
        object_revision=1,
        payload={"from_status": "created", "to_status": "offered"},
    )
    return event_log.append_for_test(event)


def _seed_edge(
    edge_repository: FakeEdgeRepository, *, source_id: str, edge_type: str, target_id: str
) -> TypedEdge:
    edge = TypedEdge(
        id=new_urn("edge"),
        source_id=source_id,
        target_id=target_id,
        edge_type=edge_type,
        created_at=datetime.now(UTC),
        created_by=_ACTOR,
        scope=None,
        status="active",
        practice_id=new_urn("practice"),
    )
    return edge_repository.add_edge(edge)


def _materialize(
    service: ObligationService, transfer_id: str, *, correlation_id: str | None = None
) -> list[StoredObject]:
    return service.materialize_from_transfer(
        transfer_id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id or _correlation_id(),
    )


# ---------------------------------------------------------------------------
# materialize_from_transfer(): happy paths.
# ---------------------------------------------------------------------------


def test_materialize_single_stub_happy_path() -> None:
    service, object_repository, edge_repository, event_log = _harness()
    transferred_object_id = new_urn("claim")
    contract = _transfer_contract(
        transferred_object_ids=[transferred_object_id],
        obligations=[{"kind": "retain_caveat"}],
    )
    _seed(object_repository, contract)
    _respond(event_log, contract.id, "accepted")

    [obligation] = _materialize(service, contract.id)

    assert obligation.revision == 1
    assert obligation.body["status"] == "open"
    assert obligation.body["bound_objects"] == [transferred_object_id]
    assert obligation.body["propagated_objects"] == []
    assert obligation.body["obligation_kind"] == "retain_caveat"
    assert obligation.body["responsible_practice_id"] == contract.receiver_practice_id
    assert obligation.practice_id == contract.receiver_practice_id
    assert obligation.body["source_transfer_id"] == contract.id
    assert "caveat_text" not in obligation.body

    bound_edges = edge_repository.edges_to(obligation.id, "subject_to_obligation")
    assert len(bound_edges) == 1
    assert bound_edges[0].source_id == transferred_object_id
    assert bound_edges[0].target_id == obligation.id

    events = [e.event for e in event_log.read_all() if e.event.object_id == obligation.id]
    assert len(events) == 1
    assert events[0].event_type == "obligation.created"
    assert events[0].causation_id is None


def test_materialize_trigger_names_the_responded_event() -> None:
    service, object_repository, _edge_repository, event_log = _harness()
    contract = _transfer_contract()
    _seed(object_repository, contract)
    responded = _respond(event_log, contract.id, "accepted")

    [obligation] = _materialize(service, contract.id)

    assert obligation.body["trigger"] == f"transfer.responded:{responded.event.id}"


def test_materialize_multiple_stubs_each_bound_to_the_same_transferred_objects() -> None:
    service, object_repository, edge_repository, event_log = _harness()
    first_id, second_id = new_urn("claim"), new_urn("claim")
    contract = _transfer_contract(
        transferred_object_ids=[first_id, second_id],
        obligations=[
            {"kind": "preserve_attribution"},
            {"kind": "review_correction", "deadline": (datetime.now(UTC) + timedelta(days=30))},
        ],
    )
    _seed(object_repository, contract)
    _respond(event_log, contract.id, "accepted")

    stored = _materialize(service, contract.id)

    assert len(stored) == 2
    assert {obj.body["obligation_kind"] for obj in stored} == {
        "preserve_attribution",
        "review_correction",
    }
    assert len({obj.id for obj in stored}) == 2  # distinct Obligation objects
    for obligation in stored:
        assert set(obligation.body["bound_objects"]) == {first_id, second_id}
        bound_sources = {
            e.source_id for e in edge_repository.edges_to(obligation.id, "subject_to_obligation")
        }
        assert bound_sources == {first_id, second_id}


def test_materialize_caveats_produce_one_retain_caveat_obligation() -> None:
    service, object_repository, _edge_repository, event_log = _harness()
    contract = _transfer_contract(
        obligations=[],
        caveats=["Sample size is small; treat estimates as provisional."],
    )
    _seed(object_repository, contract)
    _respond(event_log, contract.id, "accepted")

    stored = _materialize(service, contract.id)

    assert len(stored) == 1
    [obligation] = stored
    assert obligation.body["obligation_kind"] == "retain_caveat"
    assert obligation.body["caveat_text"] == [
        "Sample size is small; treat estimates as provisional."
    ]


def test_materialize_empty_caveats_produce_no_retain_caveat_obligation() -> None:
    service, object_repository, _edge_repository, event_log = _harness()
    contract = _transfer_contract(obligations=[], caveats=[])
    _seed(object_repository, contract)
    _respond(event_log, contract.id, "accepted")

    stored = _materialize(service, contract.id)

    assert stored == []


def test_materialize_stub_and_caveats_both_produce_independent_obligations() -> None:
    """One retain_caveat stub AND a non-empty caveats field on the SAME
    transfer produce TWO separate Obligation objects — the two mechanisms
    are independent (task-packets/E6-T02.yaml derived_decisions (c)).
    """
    service, object_repository, _edge_repository, event_log = _harness()
    contract = _transfer_contract(
        obligations=[{"kind": "retain_caveat"}],
        caveats=["Treat with caution."],
    )
    _seed(object_repository, contract)
    _respond(event_log, contract.id, "accepted")

    stored = _materialize(service, contract.id)

    assert len(stored) == 2
    retain_caveat_obligations = [o for o in stored if o.body["obligation_kind"] == "retain_caveat"]
    assert len(retain_caveat_obligations) == 2
    with_text = [o for o in retain_caveat_obligations if "caveat_text" in o.body]
    without_text = [o for o in retain_caveat_obligations if "caveat_text" not in o.body]
    assert len(with_text) == 1
    assert len(without_text) == 1
    assert with_text[0].body["caveat_text"] == ["Treat with caution."]


def test_materialize_adapted_decision_is_also_accepted() -> None:
    service, object_repository, _edge_repository, event_log = _harness()
    contract = _transfer_contract()
    _seed(object_repository, contract)
    _respond(event_log, contract.id, "adapted")

    stored = _materialize(service, contract.id)

    assert len(stored) == 1


# ---------------------------------------------------------------------------
# materialize_from_transfer(): fail-closed gating (MRR-FR-083).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("decision", ["rejected", "deferred", "unresolved"])
def test_materialize_rejects_non_accepting_decision(decision: str) -> None:
    service, object_repository, edge_repository, event_log = _harness()
    contract = _transfer_contract()
    _seed(object_repository, contract)
    _respond(event_log, contract.id, decision)

    with pytest.raises(TransferNotAcceptedError) as excinfo:
        _materialize(service, contract.id)

    assert excinfo.value.transfer_id == contract.id
    assert excinfo.value.decision == decision
    # Only the transfer itself is in the object repository — no Obligation
    # was persisted.
    assert object_repository.list_revisions(contract.id) != []
    assert all(
        appended.event.event_type != "obligation.created" for appended in event_log.read_all()
    )
    assert edge_repository.edges_from("anything") == []  # no edges of any kind were written


def test_materialize_rejects_a_transfer_never_responded_to() -> None:
    service, object_repository, _edge_repository, event_log = _harness()
    contract = _transfer_contract()
    _seed(object_repository, contract)
    _offer(event_log, contract.id)
    # Never responded — still "offered".

    with pytest.raises(TransferNotAcceptedError) as excinfo:
        _materialize(service, contract.id)

    assert excinfo.value.transfer_id == contract.id
    assert excinfo.value.decision is None


def test_materialize_missing_transfer_raises_source_not_found() -> None:
    service, _object_repository, _edge_repository, _event_log = _harness()
    missing_id = new_urn("transfer-contract")

    with pytest.raises(ObligationSourceTransferNotFoundError) as excinfo:
        _materialize(service, missing_id)

    assert excinfo.value.transfer_id == missing_id


# ---------------------------------------------------------------------------
# materialize_from_transfer(): at-most-once per transfer.
# ---------------------------------------------------------------------------


def test_materialize_is_at_most_once_per_transfer() -> None:
    """A second materialize_from_transfer call for the same transfer must
    not mint a second, independent Obligation set — it fails closed and
    persists nothing, leaving exactly the first call's objects/edges/events
    in place.
    """
    service, object_repository, edge_repository, event_log = _harness()
    transferred_object_id = new_urn("claim")
    contract = _transfer_contract(
        transferred_object_ids=[transferred_object_id],
        obligations=[{"kind": "preserve_attribution"}, {"kind": "review_correction"}],
    )
    _seed(object_repository, contract)
    _respond(event_log, contract.id, "accepted")

    first_call = _materialize(service, contract.id)
    assert len(first_call) == 2
    first_call_obligation_ids = {obj.id for obj in first_call}

    object_ids_after_first = set(object_repository._revisions.keys())
    edges_after_first = {
        edge.id
        for obligation in first_call
        for edge in edge_repository.edges_to(obligation.id, "subject_to_obligation")
    }
    assert len(edges_after_first) == 2  # one bound object x two Obligations
    events_after_first = list(event_log.read_all())

    with pytest.raises(ObligationsAlreadyMaterializedError) as excinfo:
        _materialize(service, contract.id)

    assert excinfo.value.transfer_id == contract.id
    assert set(excinfo.value.obligation_ids) == first_call_obligation_ids

    # Exactly the first call's objects/edges/events remain — nothing more,
    # nothing less.
    assert set(object_repository._revisions.keys()) == object_ids_after_first
    for obligation in first_call:
        assert object_repository.list_revisions(obligation.id) == [obligation]
    edges_after_second_attempt = {
        edge.id
        for obligation in first_call
        for edge in edge_repository.edges_to(obligation.id, "subject_to_obligation")
    }
    assert edges_after_second_attempt == edges_after_first
    assert event_log.read_all() == events_after_first


def test_materialize_is_at_most_once_even_for_a_caveats_only_obligation() -> None:
    """The guard fires from the source_transfer_id carried on ANY prior
    obligation.created event for this transfer, including one materialized
    only via the caveats mechanism (no explicit obligations stub at all).
    """
    service, object_repository, _edge_repository, event_log = _harness()
    contract = _transfer_contract(obligations=[], caveats=["Treat with caution."])
    _seed(object_repository, contract)
    _respond(event_log, contract.id, "accepted")

    first_call = _materialize(service, contract.id)
    assert len(first_call) == 1

    with pytest.raises(ObligationsAlreadyMaterializedError) as excinfo:
        _materialize(service, contract.id)

    assert excinfo.value.obligation_ids == [first_call[0].id]


# ---------------------------------------------------------------------------
# Adaptation is discovered by propagation, not materialization.
# ---------------------------------------------------------------------------


def test_adaptation_is_discovered_by_propagation_not_materialization() -> None:
    service, object_repository, edge_repository, event_log = _harness()
    transferred_object_id = new_urn("claim")
    contract = _transfer_contract(transferred_object_ids=[transferred_object_id])
    _seed(object_repository, contract)
    _respond(event_log, contract.id, "adapted")

    [obligation] = _materialize(service, contract.id)
    assert obligation.body["bound_objects"] == [transferred_object_id]
    assert obligation.body["propagated_objects"] == []

    # The adapted_from edge TransferService.respond("adapted", ...) writes —
    # seeded directly here since this module never constructs TransferService.
    adapted_object_id = new_urn("claim")
    _seed_edge(
        edge_repository,
        source_id=adapted_object_id,
        edge_type="adapted_from",
        target_id=transferred_object_id,
    )

    stored = service.propagate(
        obligation.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert stored.body["propagated_objects"] == [adapted_object_id]
    assert stored.body["bound_objects"] == [transferred_object_id]  # never rewritten
    bound_sources = {
        e.source_id for e in edge_repository.edges_to(obligation.id, "subject_to_obligation")
    }
    assert bound_sources == {transferred_object_id, adapted_object_id}


# ---------------------------------------------------------------------------
# propagate(): line/branching/cyclic/disconnected graphs.
# ---------------------------------------------------------------------------


def _materialize_one_obligation(
    service: ObligationService,
    object_repository: FakeObjectRepository,
    event_log: FakeEventLog,
    *,
    root: str,
) -> StoredObject:
    contract = _transfer_contract(transferred_object_ids=[root])
    _seed(object_repository, contract)
    _respond(event_log, contract.id, "accepted")
    [obligation] = _materialize(service, contract.id)
    return obligation


def test_propagate_line_graph_binds_the_whole_chain() -> None:
    service, object_repository, edge_repository, event_log = _harness()
    root = new_urn("claim")
    obligation = _materialize_one_obligation(service, object_repository, event_log, root=root)

    b, c, d = (new_urn("claim") for _ in range(3))
    _seed_edge(edge_repository, source_id=b, edge_type="depends_on", target_id=root)
    _seed_edge(edge_repository, source_id=c, edge_type="depends_on", target_id=b)
    _seed_edge(edge_repository, source_id=d, edge_type="depends_on", target_id=c)

    stored = service.propagate(
        obligation.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert set(stored.body["propagated_objects"]) == {b, c, d}
    bound_sources = {
        e.source_id for e in edge_repository.edges_to(obligation.id, "subject_to_obligation")
    }
    assert bound_sources == {root, b, c, d}


def test_propagate_branching_graph_binds_every_branch() -> None:
    service, object_repository, edge_repository, event_log = _harness()
    root = new_urn("claim")
    obligation = _materialize_one_obligation(service, object_repository, event_log, root=root)

    b, c, d = (new_urn("claim") for _ in range(3))
    _seed_edge(edge_repository, source_id=b, edge_type="depends_on", target_id=root)
    _seed_edge(edge_repository, source_id=c, edge_type="depends_on", target_id=root)
    _seed_edge(edge_repository, source_id=d, edge_type="depends_on", target_id=b)

    stored = service.propagate(
        obligation.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert set(stored.body["propagated_objects"]) == {b, c, d}


def test_propagate_cyclic_graph_terminates() -> None:
    service, object_repository, edge_repository, event_log = _harness()
    root = new_urn("claim")
    obligation = _materialize_one_obligation(service, object_repository, event_log, root=root)

    b, c = new_urn("claim"), new_urn("claim")
    _seed_edge(edge_repository, source_id=b, edge_type="depends_on", target_id=root)
    _seed_edge(edge_repository, source_id=c, edge_type="depends_on", target_id=b)
    _seed_edge(edge_repository, source_id=root, edge_type="depends_on", target_id=c)

    stored = service.propagate(
        obligation.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    # The cycle loops back onto the seed itself, so root is genuinely
    # downstream of itself here too (compute_impact's own documented
    # cyclic-seed behavior).
    assert set(stored.body["propagated_objects"]) == {root, b, c}


def test_propagate_disconnected_component_is_not_bound() -> None:
    service, object_repository, edge_repository, event_log = _harness()
    root = new_urn("claim")
    obligation = _materialize_one_obligation(service, object_repository, event_log, root=root)

    b = new_urn("claim")
    unrelated_x, unrelated_y = new_urn("claim"), new_urn("claim")
    _seed_edge(edge_repository, source_id=b, edge_type="depends_on", target_id=root)
    _seed_edge(
        edge_repository, source_id=unrelated_y, edge_type="depends_on", target_id=unrelated_x
    )

    stored = service.propagate(
        obligation.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert stored.body["propagated_objects"] == [b]
    bound_sources = {
        e.source_id for e in edge_repository.edges_to(obligation.id, "subject_to_obligation")
    }
    assert unrelated_x not in bound_sources
    assert unrelated_y not in bound_sources


def test_propagate_duplicate_edges_do_not_cause_a_duplicate_binding_edge() -> None:
    service, object_repository, edge_repository, event_log = _harness()
    root = new_urn("claim")
    obligation = _materialize_one_obligation(service, object_repository, event_log, root=root)

    b = new_urn("claim")
    for _ in range(3):
        _seed_edge(edge_repository, source_id=b, edge_type="depends_on", target_id=root)

    service.propagate(
        obligation.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    bound_edges = [
        e
        for e in edge_repository.edges_to(obligation.id, "subject_to_obligation")
        if e.source_id == b
    ]
    assert len(bound_edges) == 1


# ---------------------------------------------------------------------------
# propagate(): idempotency and callable-anytime recomputation.
# ---------------------------------------------------------------------------


def test_propagate_on_a_fresh_obligation_with_no_downstream_is_a_no_op() -> None:
    service, object_repository, edge_repository, event_log = _harness()
    root = new_urn("claim")
    obligation = _materialize_one_obligation(service, object_repository, event_log, root=root)
    events_before = list(event_log.read_all())

    stored = service.propagate(
        obligation.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert stored.revision == obligation.revision == 1
    assert stored.body["propagated_objects"] == []
    assert event_log.read_all() == events_before
    assert len(edge_repository.edges_to(obligation.id, "subject_to_obligation")) == 1


def test_propagate_is_idempotent_on_repeated_calls() -> None:
    service, object_repository, edge_repository, event_log = _harness()
    root = new_urn("claim")
    obligation = _materialize_one_obligation(service, object_repository, event_log, root=root)
    dependent = new_urn("claim")
    _seed_edge(edge_repository, source_id=dependent, edge_type="depends_on", target_id=root)

    first = service.propagate(
        obligation.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    events_after_first = list(event_log.read_all())
    edges_after_first = list(edge_repository.edges_to(obligation.id, "subject_to_obligation"))

    second = service.propagate(
        obligation.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert first.revision == second.revision
    assert first.body["propagated_objects"] == second.body["propagated_objects"]
    assert event_log.read_all() == events_after_first
    assert edge_repository.edges_to(obligation.id, "subject_to_obligation") == edges_after_first


def test_propagate_discovers_a_new_derivative_added_after_a_prior_call() -> None:
    service, object_repository, edge_repository, event_log = _harness()
    root = new_urn("claim")
    obligation = _materialize_one_obligation(service, object_repository, event_log, root=root)
    dependent = new_urn("claim")
    _seed_edge(edge_repository, source_id=dependent, edge_type="depends_on", target_id=root)

    first = service.propagate(
        obligation.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )
    edges_after_first = list(edge_repository.edges_to(obligation.id, "subject_to_obligation"))

    new_derivative = new_urn("claim")
    _seed_edge(
        edge_repository, source_id=new_derivative, edge_type="derived_from", target_id=dependent
    )

    second = service.propagate(
        obligation.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert second.revision == first.revision + 1
    assert set(second.body["propagated_objects"]) == {dependent, new_derivative}
    edges_after_second = edge_repository.edges_to(obligation.id, "subject_to_obligation")
    # Every previously-recorded edge is still present, undisturbed.
    for edge in edges_after_first:
        assert edge in edges_after_second
    assert len(edges_after_second) == len(edges_after_first) + 1


def test_propagate_missing_obligation_raises() -> None:
    service, _object_repository, _edge_repository, _event_log = _harness()
    missing_id = new_urn("obligation")

    with pytest.raises(ObligationNotFoundError) as excinfo:
        service.propagate(
            missing_id,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )
    assert excinfo.value.obligation_id == missing_id


# ---------------------------------------------------------------------------
# resolve()/defer(): happy paths.
# ---------------------------------------------------------------------------


def test_resolve_happy_path() -> None:
    service, object_repository, _edge_repository, event_log = _harness()
    root = new_urn("claim")
    obligation = _materialize_one_obligation(service, object_repository, event_log, root=root)

    stored = service.resolve(
        obligation.id,
        "Attribution notice added to the published dataset.",
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert stored.body["status"] == "resolved"
    assert (
        stored.body["resolution_evidence"] == "Attribution notice added to the published dataset."
    )
    assert stored.revision == 2

    events = [e.event for e in event_log.read_all() if e.event.object_id == obligation.id]
    assert events[-1].event_type == "obligation.resolved"
    assert events[-1].causation_id == events[-2].id


def test_defer_happy_path() -> None:
    service, object_repository, _edge_repository, event_log = _harness()
    root = new_urn("claim")
    obligation = _materialize_one_obligation(service, object_repository, event_log, root=root)

    stored = service.defer(
        obligation.id,
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert stored.body["status"] == "deferred"
    assert stored.revision == 2

    events = [e.event for e in event_log.read_all() if e.event.object_id == obligation.id]
    assert events[-1].event_type == "obligation.deferred"


def test_resolve_missing_obligation_raises() -> None:
    service, _object_repository, _edge_repository, _event_log = _harness()
    missing_id = new_urn("obligation")

    with pytest.raises(ObligationNotFoundError):
        service.resolve(
            missing_id,
            "evidence",
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )


def test_resolve_rejects_empty_resolution_evidence() -> None:
    service, object_repository, _edge_repository, event_log = _harness()
    root = new_urn("claim")
    obligation = _materialize_one_obligation(service, object_repository, event_log, root=root)

    with pytest.raises(MissingResolutionEvidenceError) as excinfo:
        service.resolve(
            obligation.id,
            "",
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )

    assert excinfo.value.obligation_id == obligation.id
    assert object_repository.list_revisions(obligation.id) == [obligation]


# ---------------------------------------------------------------------------
# resolve()/defer(): illegal-transition matrix.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("first_action", "second_action"),
    [
        ("resolve", "resolve"),
        ("resolve", "defer"),
        ("defer", "defer"),
        ("defer", "resolve"),
    ],
)
def test_resolve_or_defer_on_a_non_open_obligation_fails_closed(
    first_action: str, second_action: str
) -> None:
    service, object_repository, _edge_repository, event_log = _harness()
    root = new_urn("claim")
    obligation = _materialize_one_obligation(service, object_repository, event_log, root=root)

    def _apply(action: str) -> StoredObject:
        if action == "resolve":
            return service.resolve(
                obligation.id,
                "some evidence",
                actor=_ACTOR,
                policy_version=_POLICY_VERSION,
                correlation_id=_correlation_id(),
            )
        return service.defer(
            obligation.id,
            actor=_ACTOR,
            policy_version=_POLICY_VERSION,
            correlation_id=_correlation_id(),
        )

    _apply(first_action)
    revisions_after_first = object_repository.list_revisions(obligation.id)

    with pytest.raises(InvalidTransitionError):
        _apply(second_action)

    assert object_repository.list_revisions(obligation.id) == revisions_after_first


# ---------------------------------------------------------------------------
# Never-silently-dropped: resolve/defer never touch subject_to_obligation
# edges or rewrite the prior open revision.
# ---------------------------------------------------------------------------


def test_resolve_never_touches_prior_subject_to_obligation_edges_or_revisions() -> None:
    service, object_repository, edge_repository, event_log = _harness()
    root = new_urn("claim")
    obligation = _materialize_one_obligation(service, object_repository, event_log, root=root)
    edges_before = list(edge_repository.edges_to(obligation.id, "subject_to_obligation"))

    service.resolve(
        obligation.id,
        "resolved via fixture",
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=_correlation_id(),
    )

    assert edge_repository.edges_to(obligation.id, "subject_to_obligation") == edges_before
    revisions = object_repository.list_revisions(obligation.id)
    assert [r.body["status"] for r in revisions] == ["open", "resolved"]
    # The prior open revision remains addressable.
    assert object_repository.get_revision(obligation.id, 1).body["status"] == "open"


# ---------------------------------------------------------------------------
# Event provenance completeness (MRR-NFR-001), across the whole causal chain.
# ---------------------------------------------------------------------------


def test_events_carry_complete_provenance_across_the_causal_chain() -> None:
    service, object_repository, edge_repository, event_log = _harness()
    root = new_urn("claim")
    correlation_id = _correlation_id()
    contract = _transfer_contract(transferred_object_ids=[root])
    _seed(object_repository, contract)
    _respond(event_log, contract.id, "accepted")

    [obligation] = service.materialize_from_transfer(
        contract.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    dependent = new_urn("claim")
    _seed_edge(edge_repository, source_id=dependent, edge_type="depends_on", target_id=root)
    service.propagate(
        obligation.id, actor=_ACTOR, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    service.resolve(
        obligation.id,
        "evidence",
        actor=_ACTOR,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )

    events = [e.event for e in event_log.read_all() if e.event.object_id == obligation.id]
    assert [e.event_type for e in events] == [
        "obligation.created",
        "obligation.propagated",
        "obligation.resolved",
    ]
    assert events[0].causation_id is None
    assert events[1].causation_id == events[0].id
    assert events[2].causation_id == events[1].id
    for event in events:
        assert event.actor == _ACTOR
        assert event.policy_version == _POLICY_VERSION
        assert event.correlation_id == correlation_id
        assert event.object_id == obligation.id
        assert event.occurred_at.tzinfo is not None
