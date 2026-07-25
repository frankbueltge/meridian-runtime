"""Unit tests for ``mrr.services.support_audit.service.SupportAuditService``
(task-packets/N2-T03b.yaml, unit tier). DB-free, no-network, no-model: every
fixture is a small, synthetic support-batch descriptor + claim manifest +
content snapshot written under ``tmp_path`` — the REAL committed
``corpora/research-records`` batch (all 34 claims) is exercised separately
by the acceptance tests in tests/contract/test_support_audit_report.py.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from mrr.domain.support_audit import IntegrityGateError
from mrr.services.support_audit import service as support_audit_service_module
from mrr.services.support_audit.service import SupportAuditInputError, SupportAuditService


def _sha256_of(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


_CLAIMS = {
    "schema_version": "claims.manifest.v1",
    "audit_target": "a synthetic test record",
    "anchor_window_chars": 60,
    "claims": [
        {
            "claim_id": "fig-supported",
            "citation_id": "cit-a",
            "kind": "figure",
            "tokens": ["12"],
            "anchor_terms": ["hour"],
        },
        {
            "claim_id": "fig-absent",
            "citation_id": "cit-a",
            "kind": "figure",
            "tokens": ["999"],
            "anchor_terms": ["hour"],
        },
        {
            "claim_id": "quote-verbatim",
            "citation_id": "cit-a",
            "kind": "quotation",
            "text": "runs for up to 12 hours",
        },
        {
            "claim_id": "quote-absent",
            "citation_id": "cit-a",
            "kind": "quotation",
            "text": "completely unrelated wording never in the excerpt",
        },
        {
            "claim_id": "excluded-one",
            "citation_id": "cit-a",
            "kind": "excluded",
            "exclusion_reason": "the record itself withdraws this claim",
        },
    ],
}

_SNAPSHOT = {
    "schema_version": "source-content-snapshot.v1",
    "manifest": "../citations.manifest.json",
    "fetched_on": "2026-07-25",
    "excerpt_kind": "abstract",
    "resolvers": {"arxiv": "...", "crossref": "..."},
    "note": "test snapshot",
    "excerpts": [
        {
            "citation_id": "cit-a",
            "resolver": "arxiv",
            "excerpt_kind": "abstract",
            "excerpt_available": True,
            "excerpt_text": "Kosmos runs for up to 12 hours performing cycles of analysis.",
            "excerpt_sha256": "sha256:" + "a" * 64,
            "unavailable_reason": None,
        }
    ],
}


def _write_batch(
    tmp_path: Path,
    *,
    claims: dict[str, object] | None = None,
    snapshot: dict[str, object] | None = None,
) -> Path:
    """Write a minimal, valid support-batch descriptor + its two declared
    inputs under ``tmp_path`` and return the descriptor's own path.
    """
    claims_path = tmp_path / "claims.manifest.json"
    claims_path.write_text(json.dumps(claims if claims is not None else _CLAIMS), encoding="utf-8")

    snapshot_path = tmp_path / "verification" / "content-snapshot.json"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        json.dumps(snapshot if snapshot is not None else _SNAPSHOT), encoding="utf-8"
    )

    batch_path = tmp_path / "support-batch.v1.json"
    batch_path.write_text(
        json.dumps(
            {
                "schema_version": "support-batch.v1",
                "batch_id": "synthetic-batch",
                "audit_target": "a synthetic test target",
                "inputs": {
                    "claims_manifest": {
                        "path": "claims.manifest.json",
                        "sha256": _sha256_of(claims_path),
                    },
                    "content_snapshot": {
                        "path": "verification/content-snapshot.json",
                        "sha256": _sha256_of(snapshot_path),
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return batch_path


# ---------------------------------------------------------------------------
# Happy path.
# ---------------------------------------------------------------------------


def test_build_report_happy_path_reports_all_five_claims(tmp_path: Path) -> None:
    batch_path = _write_batch(tmp_path)

    report = SupportAuditService().build_report(batch_path)

    assert report.batch_id == "synthetic-batch"
    assert report.counts.total == 5
    assert report.counts.figure_supported_in_excerpt == 1
    assert report.counts.figure_absent_from_checked_excerpt == 1
    assert report.counts.quotation_verbatim == 1
    assert report.counts.quotation_absent_from_checked_excerpt == 1
    assert report.counts.claim_excluded == 1
    assert report.counts.quotation_altered == 0
    assert report.counts.violations == 0
    assert report.counts.observations == 2


def test_batch_input_paths_resolved_relative_to_descriptor_directory_not_cwd(
    tmp_path: Path,
) -> None:
    batch_path = _write_batch(tmp_path)
    report = SupportAuditService().build_report(batch_path.resolve())
    assert report.counts.total == 5


def test_report_honesty_header_is_structurally_present(tmp_path: Path) -> None:
    batch_path = _write_batch(tmp_path)
    report = SupportAuditService().build_report(batch_path)
    assert report.presence_is_not_support is True
    assert report.checked_excerpt_is_abstract_only is True
    assert "abstract" in report.note.lower()


def test_unavailable_excerpt_in_snapshot_is_a_normal_absent_evaluation_not_an_error(
    tmp_path: Path,
) -> None:
    snapshot: dict[str, object] = {
        **_SNAPSHOT,
        "excerpts": [
            {
                "citation_id": "cit-a",
                "resolver": "arxiv",
                "excerpt_kind": "abstract",
                "excerpt_available": False,
                "excerpt_text": None,
                "excerpt_sha256": None,
                "unavailable_reason": "arxiv_entry_not_found",
            }
        ],
    }
    batch_path = _write_batch(tmp_path, snapshot=snapshot)

    report = SupportAuditService().build_report(batch_path)

    assert report.counts.figure_supported_in_excerpt == 0
    assert report.counts.figure_absent_from_checked_excerpt == 2
    assert report.counts.quotation_verbatim == 0
    assert report.counts.quotation_absent_from_checked_excerpt == 2


# ---------------------------------------------------------------------------
# SupportAuditInputError — file-level / structural dependency failures.
# ---------------------------------------------------------------------------


def test_missing_batch_file_raises_support_audit_input_error(tmp_path: Path) -> None:
    with pytest.raises(SupportAuditInputError):
        SupportAuditService().build_report(tmp_path / "does-not-exist.json")


def test_batch_with_invalid_json_raises_support_audit_input_error(tmp_path: Path) -> None:
    batch_path = tmp_path / "bad.json"
    batch_path.write_text("{not valid json")
    with pytest.raises(SupportAuditInputError):
        SupportAuditService().build_report(batch_path)


def test_batch_with_wrong_top_level_shape_raises_support_audit_input_error(
    tmp_path: Path,
) -> None:
    batch_path = tmp_path / "not-an-object.json"
    batch_path.write_text(json.dumps(["a", "list", "not", "an", "object"]))
    with pytest.raises(SupportAuditInputError):
        SupportAuditService().build_report(batch_path)


def test_batch_missing_required_key_raises_support_audit_input_error(tmp_path: Path) -> None:
    batch_path = _write_batch(tmp_path)
    document = json.loads(batch_path.read_text())
    del document["batch_id"]
    batch_path.write_text(json.dumps(document))
    with pytest.raises(SupportAuditInputError):
        SupportAuditService().build_report(batch_path)


def test_batch_missing_content_snapshot_input_declaration_raises_support_audit_input_error(
    tmp_path: Path,
) -> None:
    batch_path = _write_batch(tmp_path)
    document = json.loads(batch_path.read_text())
    del document["inputs"]["content_snapshot"]
    batch_path.write_text(json.dumps(document))
    with pytest.raises(SupportAuditInputError):
        SupportAuditService().build_report(batch_path)


def test_missing_declared_claims_manifest_file_raises_support_audit_input_error(
    tmp_path: Path,
) -> None:
    batch_path = _write_batch(tmp_path)
    (tmp_path / "claims.manifest.json").unlink()
    with pytest.raises(SupportAuditInputError):
        SupportAuditService().build_report(batch_path)


def test_claim_with_unknown_kind_raises_support_audit_input_error(tmp_path: Path) -> None:
    claims = {
        **_CLAIMS,
        "claims": [
            {"claim_id": "mystery", "citation_id": "cit-a", "kind": "not-a-real-kind"},
        ],
    }
    batch_path = _write_batch(tmp_path, claims=claims)
    with pytest.raises(SupportAuditInputError):
        SupportAuditService().build_report(batch_path)


def test_claim_citation_id_absent_from_snapshot_raises_support_audit_input_error(
    tmp_path: Path,
) -> None:
    """A structural mismatch between the two committed inputs — never
    silently treated as "excerpt unavailable" (that legitimate case is
    ``excerpt_available: false`` on a PRESENT snapshot entry, exercised
    separately above).
    """
    claims = {
        **_CLAIMS,
        "claims": [
            {
                "claim_id": "orphan",
                "citation_id": "cit-does-not-exist-in-snapshot",
                "kind": "figure",
                "tokens": ["1"],
                "anchor_terms": ["x"],
            },
        ],
    }
    batch_path = _write_batch(tmp_path, claims=claims)
    with pytest.raises(SupportAuditInputError, match="cit-does-not-exist-in-snapshot"):
        SupportAuditService().build_report(batch_path)


# ---------------------------------------------------------------------------
# IntegrityGateError — the fail-closed gate itself.
# ---------------------------------------------------------------------------


def test_corrupted_claims_manifest_raises_integrity_gate_error_naming_the_role(
    tmp_path: Path,
) -> None:
    batch_path = _write_batch(tmp_path)
    document = json.loads(batch_path.read_text())
    document["inputs"]["claims_manifest"]["sha256"] = "sha256:" + "0" * 64  # deliberately wrong
    batch_path.write_text(json.dumps(document))

    with pytest.raises(IntegrityGateError) as excinfo:
        SupportAuditService().build_report(batch_path)
    assert excinfo.value.role == "claims_manifest"
    assert excinfo.value.declared_sha256 == "sha256:" + "0" * 64


def test_corrupted_content_snapshot_raises_integrity_gate_error_naming_the_role(
    tmp_path: Path,
) -> None:
    batch_path = _write_batch(tmp_path)
    document = json.loads(batch_path.read_text())
    document["inputs"]["content_snapshot"]["sha256"] = "sha256:" + "1" * 64
    batch_path.write_text(json.dumps(document))

    with pytest.raises(IntegrityGateError) as excinfo:
        SupportAuditService().build_report(batch_path)
    assert excinfo.value.role == "content_snapshot"


def test_fail_closed_gate_runs_before_any_claim_is_ever_evaluated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A corrupted input makes the service raise ``IntegrityGateError``, and
    ``mrr.domain.support_audit.evaluate_figure_claim`` /
    ``evaluate_quotation_claim`` are provably NOT reached — monkeypatched
    here to raise ``AssertionError`` if either were ever called, proving the
    gate runs strictly BEFORE any claim is evaluated (task-packets/
    N2-T03b.yaml acceptance_criteria: "asserts ... that NO claim was
    evaluated").
    """

    def _must_not_be_called(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "a claim-evaluation function must never be reached when the integrity gate "
            "has already failed"
        )

    monkeypatch.setattr(support_audit_service_module, "evaluate_figure_claim", _must_not_be_called)
    monkeypatch.setattr(
        support_audit_service_module, "evaluate_quotation_claim", _must_not_be_called
    )

    batch_path = _write_batch(tmp_path)
    document = json.loads(batch_path.read_text())
    document["inputs"]["claims_manifest"]["sha256"] = "sha256:" + "0" * 64
    batch_path.write_text(json.dumps(document))

    with pytest.raises(IntegrityGateError):
        SupportAuditService().build_report(batch_path)
    # An AssertionError from either monkeypatched function would have
    # propagated as AssertionError, not IntegrityGateError, if the gate had
    # not short-circuited first — pytest.raises above already proves it did.


def test_clean_gate_does_reach_claim_evaluation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mirror image of the fail-closed test above: with a CLEAN gate,
    the same monkeypatched ``evaluate_figure_claim`` IS reached (and its
    ``AssertionError`` propagates unmodified) — proving the earlier test's
    ``IntegrityGateError`` really was caused by the gate short-circuiting,
    not by some other reason the evaluator was never called.
    """

    def _must_be_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("reached, as expected, with a clean gate")

    monkeypatch.setattr(support_audit_service_module, "evaluate_figure_claim", _must_be_called)

    batch_path = _write_batch(tmp_path)  # anchors are clean, untouched

    with pytest.raises(AssertionError, match="reached, as expected"):
        SupportAuditService().build_report(batch_path)
