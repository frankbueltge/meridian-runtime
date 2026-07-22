"""Unit tests for ``mrr.domain.ro_crate`` (task-packets/E8-T01.yaml, EXTENDED
by task-packets/E8-T02.yaml's PROV layer), run entirely DB-free and
I/O-free — plain mapping fixtures, no repository, no filesystem, no
``ArtifactStore``.

Acceptance-test mapping (task-packets/E8-T01.yaml + E8-T02.yaml, unit/
contract tier):

- naming (R1): ``test_object_relative_path_replaces_colons_with_underscores``,
  ``test_artifact_relative_path_strips_the_sha256_prefix``.
- determinism (R6, AT4's own logic reused at the unit tier): identical
  inputs yield byte-identical ``ro-crate-metadata.json`` ->
  ``test_build_export_is_deterministic_across_repeated_calls``.
- structure (R1): metadata descriptor, root data entity, ``hasPart``
  completeness, ``File`` entities, contextual entities, signature emitted
  only when present, artifacts get no contextual entity ->
  ``test_metadata_descriptor_and_root_data_entity_shape``,
  ``test_has_part_names_every_object_and_artifact_file``,
  ``test_object_file_entity_carries_content_size_and_hash_and_about_link``,
  ``test_contextual_entity_carries_urn_kind_hash_practice_id``,
  ``test_signature_is_emitted_only_when_the_body_carries_one``,
  ``test_artifacts_get_file_entities_but_no_contextual_entity``.
- ordering: sorted-urn/sorted-hash order (R2: "exported in sorted-URN
  order") -> ``test_objects_and_artifacts_are_sorted_deterministically``.
- no wall-clock (R1 invariant): ``datePublished`` derives from the crate's
  own stored ``created_at`` ->
  ``test_date_published_is_the_crates_own_created_at_never_wall_clock``.
- the internal "crate must be in its own plan" guard ->
  ``test_build_ro_crate_metadata_raises_if_crate_urn_is_not_in_the_plan``.
- E8-T02 R1/R2/R3 wiring (the "PROV mapping (task-packets/E8-T02.yaml)"
  section below): the ``@context`` prefix, ``@type`` list-vs-string, per-kind
  relations reaching the built document, the Claim carve-out, stub entities,
  and the AT3 file-set regression argument
  (``test_provenance_edges_do_not_affect_the_export_plan``). Boundary note:
  E8-T01's own former ``test_metadata_contains_no_prov_terms_anywhere`` is
  gone — emitting ``prov:`` terms is now this module's own job; its "scan
  every string" helper (``_flatten_strings``) is repurposed below to assert
  presence/absence PER kind instead of absence everywhere.
"""

from __future__ import annotations

import json
from typing import Any, cast

from mrr.crypto.canonical import canonicalize
from mrr.domain.identity import new_urn
from mrr.domain.projection import ProvenanceEdge
from mrr.domain.prov_mapping import PROV_VOCAB_PREFIX, PROV_VOCAB_URI
from mrr.domain.ro_crate import (
    METADATA_FILE_NAME,
    MRR_VOCAB_PREFIX,
    MRR_VOCAB_URI,
    RO_CRATE_CONTEXT_URI,
    RO_CRATE_PROFILE_URI,
    ROOT_DATA_ENTITY_ID,
    artifact_relative_path,
    build_export,
    build_export_plan,
    build_ro_crate_metadata,
    object_relative_path,
)

_CRATE_CREATED_AT = "2026-07-01T12:00:00+00:00"


def _crate_body(*, crate_id: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": crate_id,
        "api_version": "mrr/v1alpha1",
        "kind": "EvidenceCrate",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": _CRATE_CREATED_AT,
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "sealed": True,
        "signature": {
            "signer_practice_id": new_urn("practice"),
            "key_id": "node-key-1",
            "algorithm": "Ed25519",
            "signed_at": _CRATE_CREATED_AT,
            "value": "0" * 44,
        },
    }
    body.update(overrides)
    return body


def _claim_body(*, claim_id: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": claim_id,
        "api_version": "mrr/v1alpha1",
        "kind": "Claim",
        "practice_id": new_urn("practice"),
        "content_hash": "sha256:" + "b" * 64,
        "assertion": "Fixture assertion for a pure ro_crate unit test.",
    }
    body.update(overrides)
    return body


