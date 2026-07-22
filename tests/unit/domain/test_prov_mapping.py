"""Unit tests for ``mrr.domain.prov_mapping`` (task-packets/E8-T02.yaml), run
entirely DB-free and I/O-free — plain mapping/dataclass fixtures, no
repository, no filesystem.

Acceptance-test mapping (task-packets/E8-T02.yaml, unit tier):

- R6 "unit tests assert the spec table rows verbatim" ->
  ``test_kind_to_prov_type_matches_the_spec_table_verbatim``.
- R6 "the derived rows" (derived_decisions (a)) ->
  ``test_kind_to_prov_type_matches_the_derived_rows``.
- R6 "the unknown-kind -> no-prov-type rule" ->
  ``test_unknown_kind_maps_to_no_prov_type``.
- R1 "the URN-entity-segment -> PROV-type fallback" ->
  ``test_urn_entity_segment_fallback_matches_the_packet_table``,
  ``test_unknown_urn_entity_segment_maps_to_no_prov_type``,
  ``test_malformed_urn_maps_to_no_prov_type``. The "executor" row carries
  the 2026-07-22 packet amendment (see the module docstring's fallback
  section for its history).
- R6 "the no-fabricated-relation rule (bodies lacking a field emit no
  property)" / AT2 -> every ``test_*_omits_*`` test below.
- R2(a)/(b)/(c)/(e) grounded relation derivation -> every
  ``test_*_relations_emits_*``/``test_group_derived_from_targets_*`` test.
- R2 preamble ("a Claim is an Entity ... gets no prov relation") ->
  ``test_prov_relations_for_object_returns_nothing_for_claim``.
- R3 "stub emission is deterministic and sorted" (the urn-collection half;
  ``mrr.domain.ro_crate``'s own tests cover stub ENTITY assembly) ->
  ``test_relation_target_urns_*``.
"""

from __future__ import annotations

from typing import Literal

from mrr.crypto.canonical import JSONValue
from mrr.domain.identity import new_urn
from mrr.domain.projection import ProvenanceEdge
from mrr.domain.prov_mapping import (
    KIND_TO_PROV_TYPE,
    PROV_USED,
    PROV_VOCAB_PREFIX,
    PROV_VOCAB_URI,
    PROV_WAS_ASSOCIATED_WITH,
    PROV_WAS_DERIVED_FROM,
    PROV_WAS_GENERATED_BY,
    URN_ENTITY_SEGMENT_TO_PROV_TYPE,
    artifact_generated_by_relation,
    derived_from_relation,
    evidence_crate_relations,
    group_derived_from_targets,
    prov_relations_for_object,
    prov_type_for_kind,
    prov_type_for_urn,
    relation_target_urns,
    run_manifest_relations,
    verification_result_relations,
)

# ---------------------------------------------------------------------------
# The kind -> PROV-type table (R1, spec + derived rows).
# ---------------------------------------------------------------------------


def test_kind_to_prov_type_matches_the_spec_table_verbatim() -> None:
    # docs/spec/02_DOMAIN_MODEL.md section 6, transcribed verbatim.
    assert prov_type_for_kind("Artifact") == "prov:Entity"
    assert prov_type_for_kind("SourceRecord") == "prov:Entity"
    assert prov_type_for_kind("Claim") == "prov:Entity"
    assert prov_type_for_kind("RunManifest") == "prov:Activity"
    assert prov_type_for_kind("Review") == "prov:Activity"
    assert prov_type_for_kind("CorrectionEvent") == "prov:Activity"
    assert prov_type_for_kind("Practice") == "prov:Agent"
    assert prov_type_for_kind("Node") == "prov:Agent"
    assert prov_type_for_kind("Person") == "prov:Agent"
    assert prov_type_for_kind("AgentRole") == "prov:Agent"


def test_kind_to_prov_type_matches_the_derived_rows() -> None:
    # task-packets/E8-T02.yaml derived_decisions (a), exhaustive.
    assert prov_type_for_kind("EvidenceCrate") == "prov:Entity"
    assert prov_type_for_kind("EvidenceAnchor") == "prov:Entity"
    assert prov_type_for_kind("VerificationResult") == "prov:Activity"


def test_kind_to_prov_type_table_has_exactly_thirteen_rows() -> None:
    # Ten spec rows plus three derived rows — nothing extra, nothing missing.
    assert len(KIND_TO_PROV_TYPE) == 13


