"""Acceptance tests for task-packets/N2-T02a.yaml (contract tier, DB-free,
no-network): running the UNCHANGED, frozen ``mrr.services.citation_audit
.service.CitationAuditService`` (N2-T01) over the REAL committed
``corpora/research-records/citations.manifest.json`` and the newly committed
``corpora/research-records/verification/resolution-snapshot.json`` — the
gated fetch this packet built, run once, at
``scripts/fetch_citation_resolutions.py``.

Acceptance-test mapping:

- AT1 ("21 citations, 21 resolved, 0 not_found/title_mismatch/malformed/
  unverifiable, asserted numerically") ->
  ``test_at1_all_21_citations_resolved_by_count``,
  ``test_at1_every_citation_status_is_resolved_individually``.
- AT2 ("cross-check: the 8 citations shared with corpora/e2e-survey resolve
  to the SAME titles in both committed snapshots") ->
  ``test_at2_shared_identifiers_resolve_to_the_same_titles_in_both_snapshots``.

This module reuses the FROZEN N2-T01 evaluator by import only — it changes
no file under ``packages/**``/``services/**`` (task-packets/N2-T02a.yaml
R5).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from mrr.domain.citation_audit_report import CitationAuditReport
from mrr.services.citation_audit.service import CitationAuditService

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "corpora" / "research-records" / "citations.manifest.json"
SNAPSHOT_PATH = (
    REPO_ROOT / "corpora" / "research-records" / "verification" / "resolution-snapshot.json"
)

E2E_SURVEY_SNAPSHOT_PATH = (
    REPO_ROOT / "corpora" / "e2e-survey" / "verification" / "resolution-snapshot.json"
)

_ARXIV_VERSION_SUFFIX = re.compile(r"v\d+$")

_EXPECTED_CITATION_IDS = (
    "aar-position-paper",
    "agent-laboratory",
    "barrie-prompt-stability",
    "beel-ai-scientist-eval",
    "citation-hallucination-multi-agent",
    "cited-but-not-verified",
    "darwin-goedel-machine",
    "deeptrace",
    "gepa",
    "inspectable-ai-for-science",
    "kosmos",
    "liu-inter-prompt-reliability",
    "llm-hacking",
    "neurips-fabricated-citations",
    "pangakis-validation",
    "sakana-nature",
    "sciintbench",
    "sciintegrity-bench",
    "silicon",
    "statabench",
    "zhu-implementation-capability",
)


def _build_report() -> CitationAuditReport:
    return CitationAuditService().build_report(MANIFEST_PATH, SNAPSHOT_PATH)


def test_fixture_files_exist_and_declare_21_citations() -> None:
    """Sanity check on the committed files themselves (the manifest is
    READ-ONLY for this packet; the snapshot is this packet's own committed
    output) before asserting anything about the service's behavior over
    them.
    """
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert len(manifest["citations"]) == 21
    snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert len(snapshot["resolutions"]) == 21


def test_at1_all_21_citations_resolved_by_count() -> None:
    report = _build_report()
    assert report.summary.total == 21
    assert report.summary.resolved == 21
    assert report.summary.not_found == 0
    assert report.summary.title_mismatch == 0
    assert report.summary.unverifiable == 0
    assert report.summary.malformed == 0


def test_at1_every_citation_status_is_resolved_individually() -> None:
    report = _build_report()
    seen_ids = {row.citation_id for row in report.citations}
    assert seen_ids == set(_EXPECTED_CITATION_IDS)
    for row in report.citations:
        assert row.status == "resolved", f"{row.citation_id}: expected resolved, got {row.status}"
        assert row.resolved_title is not None


def test_at1_citations_are_ordered_by_citation_id() -> None:
    report = _build_report()
    ids = [row.citation_id for row in report.citations]
    assert ids == sorted(ids)


def test_honesty_header_names_n2_t03_not_this_packet_as_the_deferred_use_case() -> None:
    report = _build_report()
    assert report.verifies_existence_not_support is True
    assert "SUPPORT" in report.existence_note


def _arxiv_base(identifier: str) -> str:
    """``"arxiv:2502.14297v3"`` -> ``"arxiv:2502.14297"``; DOI identifiers
    pass through unchanged. Used only to pair the SAME underlying work across
    two independently transcribed manifests, one of which cites a versioned
    arXiv id and the other the unversioned base id for it.
    """
    if not identifier.startswith("arxiv:"):
        return identifier
    return "arxiv:" + _ARXIV_VERSION_SUFFIX.sub("", identifier[len("arxiv:") :])


def test_at2_shared_identifiers_resolve_to_the_same_titles_in_both_snapshots() -> None:
    """AT2: for every identifier this snapshot shares with the committed
    corpora/e2e-survey snapshot (two INDEPENDENT fetches, task-packets/
    N2-T02a.yaml derivation: "8 of the 21 are already covered by N2-T01's
    snapshot ... the overlap is a deliberate cross-check"), the resolved
    title must agree. Matched by NORMALISED identifier (base arXiv id or
    DOI), not by citation_id — two of the eight shared identifiers carry
    different citation_ids across the two manifests (the same underlying
    paper, cited independently by each record set).
    """
    research_records_snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    e2e_survey_snapshot = json.loads(E2E_SURVEY_SNAPSHOT_PATH.read_text(encoding="utf-8"))

    e2e_titles_by_base_identifier = {
        _arxiv_base(resolution["identifier"]): resolution["resolved_title"]
        for resolution in e2e_survey_snapshot["resolutions"]
    }

    matched = 0
    for resolution in research_records_snapshot["resolutions"]:
        base_identifier = _arxiv_base(resolution["identifier"])
        if base_identifier not in e2e_titles_by_base_identifier:
            continue
        matched += 1
        assert resolution["resolved_title"] == e2e_titles_by_base_identifier[base_identifier], (
            f"{resolution['citation_id']} ({resolution['identifier']}): title diverges from the "
            "e2e-survey snapshot's independently fetched resolution for the same identifier"
        )

    # The derivation's own fact-lock: exactly 8 of the 21 record citations
    # share an identifier with the e2e-survey manifest. A different count
    # here means the two manifests drifted apart from what was verified at
    # derivation — worth knowing, not something to silently tolerate.
    assert matched == 8