def _small_fixture() -> tuple[str, dict[str, dict[str, Any]], dict[str, int]]:
    """A crate plus one Claim plus one artifact — small enough to assert
    the FULL metadata shape against, big enough to exercise ordering,
    ``hasPart`` completeness, and the object-vs-artifact entity split.
    """
    crate_id = new_urn("evidence-crate")
    claim_id = new_urn("claim")
    content_hash = "sha256:" + "c" * 64
    object_bodies = {
        crate_id: _crate_body(crate_id=crate_id, proposed_claims=[claim_id]),
        claim_id: _claim_body(claim_id=claim_id),
    }
    artifact_sizes = {content_hash: 42}
    return crate_id, object_bodies, artifact_sizes


def _entities_by_id(metadata: dict[str, Any]) -> dict[str, Any]:
    """``metadata["@graph"]``, keyed by each entity's own ``@id`` — round-
    tripped through plain ``json`` first so every structure-indexing
    assertion below reads the metadata exactly as a real consumer of
    ``ro-crate-metadata.json`` would (parsed JSON, ``Any``-typed), rather
    than fighting ``mrr.crypto.canonical.JSONValue``'s precise recursive
    union at every nested index. That union exists to keep
    ``mrr.domain.ro_crate`` itself honest about what it may emit; a test
    asserting on arbitrary nested structure gains nothing from re-deriving
    that same precision at every call site.
    """
    parsed = cast("dict[str, Any]", json.loads(json.dumps(metadata)))
    return {entity["@id"]: entity for entity in parsed["@graph"]}


# ---------------------------------------------------------------------------
# Naming (R1).
# ---------------------------------------------------------------------------


def test_object_relative_path_replaces_colons_with_underscores() -> None:
    urn = "urn:mrr:evidence-crate:01ARZ3NDEKTSV4RRFFQ69G5FAV"
    assert (
        object_relative_path(urn)
        == "objects/urn_mrr_evidence-crate_01ARZ3NDEKTSV4RRFFQ69G5FAV.json"
    )


def test_artifact_relative_path_strips_the_sha256_prefix() -> None:
    content_hash = "sha256:" + "f" * 64
    assert artifact_relative_path(content_hash) == f"artifacts/{'f' * 64}"


# ---------------------------------------------------------------------------
# Determinism (R6 / AT4's underlying logic).
# ---------------------------------------------------------------------------


def test_build_export_is_deterministic_across_repeated_calls() -> None:
    crate_id, object_bodies, artifact_sizes = _small_fixture()

    plan_a, metadata_a = build_export(
        crate_urn=crate_id, object_bodies=object_bodies, artifact_sizes=artifact_sizes
    )
    plan_b, metadata_b = build_export(
        crate_urn=crate_id, object_bodies=object_bodies, artifact_sizes=artifact_sizes
    )

    assert plan_a == plan_b
    assert metadata_a == metadata_b
    assert canonicalize(metadata_a) == canonicalize(metadata_b)


def test_build_export_is_deterministic_regardless_of_input_mapping_order() -> None:
    crate_id, object_bodies, artifact_sizes = _small_fixture()
    reversed_bodies = dict(reversed(list(object_bodies.items())))

    _, metadata_forward = build_export(
        crate_urn=crate_id, object_bodies=object_bodies, artifact_sizes=artifact_sizes
    )
    _, metadata_reversed = build_export(
        crate_urn=crate_id, object_bodies=reversed_bodies, artifact_sizes=artifact_sizes
    )

    assert canonicalize(metadata_forward) == canonicalize(metadata_reversed)


def _flatten_strings(value: object) -> list[str]:
    """Every string appearing anywhere in a JSON-like structure — keys and
    values alike — reused below (E8-T02) to scan for/count ``prov:*``
    occurrences per fixture, rather than (E8-T01's own retired test) to
    assert their total absence.
    """
    strings: list[str] = []
    if isinstance(value, str):
        strings.append(value)
    elif isinstance(value, dict):
        for key, val in value.items():
            strings.append(key)
            strings.extend(_flatten_strings(val))
    elif isinstance(value, list):
        for item in value:
            strings.extend(_flatten_strings(item))
    return strings


