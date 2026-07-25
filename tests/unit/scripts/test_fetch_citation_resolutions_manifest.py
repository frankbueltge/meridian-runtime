"""``read_manifest``/``group_by_resolver`` (task-packets/N2-T02a.yaml R2):
this script's own minimal, decoupled manifest read (it imports nothing from
``mrr.*``, so it cannot reuse the frozen evaluator's parser). Covers: valid
manifests group correctly by resolver; an entry with neither ``arxiv`` nor
``doi`` is a typed refusal, never guessed at; malformed manifest documents
(missing file, bad JSON, wrong shape) are typed :class:`ManifestInputError`
refusals. No network involved anywhere in this module.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.fetch_citation_resolutions import (
    ManifestInputError,
    UnresolvableManifestEntryError,
    group_by_resolver,
    read_manifest,
)


def _write_manifest(path: Path, citations: list[dict[str, object]]) -> Path:
    path.write_text(
        json.dumps({"audit_target": "a synthetic test target", "citations": citations}),
        encoding="utf-8",
    )
    return path


def test_read_manifest_extracts_arxiv_and_doi_identifiers(tmp_path: Path) -> None:
    manifest_path = _write_manifest(
        tmp_path / "citations.manifest.json",
        [
            {"citation_id": "a", "identifiers": {"arxiv": "2511.02824"}},
            {"citation_id": "b", "identifiers": {"doi": "10.1038/s41586-026-10265-5"}},
        ],
    )

    citations = read_manifest(manifest_path)

    assert citations[0].citation_id == "a"
    assert citations[0].arxiv_id == "2511.02824"
    assert citations[0].doi is None
    assert citations[1].citation_id == "b"
    assert citations[1].doi == "10.1038/s41586-026-10265-5"
    assert citations[1].arxiv_id is None


def test_group_by_resolver_splits_arxiv_and_doi_entries(tmp_path: Path) -> None:
    manifest_path = _write_manifest(
        tmp_path / "citations.manifest.json",
        [
            {"citation_id": "a", "identifiers": {"arxiv": "2511.02824"}},
            {"citation_id": "b", "identifiers": {"doi": "10.1038/s41586-026-10265-5"}},
            {"citation_id": "c", "identifiers": {"arxiv": "2502.14297v3"}},
        ],
    )
    citations = read_manifest(manifest_path)

    arxiv_group, doi_group = group_by_resolver(citations)

    assert arxiv_group == (("a", "2511.02824"), ("c", "2502.14297v3"))
    assert doi_group == (("b", "10.1038/s41586-026-10265-5"),)


def test_entry_with_arxiv_and_doi_prefers_arxiv() -> None:
    """R2: "every entry with identifiers.arxiv goes to arXiv" — arXiv takes
    priority when both are declared (matches the frozen evaluator's own
    ``display_identifier`` precedence: arXiv, else DOI, else URL).
    """
    from scripts.fetch_citation_resolutions import ManifestCitation

    citation = ManifestCitation(citation_id="x", arxiv_id="2511.02824", doi="10.1038/x")
    arxiv_group, doi_group = group_by_resolver([citation])
    assert arxiv_group == (("x", "2511.02824"),)
    assert doi_group == ()


def test_entry_with_neither_identifier_is_a_typed_refusal_not_a_guess() -> None:
    from scripts.fetch_citation_resolutions import ManifestCitation

    citation = ManifestCitation(citation_id="mystery", arxiv_id=None, doi=None)
    with pytest.raises(UnresolvableManifestEntryError) as exc_info:
        group_by_resolver([citation])
    assert exc_info.value.citation_id == "mystery"


def test_missing_manifest_file_is_a_typed_input_error(tmp_path: Path) -> None:
    with pytest.raises(ManifestInputError):
        read_manifest(tmp_path / "does-not-exist.json")


def test_invalid_json_manifest_is_a_typed_input_error(tmp_path: Path) -> None:
    path = tmp_path / "citations.manifest.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ManifestInputError):
        read_manifest(path)


def test_manifest_with_non_list_citations_is_a_typed_input_error(tmp_path: Path) -> None:
    path = tmp_path / "citations.manifest.json"
    path.write_text(json.dumps({"citations": "not a list"}), encoding="utf-8")
    with pytest.raises(ManifestInputError):
        read_manifest(path)


def test_citation_missing_identifiers_object_is_a_typed_input_error(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path / "citations.manifest.json", [{"citation_id": "a"}])
    with pytest.raises(ManifestInputError):
        read_manifest(path)


def test_real_committed_manifest_reads_cleanly_and_groups_20_arxiv_1_doi() -> None:
    """Sanity check against the REAL committed manifest this packet audits
    (never edited by this packet) — confirms this script's own independent
    reader agrees with the derivation's fact-lock: 20 arXiv ids, 1 DOI, 21
    total, no entry with neither identifier.
    """
    repo_root = Path(__file__).resolve().parents[3]
    manifest_path = repo_root / "corpora" / "research-records" / "citations.manifest.json"
    citations = read_manifest(manifest_path)
    assert len(citations) == 21

    arxiv_group, doi_group = group_by_resolver(citations)
    assert len(arxiv_group) == 20
    assert len(doi_group) == 1
