"""Unit tests for ``mrr.domain.ro_crate`` (task-packets/E8-T01.yaml), run
entirely DB-free and I/O-free — plain mapping fixtures, no repository, no
filesystem, no ``ArtifactStore``.

Acceptance-test mapping (task-packets/E8-T01.yaml, unit/contract tier):

- naming (R1): ``test_object_relative_path_replaces_colons_with_underscores``,
  ``test_artifact_relative_path_strips_the_sha256_prefix``.
- determinism (R6, AT4's own logic reused at the unit tier): identical
  inputs yield byte-identical ``ro-crate-metadata.json`` ->
  ``test_build_export_is_deterministic_across_repeated_calls``.
- boundary: no ``prov:*`` keys anywhere (E8-T02's boundary) ->
  ``test_metadata_contains_no_prov_terms_anywhere``.
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
"""

from __future__ import annotations

import json
from typing import Any, cast

from mrr.crypto.canonical import canonicalize
from mrr.domain.identity import new_urn
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


# ---------------------------------------------------------------------------
# Boundary: no prov:* terms anywhere (E8-T02's boundary).
# ---------------------------------------------------------------------------


def _flatten_strings(value: object) -> list[str]:
    """Every string appearing anywhere in a JSON-like structure — keys and
    values alike — so the prov:* scan below cannot miss one hiding inside a
    nested list/dict.
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


def test_metadata_contains_no_prov_terms_anywhere() -> None:
    crate_id, object_bodies, artifact_sizes = _small_fixture()
    _, metadata = build_export(
        crate_urn=crate_id, object_bodies=object_bodies, artifact_sizes=artifact_sizes
    )

    every_string = _flatten_strings(metadata)
    prov_hits = [s for s in every_string if s.startswith("prov:") or s == "prov" or "prov#" in s]
    assert prov_hits == [], f"unexpected prov:* term(s) in RO-Crate metadata: {prov_hits}"


# ---------------------------------------------------------------------------
# Structure (R1).
# ---------------------------------------------------------------------------


def test_metadata_descriptor_and_root_data_entity_shape() -> None:
    crate_id, object_bodies, artifact_sizes = _small_fixture()
    _, metadata = build_export(
        crate_urn=crate_id, object_bodies=object_bodies, artifact_sizes=artifact_sizes
    )

    assert metadata["@context"] == [RO_CRATE_CONTEXT_URI, {MRR_VOCAB_PREFIX: MRR_VOCAB_URI}]
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
    assert entity["@type"] == f"{MRR_VOCAB_PREFIX}:EvidenceCrate"
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
# Sanity: the metadata document round-trips through plain json (no exotic
# Python types leaking into what is supposed to be plain-JSON-LD).
# ---------------------------------------------------------------------------


def test_metadata_document_is_plain_json_serializable() -> None:
    crate_id, object_bodies, artifact_sizes = _small_fixture()
    _, metadata = build_export(
        crate_urn=crate_id, object_bodies=object_bodies, artifact_sizes=artifact_sizes
    )
    json.loads(json.dumps(metadata))