# ---------------------------------------------------------------------------
# Structure (R1).
# ---------------------------------------------------------------------------


def test_metadata_descriptor_and_root_data_entity_shape() -> None:
    crate_id, object_bodies, artifact_sizes = _small_fixture()
    _, metadata = build_export(
        crate_urn=crate_id, object_bodies=object_bodies, artifact_sizes=artifact_sizes
    )

    assert metadata["@context"] == [
        RO_CRATE_CONTEXT_URI,
        {MRR_VOCAB_PREFIX: MRR_VOCAB_URI},
        {PROV_VOCAB_PREFIX: PROV_VOCAB_URI},
    ]
    graph = _entities_by_id(metadata)

    descriptor = graph[METADATA_FILE_NAME]
    assert descriptor["@type"] == "CreativeWork"
    assert descriptor["conformsTo"] == {"@id": RO_CRATE_PROFILE_URI}
    assert descriptor["about"] == {"@id": ROOT_DATA_ENTITY_ID}

    root = graph[ROOT_DATA_ENTITY_ID]
    assert root["@type"] == "Dataset"
    assert root["datePublished"] == _CRATE_CREATED_AT


def test_has_part_names_every_object_and_artifact_file() -> None:
    crate_id, object_bodies, artifact_sizes = _small_fixture()
    plan, metadata = build_export(
        crate_urn=crate_id, object_bodies=object_bodies, artifact_sizes=artifact_sizes
    )

    graph = _entities_by_id(metadata)
    has_part_ids = {ref["@id"] for ref in graph[ROOT_DATA_ENTITY_ID]["hasPart"]}

    expected_ids = {obj.relative_path for obj in plan.objects} | {
        artifact.relative_path for artifact in plan.artifacts
    }
    assert has_part_ids == expected_ids
    # Every hasPart entry names an existing File entity in the graph too.
    for relative_path in has_part_ids:
        assert graph[relative_path]["@type"] == "File"


def test_object_file_entity_carries_content_size_and_hash_and_about_link() -> None:
    crate_id, object_bodies, artifact_sizes = _small_fixture()
    plan, metadata = build_export(
        crate_urn=crate_id, object_bodies=object_bodies, artifact_sizes=artifact_sizes
    )
    graph = _entities_by_id(metadata)

    crate_export = next(obj for obj in plan.objects if obj.urn == crate_id)
    entity = graph[crate_export.relative_path]
    assert entity["contentSize"] == len(crate_export.canonical_bytes)
    assert entity[f"{MRR_VOCAB_PREFIX}:contentHash"] == crate_export.body["content_hash"]
    assert entity["about"] == {"@id": crate_id}


def test_contextual_entity_carries_urn_kind_hash_practice_id() -> None:
    crate_id, object_bodies, artifact_sizes = _small_fixture()
    _, metadata = build_export(
        crate_urn=crate_id, object_bodies=object_bodies, artifact_sizes=artifact_sizes
    )
    graph = _entities_by_id(metadata)

    crate_body = object_bodies[crate_id]
    entity = graph[crate_id]
    # EvidenceCrate is a derived-row PROV mapping (task-packets/E8-T02.yaml
    # derived_decisions (a)) -> a two-element [mrr:<kind>, prov:<Type>] list,
    # never a bare string, once ANY kind mapping exists for this kind.
    assert entity["@type"] == [f"{MRR_VOCAB_PREFIX}:EvidenceCrate", "prov:Entity"]
    assert entity[f"{MRR_VOCAB_PREFIX}:urn"] == crate_id
    assert entity[f"{MRR_VOCAB_PREFIX}:kind"] == "EvidenceCrate"
    assert entity[f"{MRR_VOCAB_PREFIX}:contentHash"] == crate_body["content_hash"]
    assert entity[f"{MRR_VOCAB_PREFIX}:practiceId"] == crate_body["practice_id"]


