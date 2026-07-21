"""DB-free acceptance tests for the model-collapse corpus fixtures
(task-packets/K1-T04.yaml, ``corpora/model-collapse/``):

- [snapshot integrity] recomputing sha256 over the two committed atlas
  snapshot copies equals snapshot-manifest.json's own recorded values.
- [fixture validity, DB-free] corpus-entries.json validates against
  CorpusEntry for every entry with zero errors; protocol-parameters.sidecar
  .json validates against ProtocolParameters with zero errors and its
  eligibility_rules carries exactly the "supported"/"contested" keys.
- [candidate-set determinism] re-deriving the works-atlas candidate set
  (cluster membership includes 7) and the theory-atlas candidate set (the
  disclosed keyword match) directly from the two committed snapshot copies
  reproduces exactly the 15 + 3 candidates task-packets/K1-T04.yaml's own
  derived_decisions (d) names, with no manual override.
- [operationalizes-term convention] every ConceptCharterEntry.term in the
  committed proposal equals one of QuestionModel.load_bearing_terms.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mrr.contracts import ConceptCharter, MethodProtocol, QuestionModel
from mrr.domain.identity import new_urn
from mrr.services.node_runtime.synthesis_executor import CorpusEntry, ProtocolParameters

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CORPUS_DIR = _REPO_ROOT / "corpora" / "model-collapse"

_THEORY_KEYWORDS = (
    "model collapse",
    "model-collapse",
    "self-consuming",
    "self-consumption",
    "autophagy",
    "mad",
    "curse of recursion",
)

_EXPECTED_WORKS_CANDIDATE_TITLES = {
    "Ent- (non-earthly delights)",
    "Wilding AI Lab",
    "AI Delivered: The Abject",
    "En attendant le récit / Tales of Narrativelessness",
    "Errorism",
    "Hallucinations of an Artifact",
    "The Next Biennial Should Be Curated by a Machine",
    "The Sleight of the Machine",
    "V3: Model Collapse",
    "6,500 Alphabets Make a Map",
    "Cybernetics, or Ghosts?",
    "Matrix Vegetal",
    "Mythmachine",
    "The Feral: Epoch 1",
    "Thousand Lives (BOB lineage)",
}

_EXPECTED_THEORY_CANDIDATE_IDS = {
    "shumailov-curse-of-recursion",
    "alemohammad-self-consuming-generative-models-go-mad",
    "gerstgrasser-is-model-collapse-inevitable",
}


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(name: str) -> Any:
    return json.loads((_CORPUS_DIR / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# [snapshot integrity]
# ---------------------------------------------------------------------------


def test_theory_atlas_snapshot_sha256_matches_manifest() -> None:
    manifest = _load_json("snapshot-manifest.json")
    snapshot_path = _CORPUS_DIR / "theory-atlas.snapshot.json"

    assert _sha256_of(snapshot_path) == manifest["theory_atlas"]["sha256"]
    assert manifest["theory_atlas"]["sha256"] == (
        "f712ea4e9c6b9137fa180ad91e73a86d8d09862792f33174c77acd76a891e610"
    )


def test_works_atlas_snapshot_sha256_matches_manifest() -> None:
    manifest = _load_json("snapshot-manifest.json")
    snapshot_path = _CORPUS_DIR / "works-atlas.snapshot.json"

    assert _sha256_of(snapshot_path) == manifest["works_atlas"]["sha256"]
    assert manifest["works_atlas"]["sha256"] == (
        "9d14e877efb245cc04b8451734b26285a274dda0418f5131db257d2e4312d373"
    )


def test_snapshot_entry_counts_match_manifest() -> None:
    manifest = _load_json("snapshot-manifest.json")
    theory = _load_json("theory-atlas.snapshot.json")
    works = _load_json("works-atlas.snapshot.json")

    assert len(theory) == manifest["theory_atlas"]["entry_count"] == 87
    assert len(works) == manifest["works_atlas"]["entry_count"] == 214


# ---------------------------------------------------------------------------
# [fixture validity, DB-free]
# ---------------------------------------------------------------------------


def test_corpus_entries_validate_against_corpus_entry_with_zero_errors() -> None:
    entries = _load_json("corpus-entries.json")
    assert len(entries) == 18

    for entry in entries:
        CorpusEntry.model_validate(entry)


def test_corpus_entries_have_unique_entry_ids() -> None:
    entries = _load_json("corpus-entries.json")
    entry_ids = [e["entry_id"] for e in entries]
    assert len(entry_ids) == len(set(entry_ids))


def test_protocol_parameters_sidecar_validates_with_zero_errors() -> None:
    raw = _load_json("protocol-parameters.sidecar.json")
    params = ProtocolParameters.model_validate(raw)
    assert set(params.eligibility_rules.keys()) == {"supported", "contested"}


def test_question_model_concept_charter_method_protocol_proposals_construct_cleanly() -> None:
    """Body-only proposal content (no id/status/audit fields — those are
    minted at run time by establish_and_run_synthesis) constructs a fully
    valid object once merged with placeholder identity/audit fields.
    """
    now = datetime.now(UTC)

    qm_body = _load_json("question-model.proposal.json")
    QuestionModel.model_validate(
        {
            "id": new_urn("question-model"),
            "api_version": "mrr/v1alpha1",
            "kind": "QuestionModel",
            "practice_id": new_urn("practice"),
            "revision": 1,
            "created_at": now,
            "created_by": new_urn("agent-role"),
            "content_hash": "sha256:" + "0" * 64,
            "status": "draft",
            **qm_body,
        }
    )

    cc_body = _load_json("concept-charter.proposal.json")
    ConceptCharter.model_validate(
        {
            "id": new_urn("concept-charter"),
            "api_version": "mrr/v1alpha1",
            "kind": "ConceptCharter",
            "practice_id": new_urn("practice"),
            "revision": 1,
            "created_at": now,
            "created_by": new_urn("agent-role"),
            "content_hash": "sha256:" + "0" * 64,
            "status": "draft",
            **cc_body,
        }
    )

    mp_body = _load_json("method-protocol.proposal.json")
    MethodProtocol.model_validate(
        {
            "id": new_urn("method-protocol"),
            "api_version": "mrr/v1alpha1",
            "kind": "MethodProtocol",
            "practice_id": new_urn("practice"),
            "revision": 1,
            "created_at": now,
            "created_by": new_urn("agent-role"),
            "content_hash": "sha256:" + "0" * 64,
            "profile_id": new_urn("method-profile"),
            "status": "draft",
            **mp_body,
        }
    )


# ---------------------------------------------------------------------------
# [candidate-set determinism]
# ---------------------------------------------------------------------------


def test_works_atlas_cluster_7_candidate_set_reproduces_the_named_15() -> None:
    works = _load_json("works-atlas.snapshot.json")
    candidates = [w for w in works if 7 in w.get("clusters", [])]

    assert len(candidates) == 15
    assert {w["title"] for w in candidates} == _EXPECTED_WORKS_CANDIDATE_TITLES


def test_theory_atlas_keyword_match_candidate_set_reproduces_the_named_3() -> None:
    theory = _load_json("theory-atlas.snapshot.json")

    def _matches(entry: dict[str, Any]) -> bool:
        haystack = " ".join(
            [
                " ".join(entry.get("tags", [])),
                entry.get("summary", ""),
                entry.get("work", ""),
            ]
        ).lower()
        return any(keyword in haystack for keyword in _THEORY_KEYWORDS)

    candidates = [e for e in theory if _matches(e)]

    assert len(candidates) == 3
    assert {e["id"] for e in candidates} == _EXPECTED_THEORY_CANDIDATE_IDS


def test_corpus_entries_cover_exactly_the_deterministic_candidate_sets() -> None:
    """The committed corpus-entries.json is built from exactly the
    deterministic 15+3 candidate sets above — no manual addition or
    omission.
    """
    entries = _load_json("corpus-entries.json")
    theory_entry_ids = {
        e["identifiers"]["local_asset_id"]
        for e in entries
        if e["applies_to_analysis"] == "model-collapse-mechanism-theory-confirmation"
    }
    works_entry_titles = {
        e["title"]
        for e in entries
        if e["applies_to_analysis"] == "instantiation-vs-reference-classification"
    }

    assert theory_entry_ids == _EXPECTED_THEORY_CANDIDATE_IDS
    assert works_entry_titles == _EXPECTED_WORKS_CANDIDATE_TITLES


# ---------------------------------------------------------------------------
# [operationalizes-term convention]
# ---------------------------------------------------------------------------


def test_every_concept_charter_entry_term_equals_a_question_model_load_bearing_term() -> None:
    question_model = _load_json("question-model.proposal.json")
    concept_charter = _load_json("concept-charter.proposal.json")

    load_bearing_terms = set(question_model["load_bearing_terms"])
    for entry in concept_charter["entries"]:
        assert entry["term"] in load_bearing_terms