def test_unknown_kind_maps_to_no_prov_type() -> None:
    assert prov_type_for_kind("SomeFutureKindNoRowNamesYet") is None
    # Named in the spec table but NOT as an MRR object kind (it is the
    # producer-relation TARGET kind, "Artifact", that IS mapped above) —
    # this checks a kind string the table genuinely never names at all.
    assert prov_type_for_kind("") is None


# ---------------------------------------------------------------------------
# The URN-entity-segment fallback (R1, R3).
# ---------------------------------------------------------------------------


def test_urn_entity_segment_fallback_matches_the_packet_table() -> None:
    assert prov_type_for_urn(new_urn("person")) == "prov:Agent"
    assert prov_type_for_urn(new_urn("agent-role")) == "prov:Agent"
    assert prov_type_for_urn(new_urn("practice")) == "prov:Agent"
    assert prov_type_for_urn(new_urn("node")) == "prov:Agent"
    assert prov_type_for_urn(new_urn("executor")) == "prov:Agent"
    assert prov_type_for_urn(new_urn("run")) == "prov:Activity"
    assert prov_type_for_urn(new_urn("artifact")) == "prov:Entity"
    assert prov_type_for_urn(new_urn("source-record")) == "prov:Entity"
    assert prov_type_for_urn(new_urn("claim")) == "prov:Entity"
    assert prov_type_for_urn(new_urn("evidence-anchor")) == "prov:Entity"


def test_urn_entity_segment_fallback_table_has_exactly_ten_rows() -> None:
    # Nine original R1 rows plus the "executor" row of the 2026-07-22
    # packet amendment.
    assert len(URN_ENTITY_SEGMENT_TO_PROV_TYPE) == 10


def test_unknown_urn_entity_segment_maps_to_no_prov_type() -> None:
    # Kinds always exported (never stub-referenced) and future segments the
    # fallback table does not name stay type-less — never guessed.
    assert prov_type_for_urn(new_urn("evidence-crate")) is None
    assert prov_type_for_urn(new_urn("verification")) is None


def test_executor_segment_maps_to_agent_per_the_packet_amendment() -> None:
    # task-packets/E8-T02.yaml reviewer_resolution AMENDMENT 2026-07-22:
    # "executor" -> prov:Agent — the associated agent of the spec table's
    # own "executor/reviewer relation" row (see the module docstring's
    # fallback section for why this row arrived by amendment, not guess).
    assert prov_type_for_urn(new_urn("executor")) == "prov:Agent"


def test_malformed_urn_maps_to_no_prov_type() -> None:
    assert prov_type_for_urn("not-a-urn-at-all") is None
    assert prov_type_for_urn("urn:mrr:person:lowercase-ulid-invalid") is None


# ---------------------------------------------------------------------------
# R2(b): RunManifest relations.
# ---------------------------------------------------------------------------


def test_run_manifest_relations_emits_associated_with_and_used_when_present() -> None:
    executor_id = new_urn("executor")
    artifact_one = new_urn("artifact")
    artifact_two = new_urn("artifact")
    body: dict[str, JSONValue] = {
        "executor_id": executor_id,
        "parameters": {
            "input_artifact": artifact_one,
            "nested": {"another_artifact": artifact_two, "not_a_urn": "plain string"},
        },
    }

    relations = run_manifest_relations(body)

    assert relations[PROV_WAS_ASSOCIATED_WITH] == {"@id": executor_id}
    assert relations[PROV_USED] == [{"@id": urn} for urn in sorted([artifact_one, artifact_two])]


def test_run_manifest_relations_omits_used_when_parameters_name_no_artifact_urn() -> None:
    # This packet's own integration fixture's own real shape: RunManifest
    # .parameters <- TaskBundle.instructions, a plain operational dict.
    body: dict[str, JSONValue] = {
        "executor_id": new_urn("executor"),
        "parameters": {"operation": "percentage", "numerator": 42, "denominator": 100},
    }

    relations = run_manifest_relations(body)

    assert PROV_USED not in relations
    assert relations[PROV_WAS_ASSOCIATED_WITH] == {"@id": body["executor_id"]}


def test_run_manifest_relations_omits_associated_with_when_executor_id_absent() -> None:
    relations = run_manifest_relations({"parameters": {}})
    assert PROV_WAS_ASSOCIATED_WITH not in relations
    assert PROV_USED not in relations


def test_run_manifest_relations_on_a_wholly_empty_body_is_empty() -> None:
    assert run_manifest_relations({}) == {}