def test_signature_is_emitted_only_when_the_body_carries_one() -> None:
    crate_id, object_bodies, artifact_sizes = _small_fixture()
    claim_id = next(urn for urn, body in object_bodies.items() if body["kind"] == "Claim")
    _, metadata = build_export(
        crate_urn=crate_id, object_bodies=object_bodies, artifact_sizes=artifact_sizes
    )
    graph = _entities_by_id(metadata)

    crate_body = object_bodies[crate_id]
    assert graph[crate_id][f"{MRR_VOCAB_PREFIX}:signature"] == crate_body["signature"]
    assert f"{MRR_VOCAB_PREFIX}:signature" not in graph[claim_id]


def test_artifacts_get_file_entities_but_no_contextual_entity() -> None:
    crate_id, object_bodies, artifact_sizes = _small_fixture()
    plan, metadata = build_export(
        crate_urn=crate_id, object_bodies=object_bodies, artifact_sizes=artifact_sizes
    )
    graph = _entities_by_id(metadata)

    (artifact,) = plan.artifacts
    file_entity = graph[artifact.relative_path]
    assert file_entity["@type"] == "File"
    assert file_entity["contentSize"] == artifact.size_bytes
    assert file_entity[f"{MRR_VOCAB_PREFIX}:contentHash"] == artifact.content_hash
    assert "about" not in file_entity

    # No contextual entity keyed by the artifact's own content hash exists.
    assert artifact.content_hash not in graph


# ---------------------------------------------------------------------------
# Ordering (R2: "exported in sorted-URN order").
# ---------------------------------------------------------------------------


def test_objects_and_artifacts_are_sorted_deterministically() -> None:
    crate_id = new_urn("evidence-crate")
    claim_ids = sorted(new_urn("claim") for _ in range(3))
    object_bodies = {crate_id: _crate_body(crate_id=crate_id, proposed_claims=claim_ids)}
    for claim_id in claim_ids:
        object_bodies[claim_id] = _claim_body(claim_id=claim_id)
    # Insert in reverse-sorted order to prove the plan itself re-sorts.
    shuffled_bodies = {urn: object_bodies[urn] for urn in sorted(object_bodies, reverse=True)}
    hashes = sorted(("sha256:" + digit * 64) for digit in ("1", "2", "3"))
    artifact_sizes = dict.fromkeys(reversed(hashes), 10)

    plan = build_export_plan(shuffled_bodies, artifact_sizes)

    assert [obj.urn for obj in plan.objects] == sorted(object_bodies)
    assert [artifact.content_hash for artifact in plan.artifacts] == hashes


# ---------------------------------------------------------------------------
# No wall-clock timestamps (R1 invariant).
# ---------------------------------------------------------------------------


def test_date_published_is_the_crates_own_created_at_never_wall_clock() -> None:
    crate_id, object_bodies, artifact_sizes = _small_fixture()
    _, metadata = build_export(
        crate_urn=crate_id, object_bodies=object_bodies, artifact_sizes=artifact_sizes
    )
    graph = _entities_by_id(metadata)
    assert graph[ROOT_DATA_ENTITY_ID]["datePublished"] == _CRATE_CREATED_AT


# ---------------------------------------------------------------------------
# The internal "crate must be in its own plan" guard.
# ---------------------------------------------------------------------------


def test_build_ro_crate_metadata_raises_if_crate_urn_is_not_in_the_plan() -> None:
    crate_id, object_bodies, artifact_sizes = _small_fixture()
    plan = build_export_plan(object_bodies, artifact_sizes)

    try:
        build_ro_crate_metadata(crate_urn=new_urn("evidence-crate"), plan=plan)
    except ValueError as exc:
        assert "names no object in plan.objects" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected ValueError for a crate_urn absent from the plan")


# ---------------------------------------------------------------------------
# PROV mapping (task-packets/E8-T02.yaml).
# ---------------------------------------------------------------------------


def _run_manifest_body(
    *,
    manifest_id: str,
    executor_id: str,
    parameters: dict[str, Any] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": manifest_id,
        "api_version": "mrr/v1alpha1",
        "kind": "RunManifest",
        "practice_id": new_urn("practice"),
        "content_hash": "sha256:" + "d" * 64,
        "executor_id": executor_id,
        "parameters": parameters or {},
    }
    body.update(overrides)
    return body


