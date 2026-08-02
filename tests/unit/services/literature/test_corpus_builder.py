"""task-packets/N1-T05.yaml AT1-AT7: the literature channel's corpus hits
``CorpusEntry`` exactly, keeps its two axes apart, refuses rather than
under-reading, and never substitutes a relation the model declined to give.

Every assertion here calls the thing it checks. AT1 in particular validates
against ``CorpusEntry`` itself rather than re-describing its fields, because
there is no JSON schema for a corpus entry — the Pydantic model is the only
definition, and a test that restated it would drift away from it silently.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from mrr.services.cli import literature_main
from mrr.services.literature.corpus_builder import (
    REQUIRED_CORPUS_FILES,
    CorpusBuildError,
    CorpusBuildRefusedError,
    LiteratureCorpusBuilder,
    arxiv_family_id,
)
from mrr.services.node_runtime.synthesis_executor import CorpusEntry
from pydantic import ValidationError

_CLAIM = (
    "Systems that automate the research cycle end to end verify their own outputs "
    "independently of the component that produced them."
)


def _sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _excerpt_text(index: int) -> str:
    return f"Abstract number {index}. " + ("It says something checkable. " * 10)


def _manifest(ids: list[str]) -> dict[str, Any]:
    return {
        "schema_version": "citations.manifest.v1",
        "citations": [
            {
                "citation_id": f"lit-{arxiv}",
                "cited_as": f"Paper {arxiv}",
                "cited_url": f"https://arxiv.org/abs/{arxiv}",
                "identifiers": {"arxiv": arxiv},
                "claimed_title": f"Paper {arxiv}",
            }
            for arxiv in ids
        ],
    }


def _snapshot(ids: list[str], *, unavailable: tuple[str, ...] = ()) -> dict[str, Any]:
    excerpts = []
    for index, arxiv in enumerate(ids):
        citation_id = f"lit-{arxiv}"
        if arxiv in unavailable:
            excerpts.append(
                {
                    "citation_id": citation_id,
                    "excerpt_available": False,
                    "excerpt_kind": "abstract",
                    "reason": "no usable summary in the resolver response",
                }
            )
            continue
        text = _excerpt_text(index)
        excerpts.append(
            {
                "citation_id": citation_id,
                "excerpt_available": True,
                "excerpt_kind": "abstract",
                "excerpt_sha256": _sha256(text),
                "excerpt_text": text,
            }
        )
    return {
        "schema_version": "source-content-snapshot.v1",
        "excerpt_kind": "abstract",
        "fetched_on": "2026-08-02",
        "manifest": "citations.manifest.json",
        "excerpts": excerpts,
    }


def _proposals(
    ids: list[str],
    *,
    relations: dict[str, str] | None = None,
    undecidable: tuple[str, ...] = (),
    omit: tuple[str, ...] = (),
) -> dict[str, Any]:
    relations = relations or {}
    proposals = []
    for arxiv in ids:
        if arxiv in omit:
            continue
        citation_id = f"lit-{arxiv}"
        if arxiv in undecidable:
            proposals.append(
                {
                    "case_id": citation_id,
                    "proposed_relation": None,
                    "rationale": "The abstract does not settle the question.",
                    "decided_by": None,
                    "tie_with": None,
                    "undecidable": True,
                    "verification_disposition": "downgraded-to-proposal",
                }
            )
            continue
        proposals.append(
            {
                "case_id": citation_id,
                "proposed_relation": relations.get(arxiv, "qualifies"),
                "rationale": f"Reason for {arxiv}, one sentence that travels verbatim.",
                "decided_by": "qualifies definition",
                "tie_with": "contextualizes",
                "undecidable": False,
                "verification_disposition": "downgraded-to-proposal",
            }
        )
    return {
        "system_id": "test-model@mb-cls-criteria-v3",
        "model_name": "test-model",
        "model_profile_id": "urn:mrr:model-profile:TESTPROFILE",
        "prompt_template_sha256": "sha256:deadbeef",
        "criteria_version": "mb-cls-criteria-v3",
        "criteria_sha256": "sha256:cafebabe",
        "proposals": proposals,
    }


@pytest.fixture
def batch(tmp_path: Path) -> dict[str, Any]:
    """A five-source batch, fully anchored, fully classified."""
    ids = ["2601.00001", "2601.00002", "2601.00003", "2601.00004", "2601.00005"]
    manifest_path = tmp_path / "citations.manifest.json"
    snapshot_path = tmp_path / "content-snapshot.json"
    proposals_path = tmp_path / "proposals.json"
    manifest_path.write_text(json.dumps(_manifest(ids)), encoding="utf-8")
    snapshot_path.write_text(json.dumps(_snapshot(ids)), encoding="utf-8")
    proposals_path.write_text(json.dumps(_proposals(ids)), encoding="utf-8")
    return {
        "ids": ids,
        "manifest": manifest_path,
        "snapshot": snapshot_path,
        "proposals": proposals_path,
        "tmp": tmp_path,
    }


def _build_entries(batch: dict[str, Any]) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    builder = LiteratureCorpusBuilder()
    citations = builder.load_manifest(batch["manifest"])
    anchored, unanchored, fetched_on = builder.load_snapshot(batch["snapshot"])
    proposals, provenance = builder.load_proposals(batch["proposals"])
    return builder.build_entries(
        citations=citations,
        anchored=anchored,
        unanchored=unanchored,
        proposals=proposals,
        provenance=provenance,
        analysis="test-evidence-relations",
        claim_type="interpretive",
        snapshot_path=str(batch["snapshot"]),
        proposals_path=str(batch["proposals"]),
        accuracy_note="Measured accuracy 0.5439 against floor 0.4211.",
        fetched_on=fetched_on,
    )


# --- AT1: the corpus hits the model exactly ---------------------------------


def test_every_entry_validates_against_corpus_entry(batch: dict[str, Any]) -> None:
    """AT1. Called, not restated: CorpusEntry is the only definition there is."""
    entries, _ = _build_entries(batch)
    assert len(entries) == 5
    for entry in entries:
        CorpusEntry.model_validate(entry)


def test_a_surplus_key_fails_under_extra_forbid(batch: dict[str, Any]) -> None:
    """AT1, negative. `extra="forbid"` is what makes "hits it exactly" mean
    something in BOTH directions; without this assertion the builder could
    grow a field nobody notices.
    """
    entries, _ = _build_entries(batch)
    entry = dict(entries[0])
    entry["evidence_strength"] = "high"
    with pytest.raises(ValidationError):
        CorpusEntry.model_validate(entry)


def test_a_missing_required_field_fails(batch: dict[str, Any]) -> None:
    """AT1, the other direction."""
    entries, _ = _build_entries(batch)
    entry = dict(entries[0])
    entry["claim_relevant_finding"] = ""
    with pytest.raises(ValidationError):
        CorpusEntry.model_validate(entry)


# --- AT2: the two axes stay apart -------------------------------------------


def test_verification_status_is_the_source_axis_not_the_relation_axis(
    batch: dict[str, Any],
) -> None:
    """AT2. An anchored excerpt is `verified` (MTH-015: "anchor a resolvable
    source") even though its relation is a downgraded model proposal. The two
    judgements are independent and the entry says so.
    """
    entries, _ = _build_entries(batch)
    for entry in entries:
        assert entry["verification_status"] == "verified"
        assert entry["unverifiable_reason"] is None
        provenance = entry["extraction"]["classification_provenance"]
        assert "Model-PROPOSED relation, not a verified finding" in provenance
        assert "downgraded-to-proposal" in provenance
        assert "0.5439" in provenance


def test_an_unanchored_source_yields_no_entry_and_is_counted(tmp_path: Path) -> None:
    """AT2. A source nobody could fetch has not been read, so it gets no
    relation — giving it one would be the fabrication this channel exists to
    avoid. It is counted rather than silently lost.
    """
    ids = ["2601.00001", "2601.00002", "2601.00003", "2601.00004"]
    manifest = tmp_path / "m.json"
    snapshot = tmp_path / "s.json"
    proposals = tmp_path / "p.json"
    manifest.write_text(json.dumps(_manifest(ids)), encoding="utf-8")
    snapshot.write_text(json.dumps(_snapshot(ids, unavailable=("2601.00002",))), encoding="utf-8")
    proposals.write_text(json.dumps(_proposals(ids)), encoding="utf-8")

    builder = LiteratureCorpusBuilder()
    anchored, unanchored, _ = builder.load_snapshot(snapshot)
    assert unanchored == ("lit-2601.00002",)
    assert "lit-2601.00002" not in anchored

    entries, _ = _build_entries(
        {"manifest": manifest, "snapshot": snapshot, "proposals": proposals}
    )
    assert len(entries) == 3
    assert all(entry["entry_id"] != "lit-2601.00002" for entry in entries)


def test_a_snapshot_that_disagrees_with_itself_is_refused(tmp_path: Path) -> None:
    """The excerpt hash is RECOMPUTED, not trusted. A corpus that published a
    hash which does not describe the bytes beside it would be worse than no
    corpus.
    """
    ids = ["2601.00001"]
    document = _snapshot(ids)
    document["excerpts"][0]["excerpt_sha256"] = "sha256:0000"
    path = tmp_path / "s.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(CorpusBuildError, match="hashes to"):
        LiteratureCorpusBuilder().load_snapshot(path)


# --- AT6: undecidable does not acquire a default ----------------------------


def test_undecidable_leaves_the_corpus_and_is_counted(tmp_path: Path) -> None:
    """AT6. `CorpusEntry.evidence_relation` has four values and none means
    "undecidable". Mapping it onto `contextualizes` would manufacture a
    reading the model expressly refused to give.
    """
    ids = ["2601.00001", "2601.00002", "2601.00003", "2601.00004"]
    manifest = tmp_path / "m.json"
    snapshot = tmp_path / "s.json"
    proposals = tmp_path / "p.json"
    manifest.write_text(json.dumps(_manifest(ids)), encoding="utf-8")
    snapshot.write_text(json.dumps(_snapshot(ids)), encoding="utf-8")
    proposals.write_text(json.dumps(_proposals(ids, undecidable=("2601.00003",))), encoding="utf-8")

    entries, undecidable = _build_entries(
        {"manifest": manifest, "snapshot": snapshot, "proposals": proposals}
    )
    assert undecidable == ("lit-2601.00003",)
    assert len(entries) == 3
    assert all(entry["entry_id"] != "lit-2601.00003" for entry in entries)


def test_a_proposal_gap_refuses_rather_than_guessing(tmp_path: Path) -> None:
    """An anchored source with no proposal means the artefact does not match
    the batch it claims to classify. Building anyway would mix read entries
    with guessed ones.
    """
    ids = ["2601.00001", "2601.00002"]
    manifest = tmp_path / "m.json"
    snapshot = tmp_path / "s.json"
    proposals = tmp_path / "p.json"
    manifest.write_text(json.dumps(_manifest(ids)), encoding="utf-8")
    snapshot.write_text(json.dumps(_snapshot(ids)), encoding="utf-8")
    proposals.write_text(json.dumps(_proposals(ids, omit=("2601.00002",))), encoding="utf-8")

    with pytest.raises(CorpusBuildRefusedError, match="no proposal covers"):
        _build_entries({"manifest": manifest, "snapshot": snapshot, "proposals": proposals})


# --- source families --------------------------------------------------------


def test_two_versions_of_one_paper_are_one_family() -> None:
    """MTH-015 forbids counting copies as independent evidence, and the
    independence calculation downstream trusts this field without re-deriving
    it.
    """
    assert arxiv_family_id("2601.00001v3") == "2601.00001"
    assert arxiv_family_id("2601.00001") == "2601.00001"


def test_entries_carry_the_version_stripped_family(batch: dict[str, Any]) -> None:
    entries, _ = _build_entries(batch)
    for entry in entries:
        assert entry["source_family_id"] == entry["identifiers"]["repository_id"]


# --- the blind commission ---------------------------------------------------


def test_the_commission_carries_no_relation_field(batch: dict[str, Any], tmp_path: Path) -> None:
    """Blindness is structural: the union of case keys is exactly the union
    the gold commission carries, and none of them can hold an answer.
    """
    criteria = tmp_path / "criteria.json"
    criteria.write_text(
        json.dumps({"criteria_version": "mb-cls-criteria-v3", "criteria": {}}), encoding="utf-8"
    )
    builder = LiteratureCorpusBuilder()
    citations = builder.load_manifest(batch["manifest"])
    anchored, _, _ = builder.load_snapshot(batch["snapshot"])
    document = builder.build_commission(
        citations=citations,
        anchored=anchored,
        criteria_path=criteria,
        claim_text=_CLAIM,
        batch="test-batch",
    )
    keys: set[str] = set()
    for case in document["cases"]:
        keys |= set(case)
    assert keys == {
        "case_id",
        "claim_text",
        "excerpt",
        "excerpt_sha256",
        "source_identifiers",
        "source_url",
        "title",
    }
    forbidden = {"expected_relation", "expected_rationale", "decided_by", "tie_with", "undecidable"}
    assert not (keys & forbidden)


# --- AT3 / AT4 / AT7: the CLI ----------------------------------------------


def _build_args(batch: dict[str, Any], out: Path, **overrides: Any) -> Any:
    parser = literature_main.build_parser()
    argv = [
        "build",
        "--manifest",
        str(batch["manifest"]),
        "--snapshot",
        str(batch["snapshot"]),
        "--proposals",
        str(batch["proposals"]),
        "--batch",
        "test-batch",
        "--claim-text",
        _CLAIM,
        "--output-dir",
        str(out),
    ]
    for flag, value in overrides.items():
        argv += [f"--{flag.replace('_', '-')}", str(value)]
    return parser.parse_args(argv)


def test_build_writes_all_five_required_files(batch: dict[str, Any], tmp_path: Path) -> None:
    """AT4, first half: the directory research-run.yml would accept."""
    out = tmp_path / "corpus"
    assert literature_main.run_build_command(_build_args(batch, out)) == 0
    for name in REQUIRED_CORPUS_FILES:
        assert (out / name).is_file(), name
    entries = json.loads((out / "corpus-entries.json").read_text())
    assert len(entries) == 5
    for entry in entries:
        CorpusEntry.model_validate(entry)


def test_research_run_selection_logic_finds_the_new_batch(
    batch: dict[str, Any], tmp_path: Path
) -> None:
    """AT4, second half: research-run.yml's OWN test, re-executed — a
    directory counts as a stated question exactly when all five files are
    present and it is not in the answered register.
    """
    corpora = tmp_path / "corpora"
    corpora.mkdir()
    out = corpora / "test-batch"
    assert literature_main.run_build_command(_build_args(batch, out)) == 0

    answered: set[str] = set()
    pending = [
        directory.name
        for directory in sorted(corpora.iterdir())
        if directory.is_dir()
        and not [f for f in REQUIRED_CORPUS_FILES if not (directory / f).is_file()]
        and directory.name not in answered
    ]
    assert pending == ["test-batch"]


def test_a_batch_below_the_kill_condition_refuses_and_writes_nothing(
    tmp_path: Path,
) -> None:
    """AT3. The shortfall is diagnosable now; two nights later it is an
    `insufficient_evidence` verdict whose cause nobody can see.
    """
    ids = ["2601.00001", "2601.00002"]
    manifest = tmp_path / "m.json"
    snapshot = tmp_path / "s.json"
    proposals = tmp_path / "p.json"
    manifest.write_text(json.dumps(_manifest(ids)), encoding="utf-8")
    snapshot.write_text(json.dumps(_snapshot(ids)), encoding="utf-8")
    proposals.write_text(json.dumps(_proposals(ids)), encoding="utf-8")

    out = tmp_path / "corpus"
    args = _build_args({"manifest": manifest, "snapshot": snapshot, "proposals": proposals}, out)
    assert literature_main.run_build_command(args) == 3
    assert not out.exists()


def test_an_existing_output_directory_refuses(batch: dict[str, Any], tmp_path: Path) -> None:
    out = tmp_path / "corpus"
    out.mkdir()
    assert literature_main.run_build_command(_build_args(batch, out)) == 3


def test_two_builds_are_byte_identical_and_carry_no_clock(
    batch: dict[str, Any], tmp_path: Path
) -> None:
    """AT7. Determinism, and the invariant that no written byte is a
    wall-clock read — the reason is on the record: a hand-typed timestamp
    inside something that gates on time blocked its own gate within hours.
    """
    first = tmp_path / "a"
    second = tmp_path / "b"
    assert literature_main.run_build_command(_build_args(batch, first)) == 0
    assert literature_main.run_build_command(_build_args(batch, second)) == 0
    for name in REQUIRED_CORPUS_FILES:
        assert (first / name).read_bytes() == (second / name).read_bytes(), name

    # The only timestamp in the corpus comes from the snapshot's committed
    # fetched_on, never from a clock.
    entries = json.loads((first / "corpus-entries.json").read_text())
    for entry in entries:
        assert entry["retrieval_timestamp"] == "2026-08-02T00:00:00Z"


def test_the_run_facing_files_say_the_relations_are_proposals(
    batch: dict[str, Any], tmp_path: Path
) -> None:
    """The error rate must be visible to a reader of the RUN, not only to a
    reader of the corpus entries.
    """
    out = tmp_path / "corpus"
    assert literature_main.run_build_command(_build_args(batch, out)) == 0
    protocol = (out / "method-protocol.proposal.json").read_text()
    sidecar = (out / "protocol-parameters.sidecar.json").read_text()
    for text in (protocol, sidecar):
        assert "PROPOSAL made by" in text
        assert "0.5439" in text
        assert "0.4211" in text