def test_run_manifest_relations_scans_lists_inside_parameters_too() -> None:
    artifact_id = new_urn("artifact")
    body = {"parameters": {"inputs": [artifact_id, "urn:mrr:claim:not-an-artifact-ulid"]}}
    relations = run_manifest_relations(body)
    assert relations[PROV_USED] == [{"@id": artifact_id}]


# ---------------------------------------------------------------------------
# R2(c): VerificationResult relations.
# ---------------------------------------------------------------------------


def test_verification_result_relations_emits_associated_with_and_used() -> None:
    reviewer_id = new_urn("person")
    target_id = new_urn("claim")
    anchor_id = new_urn("evidence-anchor")
    body = {
        "reviewer_id": reviewer_id,
        "target_id": target_id,
        "evidence_inspected": [anchor_id],
    }

    relations = verification_result_relations(body)

    assert relations[PROV_WAS_ASSOCIATED_WITH] == {"@id": reviewer_id}
    assert relations[PROV_USED] == [{"@id": urn} for urn in sorted([target_id, anchor_id])]


def test_verification_result_relations_dedupes_target_id_against_evidence_inspected() -> None:
    target_id = new_urn("claim")
    body = {
        "reviewer_id": new_urn("person"),
        "target_id": target_id,
        "evidence_inspected": [target_id],
    }

    relations = verification_result_relations(body)

    assert relations[PROV_USED] == [{"@id": target_id}]


def test_verification_result_relations_omits_used_when_no_target_or_evidence() -> None:
    # A minimal synthetic body missing the optional relation-bearing fields.
    reviewer_id = new_urn("person")
    relations = verification_result_relations({"reviewer_id": reviewer_id})
    assert PROV_USED not in relations
    assert relations[PROV_WAS_ASSOCIATED_WITH] == {"@id": reviewer_id}


def test_verification_result_relations_omits_associated_with_when_reviewer_id_absent() -> None:
    relations = verification_result_relations(
        {"target_id": new_urn("claim"), "evidence_inspected": []}
    )
    assert PROV_WAS_ASSOCIATED_WITH not in relations


def test_verification_result_relations_on_a_wholly_empty_body_is_empty() -> None:
    assert verification_result_relations({}) == {}


# ---------------------------------------------------------------------------
# R2(e): EvidenceCrate relations.
# ---------------------------------------------------------------------------


def test_evidence_crate_relations_emits_generated_by_when_run_id_present() -> None:
    run_id = new_urn("run")
    relations = evidence_crate_relations({"run_id": run_id})
    assert relations == {PROV_WAS_GENERATED_BY: {"@id": run_id}}


def test_evidence_crate_relations_omits_generated_by_when_run_id_absent() -> None:
    assert evidence_crate_relations({}) == {}


# ---------------------------------------------------------------------------
# The dispatcher, including the deliberate Claim carve-out (R2 preamble).
# ---------------------------------------------------------------------------


def test_prov_relations_for_object_routes_each_grounded_kind() -> None:
    run_id = new_urn("run")
    assert prov_relations_for_object("EvidenceCrate", {"run_id": run_id}) == {
        PROV_WAS_GENERATED_BY: {"@id": run_id}
    }
    executor_id = new_urn("executor")
    assert prov_relations_for_object("RunManifest", {"executor_id": executor_id}) == {
        PROV_WAS_ASSOCIATED_WITH: {"@id": executor_id}
    }
    reviewer_id = new_urn("person")
    assert prov_relations_for_object("VerificationResult", {"reviewer_id": reviewer_id}) == {
        PROV_WAS_ASSOCIATED_WITH: {"@id": reviewer_id}
    }


def test_prov_relations_for_object_returns_nothing_for_claim() -> None:
    # R2's own preamble: "a Claim is an Entity, so claim.proposer_id gets no
    # prov relation in this packet" — even though proposer_id looks exactly
    # like an attribution field, no relation is invented for it.
    body = {"proposer_id": new_urn("agent-role"), "kind": "Claim"}
    assert prov_relations_for_object("Claim", body) == {}


def test_prov_relations_for_object_returns_nothing_for_an_unmapped_kind() -> None:
    assert prov_relations_for_object("SourceRecord", {"anything": "at all"}) == {}


# ---------------------------------------------------------------------------
# R2(a): typed-edge derived_from -> prov:wasDerivedFrom.
# ---------------------------------------------------------------------------