def _verification_body(
    *,
    verification_id: str,
    reviewer_id: str,
    target_id: str,
    evidence_inspected: list[str] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": verification_id,
        "api_version": "mrr/v1alpha1",
        "kind": "VerificationResult",
        "practice_id": new_urn("practice"),
        "content_hash": "sha256:" + "e" * 64,
        "reviewer_id": reviewer_id,
        "target_id": target_id,
        "evidence_inspected": evidence_inspected or [],
    }
    body.update(overrides)
    return body


def test_context_gains_the_prov_prefix_after_the_mrr_one() -> None:
    crate_id, object_bodies, artifact_sizes = _small_fixture()
    _, metadata = build_export(
        crate_urn=crate_id, object_bodies=object_bodies, artifact_sizes=artifact_sizes
    )
    assert metadata["@context"] == [
        RO_CRATE_CONTEXT_URI,
        {MRR_VOCAB_PREFIX: MRR_VOCAB_URI},
        {PROV_VOCAB_PREFIX: PROV_VOCAB_URI},
    ]


def test_unmapped_kind_keeps_a_bare_mrr_type_string() -> None:
    # TaskBundle is a real MRR kind that names no row anywhere in
    # mrr.domain.prov_mapping.KIND_TO_PROV_TYPE.
    crate_id = new_urn("evidence-crate")
    bundle_id = new_urn("task-bundle")
    object_bodies = {
        crate_id: _crate_body(crate_id=crate_id),
        bundle_id: {
            "id": bundle_id,
            "kind": "TaskBundle",
            "content_hash": "sha256:" + "9" * 64,
            "practice_id": new_urn("practice"),
        },
    }
    _, metadata = build_export(crate_urn=crate_id, object_bodies=object_bodies, artifact_sizes={})
    graph = _entities_by_id(metadata)

    assert graph[bundle_id]["@type"] == f"{MRR_VOCAB_PREFIX}:TaskBundle"
    # No stray "prov:" hits anywhere in this unmapped kind's own entity.
    prov_hits = [s for s in _flatten_strings(graph[bundle_id]) if s.startswith("prov:")]
    assert prov_hits == []


def test_claim_gets_a_prov_type_but_no_prov_relation_for_proposer_id() -> None:
    # R2's own preamble: Claim is a PROV Entity, so claim.proposer_id gets
    # no prov relation in this packet (it stays an mrr: field only).
    crate_id, object_bodies, artifact_sizes = _small_fixture()
    claim_id = next(urn for urn, body in object_bodies.items() if body["kind"] == "Claim")
    object_bodies[claim_id]["proposer_id"] = new_urn("agent-role")

    _, metadata = build_export(
        crate_urn=crate_id, object_bodies=object_bodies, artifact_sizes=artifact_sizes
    )
    claim_entity = _entities_by_id(metadata)[claim_id]

    assert claim_entity["@type"] == [f"{MRR_VOCAB_PREFIX}:Claim", "prov:Entity"]
    assert not any(key.startswith("prov:") and key != "@type" for key in claim_entity)


def test_evidence_crate_and_its_artifacts_get_generated_by_and_a_run_stub() -> None:
    run_id = new_urn("run")  # never itself part of object_bodies -> unreached.
    crate_id = new_urn("evidence-crate")
    content_hash = "sha256:" + "c" * 64
    object_bodies = {crate_id: _crate_body(crate_id=crate_id, run_id=run_id)}
    artifact_sizes = {content_hash: 7}

    _, metadata = build_export(
        crate_urn=crate_id, object_bodies=object_bodies, artifact_sizes=artifact_sizes
    )
    graph = _entities_by_id(metadata)

    crate_entity = graph[crate_id]
    assert crate_entity["prov:wasGeneratedBy"] == {"@id": run_id}

    artifact_relative = artifact_relative_path(content_hash)
    assert graph[artifact_relative]["prov:wasGeneratedBy"] == {"@id": run_id}

    stub = graph[run_id]
    assert stub == {"@id": run_id, "@type": "prov:Activity", f"{MRR_VOCAB_PREFIX}:urn": run_id}
    # The stub names no file — never added to hasPart.
    has_part_ids = {ref["@id"] for ref in graph[ROOT_DATA_ENTITY_ID]["hasPart"]}
    assert run_id not in has_part_ids


