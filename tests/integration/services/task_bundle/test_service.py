"""Integration tests for ``mrr.services.task_bundle.service.TaskBundleService``
and ``NodeTaskDecisionService`` (task-packets/E2-T03.yaml), run against a real
PostgreSQL via the ``postgres_engine`` fixture in
tests/integration/conftest.py — wired exactly as production code would:
``PostgresObjectRepository``/``PostgresEventLog`` over the fixture's engine,
with ``bind_unit_of_work`` closing over all three, shared by
``ResearchScoreService``, ``CapabilityRegistry``, ``TaskBundleService``, and
``NodeTaskDecisionService`` alike (one real ``objects``/``domain_events``
pair backs all of them, exactly like production). Skips visibly if
``MRR_TEST_DATABASE_URL`` is unset (fails hard instead if ``CI=true``) — see
that module's docstring.

Acceptance-test mapping (task-packets/E2-T03.yaml, integration tier):

- "the full CREATED->OFFERED->ACCEPTED path persists revisions + events
  atomically" -> ``test_create_offer_accept_persists_revisions_and_events_atomically``.
- "propose_modification revision 2 with revision 1 intact" ->
  ``test_propose_modification_persists_new_revision_and_leaves_prior_intact``.
- "illegal transition rolls back" ->
  ``test_illegal_transition_persists_nothing``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import mrr.services.capability_registry.service as capability_registry_module
import mrr.services.research_score.service as research_score_module
import mrr.services.task_bundle.service as task_bundle_module
import pytest
import sqlalchemy as sa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.contracts import NodeManifest, ResearchScore, TaskBundle
from mrr.domain.exceptions import InvalidTransitionError, NodeAuthorityError
from mrr.domain.hashing_policy import sign_object
from mrr.domain.identity import new_urn
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.persistence.tables import domain_events_table, objects_table
from mrr.services.capability_registry.service import CapabilityRegistry
from mrr.services.research_score.service import ResearchScoreService
from mrr.services.task_bundle.service import NodeTaskDecisionService, TaskBundleService
from sqlalchemy import Engine

_POLICY_VERSION = "policy-2026-07-01"
_CAPABILITY_NAME = "statistics.recompute"
_CAPABILITY_VERSION = "1.0.0"


def _services_for(
    engine: Engine,
) -> tuple[TaskBundleService, NodeTaskDecisionService, ResearchScoreService, CapabilityRegistry]:
    object_repository = PostgresObjectRepository(engine)
    event_log = PostgresEventLog(engine)

    research_score_service = ResearchScoreService(
        object_repository,
        event_log,
        research_score_module.bind_unit_of_work(engine, object_repository, event_log),
    )
    capability_registry = CapabilityRegistry(
        object_repository,
        event_log,
        capability_registry_module.bind_unit_of_work(engine, object_repository, event_log),
    )
    record = task_bundle_module.bind_unit_of_work(engine, object_repository, event_log)
    task_bundle_service = TaskBundleService(
        object_repository, event_log, record, research_score_service, capability_registry
    )
    node_decision_service = NodeTaskDecisionService(object_repository, event_log, record)
    return task_bundle_service, node_decision_service, research_score_service, capability_registry


def _draft_score(*, id: str | None = None) -> ResearchScore:
    return ResearchScore.model_validate(
        {
            "id": id or new_urn("research-score"),
            "api_version": "mrr/v1alpha1",
            "kind": "ResearchScore",
            "practice_id": new_urn("practice"),
            "revision": 1,
            "created_at": datetime.now(UTC),
            "created_by": new_urn("agent-role"),
            "content_hash": "sha256:" + "a" * 64,
            "question": "Does this fixture question satisfy the schema's minimum length?",
            "objectives": ["Measure something falsifiable."],
            "non_goals": [],
            "scope": {},
            "methods": {"allowed": [], "prohibited": []},
            "data_classes": ["PUBLIC"],
            "autonomy": {},
            "budgets": {},
            "quality_gates": ["No unsupported claim without independent verification."],
            "stop_conditions": ["Budget exhausted."],
            "publication_policy": {
                "max_disclosure": "INTERNAL",
                "external_publication_requires_approval": True,
            },
            "status": "DRAFT",
            "approvals": [],
        }
    )


def _create_and_approve_score(research_score_service: ResearchScoreService) -> str:
    """Drive a fresh score through the real RESEARCH_SCORE_LIFECYCLE
    (DRAFT -> IN_REVIEW -> APPROVED) so the seeded state is exactly what
    production would produce, not a hand-seeded shortcut (unlike the unit
    tests' ``_seed_score``, which is DB-free and seeds an arbitrary status
    directly) — the point of the integration tier is to exercise the real
    services together end to end.
    """
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")
    score = _draft_score()
    research_score_service.create(
        score, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    in_review = research_score_service.submit_for_review(
        score.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    approvable_body = dict(in_review.body)
    approvable_body["approvals"] = [new_urn("approval")]
    revised_score = ResearchScore.model_validate(
        {**approvable_body, "revision": in_review.revision + 1, "created_at": datetime.now(UTC)}
    )
    research_score_service.revise(
        revised_score, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    research_score_service.approve(
        score.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    return score.id


def _node_manifest(*, node_id: str, **overrides: Any) -> NodeManifest:
    now = datetime.now(UTC)
    data: dict[str, Any] = {
        "id": new_urn("node-manifest"),
        "api_version": "mrr/v1alpha1",
        "kind": "NodeManifest",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": now,
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "node_id": node_id,
        "capabilities": [
            {
                "name": _CAPABILITY_NAME,
                "version": _CAPABILITY_VERSION,
                "input_schema": "urn:mrr:schema:numeric-check:1",
                "output_schema": "urn:mrr:schema:evidence-crate:1",
                "max_autonomy": "A2",
                "approval": "automatic",
                "network_profile": "none",
            }
        ],
        "restrictions": [],
        "accepted_classifications": ["PUBLIC"],
        "transport_modes": ["online"],
        "valid_from": now - timedelta(days=1),
        "valid_until": now + timedelta(days=365),
        "public_keys": ["did:key:zTestKey"],
        "signature": {
            "signer_practice_id": new_urn("practice"),
            "key_id": "key-test",
            "algorithm": "Ed25519",
            "signed_at": now,
            "value": "0" * 44,
        },
    }
    data.update(overrides)
    return NodeManifest.model_validate(data)


def _register_node(
    capability_registry: CapabilityRegistry, node_id: str, private_key: Ed25519PrivateKey
) -> None:
    manifest = _node_manifest(node_id=node_id)
    signature_value = sign_object(private_key, manifest.model_dump(mode="json"))
    signed = manifest.model_copy(
        update={"signature": manifest.signature.model_copy(update={"value": signature_value})}
    )
    capability_registry.register(
        signed,
        private_key.public_key(),
        actor=new_urn("agent-role"),
        policy_version=_POLICY_VERSION,
        correlation_id=new_urn("research-run"),
    )


def _bundle(*, target_node_id: str, research_score_id: str, **overrides: Any) -> TaskBundle:
    now = datetime.now(UTC)
    origin_practice_id = new_urn("practice")
    data: dict[str, Any] = {
        "id": new_urn("task-bundle"),
        "api_version": "mrr/v1alpha1",
        "kind": "TaskBundle",
        "practice_id": origin_practice_id,
        "revision": 1,
        "created_at": now,
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "origin_practice_id": origin_practice_id,
        "target_node_id": target_node_id,
        "research_score_id": research_score_id,
        "research_score_revision": 1,
        "branch_id": new_urn("branch"),
        "capability": {"name": _CAPABILITY_NAME, "version": _CAPABILITY_VERSION},
        "purpose": "Recompute the summary statistics for the confirmatory branch.",
        "instructions": {"note": "run the standard pipeline"},
        "inputs": [],
        "data_access_mode": "read_local",
        "execution": {"image_digest": "sha256:" + "c" * 64, "entrypoint": ["run.sh"]},
        "resource_limits": {"cpu": 1.0, "memory_mb": 512, "disk_mb": 100, "timeout_seconds": 60},
        "network_policy": {"mode": "deny_all", "allowlist": []},
        "output_schema": "urn:mrr:schema:evidence-crate:1",
        "classification": "PUBLIC",
        "approval_requirement": "automatic",
        "expires_at": now + timedelta(days=1),
        "nonce": "n" * 16,
        "signature": {
            "signer_practice_id": origin_practice_id,
            "key_id": "origin-key",
            "algorithm": "Ed25519",
            "signed_at": now,
            "value": "0" * 44,
        },
    }
    data.update(overrides)
    return TaskBundle.model_validate(data)


def _sign(bundle: TaskBundle, private_key: Ed25519PrivateKey) -> TaskBundle:
    signature_value = sign_object(private_key, bundle.model_dump(mode="json"))
    return bundle.model_copy(
        update={"signature": bundle.signature.model_copy(update={"value": signature_value})}
    )


def test_create_offer_accept_persists_revisions_and_events_atomically(
    postgres_engine: Engine,
) -> None:
    task_bundle_service, node_decision_service, research_score_service, capability_registry = (
        _services_for(postgres_engine)
    )
    score_id = _create_and_approve_score(research_score_service)
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    node_id = new_urn("node")
    _register_node(capability_registry, node_id, node_key)
    bundle = _sign(_bundle(target_node_id=node_id, research_score_id=score_id), origin_key)
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")

    created = task_bundle_service.create(
        bundle, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    assert created.revision == 1
    assert created.body["status"] == "CREATED"

    offered = task_bundle_service.offer(
        bundle.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    assert offered.revision == 2
    assert offered.body["status"] == "OFFERED"

    accepted = node_decision_service.accept(
        bundle.id,
        node_id,
        origin_key.public_key(),
        actor=node_id,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )
    assert accepted.revision == 3
    assert accepted.body["status"] == "ACCEPTED"

    # Assert straight from the database, not just through the repository
    # abstraction — the whole point of the atomic unit-of-work invariant.
    with postgres_engine.connect() as conn:
        object_rows = conn.execute(
            sa.select(objects_table)
            .where(objects_table.c.id == bundle.id)
            .order_by(objects_table.c.revision.asc())
        ).fetchall()
        event_rows = conn.execute(
            sa.select(domain_events_table)
            .where(domain_events_table.c.object_id == bundle.id)
            .order_by(domain_events_table.c.sequence.asc())
        ).fetchall()

    assert len(object_rows) == 3
    assert [row.body["status"] for row in object_rows] == ["CREATED", "OFFERED", "ACCEPTED"]
    assert len(event_rows) == 3
    assert [row.event_type for row in event_rows] == [
        "task_bundle.created",
        "task_bundle.offered",
        "task_bundle.accepted",
    ]
    assert event_rows[0].causation_id is None
    assert event_rows[1].causation_id == event_rows[0].id
    assert event_rows[2].causation_id == event_rows[1].id


def test_propose_modification_persists_new_revision_and_leaves_prior_intact(
    postgres_engine: Engine,
) -> None:
    task_bundle_service, node_decision_service, research_score_service, capability_registry = (
        _services_for(postgres_engine)
    )
    score_id = _create_and_approve_score(research_score_service)
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    node_id = new_urn("node")
    _register_node(capability_registry, node_id, node_key)
    bundle = _sign(_bundle(target_node_id=node_id, research_score_id=score_id), origin_key)
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")

    task_bundle_service.create(
        bundle, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    task_bundle_service.offer(
        bundle.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    modified = _sign(
        bundle.model_copy(
            update={
                "revision": 2,
                "content_hash": "sha256:" + "d" * 64,
                "resource_limits": bundle.resource_limits.model_copy(update={"cpu": 4.0}),
            }
        ),
        node_key,
    )
    result = node_decision_service.propose_modification(
        bundle.id,
        modified,
        node_id,
        origin_key.public_key(),
        actor=node_id,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )
    assert result.body["status"] == "OFFERED"
    assert result.body["revision"] == 2
    assert result.body["resource_limits"]["cpu"] == 4.0

    with postgres_engine.connect() as conn:
        object_rows = conn.execute(
            sa.select(objects_table)
            .where(objects_table.c.id == bundle.id)
            .order_by(objects_table.c.revision.asc())
        ).fetchall()

    # 1: CREATED (content revision 1); 2: OFFERED (content revision 1);
    # 3: MODIFICATION_PROPOSED (content revision 1, unchanged);
    # 4: OFFERED again (content revision 2, the node's new signed content).
    assert len(object_rows) == 4
    assert object_rows[0].body["content_hash"] == bundle.content_hash
    assert object_rows[0].body["resource_limits"]["cpu"] == 1.0
    assert object_rows[2].body["status"] == "MODIFICATION_PROPOSED"
    assert object_rows[2].body["content_hash"] == bundle.content_hash  # unchanged content
    assert object_rows[3].body["status"] == "OFFERED"
    assert object_rows[3].body["content_hash"] == "sha256:" + "d" * 64
    assert object_rows[3].body["resource_limits"]["cpu"] == 4.0

    # The very first, origin-signed revision is untouched and independently
    # addressable.
    with postgres_engine.connect() as conn:
        original_row = conn.execute(
            sa.select(objects_table).where(
                objects_table.c.id == bundle.id, objects_table.c.revision == 1
            )
        ).one()
    assert original_row.body["status"] == "CREATED"
    assert original_row.body["content_hash"] == bundle.content_hash


def test_illegal_transition_persists_nothing(postgres_engine: Engine) -> None:
    task_bundle_service, node_decision_service, research_score_service, capability_registry = (
        _services_for(postgres_engine)
    )
    score_id = _create_and_approve_score(research_score_service)
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    node_id = new_urn("node")
    _register_node(capability_registry, node_id, node_key)
    bundle = _sign(_bundle(target_node_id=node_id, research_score_id=score_id), origin_key)
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")

    task_bundle_service.create(
        bundle, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    # Never offered — still CREATED. Accepting now is illegal
    # (TASK_BUNDLE_LIFECYCLE only draws OFFERED -> ACCEPTED).

    with pytest.raises(InvalidTransitionError):
        node_decision_service.accept(
            bundle.id,
            node_id,
            origin_key.public_key(),
            actor=node_id,
            policy_version=_POLICY_VERSION,
            correlation_id=correlation_id,
        )

    with postgres_engine.connect() as conn:
        object_rows = conn.execute(
            sa.select(objects_table).where(objects_table.c.id == bundle.id)
        ).fetchall()
        event_rows = conn.execute(
            sa.select(domain_events_table).where(domain_events_table.c.object_id == bundle.id)
        ).fetchall()

    assert len(object_rows) == 1  # only the original create() — no rollback residue
    assert len(event_rows) == 1


def test_non_target_node_fails_closed_and_persists_nothing(postgres_engine: Engine) -> None:
    task_bundle_service, node_decision_service, research_score_service, capability_registry = (
        _services_for(postgres_engine)
    )
    score_id = _create_and_approve_score(research_score_service)
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    node_id = new_urn("node")
    _register_node(capability_registry, node_id, node_key)
    bundle = _sign(_bundle(target_node_id=node_id, research_score_id=score_id), origin_key)
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")

    task_bundle_service.create(
        bundle, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    task_bundle_service.offer(
        bundle.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    not_the_target_node = new_urn("node")

    with pytest.raises(NodeAuthorityError):
        node_decision_service.accept(
            bundle.id,
            not_the_target_node,
            origin_key.public_key(),
            actor=not_the_target_node,
            policy_version=_POLICY_VERSION,
            correlation_id=correlation_id,
        )

    with postgres_engine.connect() as conn:
        object_rows = conn.execute(
            sa.select(objects_table).where(objects_table.c.id == bundle.id)
        ).fetchall()
    assert len(object_rows) == 2  # create + offer only


def test_reject_persists_refusal_event_with_reason_category(postgres_engine: Engine) -> None:
    task_bundle_service, node_decision_service, research_score_service, capability_registry = (
        _services_for(postgres_engine)
    )
    score_id = _create_and_approve_score(research_score_service)
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    node_id = new_urn("node")
    _register_node(capability_registry, node_id, node_key)
    bundle = _sign(_bundle(target_node_id=node_id, research_score_id=score_id), origin_key)
    actor = new_urn("agent-role")
    correlation_id = new_urn("research-run")

    task_bundle_service.create(
        bundle, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )
    task_bundle_service.offer(
        bundle.id, actor=actor, policy_version=_POLICY_VERSION, correlation_id=correlation_id
    )

    rejected = node_decision_service.reject(
        bundle.id,
        node_id,
        origin_key.public_key(),
        reason_category="resource_unavailable",
        actor=node_id,
        policy_version=_POLICY_VERSION,
        correlation_id=correlation_id,
    )
    assert rejected.body["status"] == "REJECTED"

    with postgres_engine.connect() as conn:
        event_rows = conn.execute(
            sa.select(domain_events_table)
            .where(domain_events_table.c.object_id == bundle.id)
            .order_by(domain_events_table.c.sequence.asc())
        ).fetchall()
    reject_event = event_rows[-1]
    assert reject_event.event_type == "task_bundle.rejected"
    assert reject_event.payload["reason_category"] == "resource_unavailable"