def _edge(
    *,
    source_id: str,
    target_id: str,
    relation: str,
    via: Literal["edge", "field"] = "edge",
    target_kind: str = "Claim",
) -> ProvenanceEdge:
    return ProvenanceEdge(
        source_id=source_id,
        target_id=target_id,
        target_kind=target_kind,
        relation=relation,
        via=via,
        edge_id=new_urn("edge") if via == "edge" else None,
    )


def test_group_derived_from_targets_only_counts_typed_edges_not_field_references() -> None:
    source_id = new_urn("claim")
    edge_target = new_urn("source-record")
    field_target = new_urn("run")
    edges = [
        _edge(source_id=source_id, target_id=edge_target, relation="derived_from", via="edge"),
        _edge(source_id=source_id, target_id=field_target, relation="derived_from", via="field"),
    ]

    grouped = group_derived_from_targets(edges)

    assert grouped == {source_id: (edge_target,)}


def test_group_derived_from_targets_ignores_non_derived_from_edge_types() -> None:
    source_id = new_urn("claim")
    edges = [_edge(source_id=source_id, target_id=new_urn("evidence-anchor"), relation="supports")]
    assert group_derived_from_targets(edges) == {}


def test_group_derived_from_targets_sorts_and_dedupes_targets_per_source() -> None:
    source_id = new_urn("claim")
    target_a, target_b = sorted([new_urn("source-record"), new_urn("source-record")])
    edges = [
        _edge(source_id=source_id, target_id=target_b, relation="derived_from"),
        _edge(source_id=source_id, target_id=target_a, relation="derived_from"),
        _edge(source_id=source_id, target_id=target_a, relation="derived_from"),  # duplicate
    ]

    grouped = group_derived_from_targets(edges)

    assert grouped == {source_id: (target_a, target_b)}


def test_derived_from_relation_omits_property_when_no_targets() -> None:
    assert derived_from_relation(()) == {}


def test_derived_from_relation_wraps_targets_as_a_sorted_id_ref_list() -> None:
    urn_a, urn_b = sorted([new_urn("claim"), new_urn("claim")])
    assert derived_from_relation([urn_b, urn_a]) == {
        PROV_WAS_DERIVED_FROM: [{"@id": urn_a}, {"@id": urn_b}]
    }


# ---------------------------------------------------------------------------
# R2(d): artifact File entities -> prov:wasGeneratedBy.
# ---------------------------------------------------------------------------


def test_artifact_generated_by_relation_emits_when_run_id_present() -> None:
    run_id = new_urn("run")
    assert artifact_generated_by_relation(run_id) == {PROV_WAS_GENERATED_BY: {"@id": run_id}}


def test_artifact_generated_by_relation_omits_property_when_run_id_missing() -> None:
    assert artifact_generated_by_relation(None) == {}
    assert artifact_generated_by_relation("") == {}


# ---------------------------------------------------------------------------
# R3: which urns does a built relations dict reference?
# ---------------------------------------------------------------------------


def test_relation_target_urns_extracts_single_and_list_valued_refs() -> None:
    single = new_urn("executor")
    listed_one = new_urn("claim")
    listed_two = new_urn("evidence-anchor")
    relations: dict[str, JSONValue] = {
        PROV_WAS_ASSOCIATED_WITH: {"@id": single},
        PROV_USED: [{"@id": listed_one}, {"@id": listed_two}],
    }
    assert relation_target_urns(relations) == frozenset({single, listed_one, listed_two})


def test_relation_target_urns_ignores_non_prov_keys() -> None:
    relations = {"mrr:kind": "Claim", "@type": "mrr:Claim"}
    assert relation_target_urns(relations) == frozenset()


def test_relation_target_urns_on_an_empty_dict_is_empty() -> None:
    assert relation_target_urns({}) == frozenset()


# ---------------------------------------------------------------------------
# The prov: vocabulary constants themselves.
# ---------------------------------------------------------------------------


def test_prov_vocabulary_constants() -> None:
    assert PROV_VOCAB_PREFIX == "prov"
    assert PROV_VOCAB_URI == "http://www.w3.org/ns/prov#"
    assert PROV_WAS_DERIVED_FROM == "prov:wasDerivedFrom"
    assert PROV_WAS_ASSOCIATED_WITH == "prov:wasAssociatedWith"
    assert PROV_USED == "prov:used"
    assert PROV_WAS_GENERATED_BY == "prov:wasGeneratedBy"