def test_run_manifest_and_verification_relations_reach_the_document_with_agent_stubs() -> None:
    crate_id = new_urn("evidence-crate")
    manifest_id = new_urn("run")
    verification_id = new_urn("verification")
    executor_id = new_urn("executor")  # -> prov:Agent stub (packet amendment 2026-07-22).
    reviewer_id = new_urn("person")  # mapped -> prov:Agent stub.
    target_claim_id = new_urn("claim")  # unreached -> prov:Entity stub.
    inspected_anchor_id = new_urn("evidence-anchor")  # unreached -> prov:Entity stub.

    object_bodies = {
        crate_id: _crate_body(crate_id=crate_id, run_id=manifest_id),
        manifest_id: _run_manifest_body(manifest_id=manifest_id, executor_id=executor_id),
        verification_id: _verification_body(
            verification_id=verification_id,
            reviewer_id=reviewer_id,
            target_id=target_claim_id,
            evidence_inspected=[inspected_anchor_id],
        ),
    }

    _, metadata = build_export(crate_urn=crate_id, object_bodies=object_bodies, artifact_sizes={})
    graph = _entities_by_id(metadata)

    manifest_entity = graph[manifest_id]
    assert manifest_entity["@type"] == [f"{MRR_VOCAB_PREFIX}:RunManifest", "prov:Activity"]
    assert manifest_entity["prov:wasAssociatedWith"] == {"@id": executor_id}
    assert "prov:used" not in manifest_entity  # empty `parameters` names no artifact urn.

    verification_entity = graph[verification_id]
    assert verification_entity["@type"] == [
        f"{MRR_VOCAB_PREFIX}:VerificationResult",
        "prov:Activity",
    ]
    assert verification_entity["prov:wasAssociatedWith"] == {"@id": reviewer_id}
    assert verification_entity["prov:used"] == [
        {"@id": urn} for urn in sorted([target_claim_id, inspected_anchor_id])
    ]

    # Executor stub: "executor" -> prov:Agent per the packet's
    # reviewer_resolution AMENDMENT of 2026-07-22 (governance amendment,
    # not implementation guess — see mrr.domain.prov_mapping's docstring).
    executor_stub = graph[executor_id]
    assert executor_stub == {
        "@id": executor_id,
        "@type": "prov:Agent",
        f"{MRR_VOCAB_PREFIX}:urn": executor_id,
    }

    reviewer_stub = graph[reviewer_id]
    assert reviewer_stub["@type"] == "prov:Agent"

    claim_stub = graph[target_claim_id]
    assert claim_stub["@type"] == "prov:Entity"

    anchor_stub = graph[inspected_anchor_id]
    assert anchor_stub["@type"] == "prov:Entity"

    has_part_ids = {ref["@id"] for ref in graph[ROOT_DATA_ENTITY_ID]["hasPart"]}
    for stub_urn in (executor_id, reviewer_id, target_claim_id, inspected_anchor_id):
        assert stub_urn not in has_part_ids


def test_derived_from_typed_edge_produces_a_relation_and_a_stub_for_its_target() -> None:
    crate_id, object_bodies, artifact_sizes = _small_fixture()
    claim_id = next(urn for urn, body in object_bodies.items() if body["kind"] == "Claim")
    target_id = new_urn("source-record")  # unexported -> gets a prov:Entity stub.
    edges = (
        ProvenanceEdge(
            source_id=claim_id,
            target_id=target_id,
            target_kind="SourceRecord",
            relation="derived_from",
            via="edge",
            edge_id=new_urn("edge"),
        ),
    )

    _, metadata = build_export(
        crate_urn=crate_id,
        object_bodies=object_bodies,
        artifact_sizes=artifact_sizes,
        provenance_edges=edges,
    )
    graph = _entities_by_id(metadata)

    assert graph[claim_id]["prov:wasDerivedFrom"] == [{"@id": target_id}]
    assert graph[target_id] == {
        "@id": target_id,
        "@type": "prov:Entity",
        f"{MRR_VOCAB_PREFIX}:urn": target_id,
    }


def test_a_derived_from_edge_of_the_wrong_via_kind_produces_no_relation() -> None:
    # mrr.services.projection.service never emits a "derived_from"-named
    # FIELD reference today, but this module's own R2(a) rule is scoped to
    # via == "edge" regardless — proven directly here.
    crate_id, object_bodies, artifact_sizes = _small_fixture()
    claim_id = next(urn for urn, body in object_bodies.items() if body["kind"] == "Claim")
    edges = (
        ProvenanceEdge(
            source_id=claim_id,
            target_id=new_urn("run"),
            target_kind="RunManifest",
            relation="derived_from",
            via="field",
            edge_id=None,
        ),
    )

    _, metadata = build_export(
        crate_urn=crate_id,
        object_bodies=object_bodies,
        artifact_sizes=artifact_sizes,
        provenance_edges=edges,
    )
    claim_entity = _entities_by_id(metadata)[claim_id]
    assert "prov:wasDerivedFrom" not in claim_entity


def test_stub_entities_are_deterministic_and_sorted() -> None:
    crate_id = new_urn("evidence-crate")
    claim_id = new_urn("claim")
    object_bodies = {
        crate_id: _crate_body(crate_id=crate_id),
        claim_id: _claim_body(claim_id=claim_id),
    }
    # Three independently-unreached derived_from targets, inserted out of
    # sorted order, to prove the stub section itself re-sorts.
    targets = sorted(new_urn("source-record") for _ in range(3))
    edges = tuple(
        ProvenanceEdge(
            source_id=claim_id,
            target_id=target,
            target_kind="SourceRecord",
            relation="derived_from",
            via="edge",
            edge_id=new_urn("edge"),
        )
        for target in reversed(targets)  # inserted out of order
    )

    _, metadata = build_export(
        crate_urn=crate_id,
        object_bodies=object_bodies,
        artifact_sizes={},
        provenance_edges=edges,
    )
    graph_ids = list(_entities_by_id(metadata).keys())
    stub_positions = [graph_ids.index(target) for target in targets]
    assert stub_positions == sorted(stub_positions)


def test_provenance_edges_do_not_affect_the_export_plan() -> None:
    """AT3 (file-set regression), constructed directly: ``plan`` — hence
    every object's ``relative_path``/``canonical_bytes`` and every
    artifact's ``relative_path``/``size_bytes``, i.e. the entire exported
    FILE SET other than ``ro-crate-metadata.json`` — is a pure function of
    ``(object_bodies, artifact_sizes)`` alone. ``provenance_edges`` never
    reaches ``build_export_plan`` at all, so passing it (or not, or
    differently) cannot change the file set, by construction.
    """
    crate_id, object_bodies, artifact_sizes = _small_fixture()
    claim_id = next(urn for urn, body in object_bodies.items() if body["kind"] == "Claim")
    edges = (
        ProvenanceEdge(
            source_id=claim_id,
            target_id=new_urn("source-record"),
            target_kind="SourceRecord",
            relation="derived_from",
            via="edge",
            edge_id=new_urn("edge"),
        ),
    )

    plan_without_edges, _ = build_export(
        crate_urn=crate_id, object_bodies=object_bodies, artifact_sizes=artifact_sizes
    )
    plan_with_edges, _ = build_export(
        crate_urn=crate_id,
        object_bodies=object_bodies,
        artifact_sizes=artifact_sizes,
        provenance_edges=edges,
    )

    assert plan_without_edges == plan_with_edges
    assert plan_with_edges == build_export_plan(object_bodies, artifact_sizes)


# ---------------------------------------------------------------------------
# Sanity: the metadata document round-trips through plain json (no exotic
# Python types leaking into what is supposed to be plain-JSON-LD).
# ---------------------------------------------------------------------------


def test_metadata_document_is_plain_json_serializable() -> None:
    crate_id, object_bodies, artifact_sizes = _small_fixture()
    _, metadata = build_export(
        crate_urn=crate_id, object_bodies=object_bodies, artifact_sizes=artifact_sizes
    )
    json.loads(json.dumps(metadata))
