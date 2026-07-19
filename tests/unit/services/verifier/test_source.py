"""Unit tests for mrr.services.verifier.source (task-packets/E4-T05.yaml,
MRR-FR-072): validating an EvidenceAnchor by LOCAL inspection only.

Acceptance-test mapping (task-packets/E4-T05.yaml):

- "source anchor resolving against locally available artifact content ->
  'validated' -> 'pass'" -> the ``*_validated`` tests below.
- "a cited source NOT locally available / anchor unresolved ->
  unverified_source_access -> 'inconclusive', NEVER 'pass' (section 4.8
  acceptance)" -> the ``*_unvalidated`` tests.
- "an anchor pointing to absent content -> 'invalid' -> 'fail'" -> the
  ``*_invalid`` tests.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from mrr.contracts.evidence_anchor import (
    ComputationalSelector,
    EvidenceAnchor,
    TextLocator,
)
from mrr.crypto.canonical import JSONValue
from mrr.crypto.hashing import content_hash
from mrr.domain.identity import new_urn
from mrr.services.verifier.source import (
    LocalComputationalArtifact,
    LocalTextArtifact,
    validate_computational_anchor,
    validate_evidence_anchor,
    validate_text_anchor,
)

_PRACTICE_ID = new_urn("practice")
_AGENT_ID = new_urn("agent-role")
_FULL_TEXT = "Alpha reported a beta increase across the gamma cohort."
_SNAPSHOT_HASH = content_hash(_FULL_TEXT.encode("utf-8"))
_FRAGMENT = _FULL_TEXT[17:32]  # "beta increase a"
_FRAGMENT_HASH = content_hash(_FRAGMENT.encode("utf-8"))


def _base_anchor(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "id": new_urn("evidence-anchor"),
        "api_version": "mrr/v1alpha1",
        "kind": "EvidenceAnchor",
        "practice_id": _PRACTICE_ID,
        "revision": 1,
        "created_at": datetime.now(UTC),
        "created_by": _AGENT_ID,
        "content_hash": "sha256:" + "a" * 64,
        "relation": "supports",
        "extraction_method": "manual review",
        "extractor_id": _AGENT_ID,
        "anchor_validation_status": "unvalidated",
        "transformation_chain": [],
    }
    data.update(overrides)
    return data


def _text_anchor(**overrides: object) -> EvidenceAnchor:
    data = _base_anchor(
        anchor_kind="text",
        source_record_id=new_urn("source-record"),
        snapshot_hash=_SNAPSHOT_HASH,
        locator=None,
        quoted_fragment_hash=None,
    )
    data.update(overrides)
    return EvidenceAnchor.model_validate(data)


def _computational_anchor(**overrides: object) -> EvidenceAnchor:
    data = _base_anchor(
        anchor_kind="computational",
        run_id=new_urn("run"),
        recomputation_status="reproduced",
        selector=None,
    )
    data.update(overrides)
    return EvidenceAnchor.model_validate(data)


# ---------------------------------------------------------------------------
# Text anchors: unvalidated (source not locally available).
# ---------------------------------------------------------------------------


def test_text_anchor_with_no_local_artifact_is_unvalidated() -> None:
    anchor = _text_anchor()
    outcome = validate_text_anchor(anchor, local_artifact=None)
    assert outcome.anchor_validation_status == "unvalidated"
    assert outcome.source_access_outcome == "unverified_source_access"


def test_text_anchor_with_mismatched_snapshot_hash_is_unvalidated() -> None:
    anchor = _text_anchor()
    stale_artifact = LocalTextArtifact(full_text="this is not the anchored snapshot at all")
    outcome = validate_text_anchor(anchor, local_artifact=stale_artifact)
    assert outcome.anchor_validation_status == "unvalidated"
    assert outcome.source_access_outcome == "unverified_source_access"


def test_text_anchor_declaring_anchor_unavailable_reason_is_unvalidated_regardless() -> None:
    anchor = _text_anchor(
        snapshot_hash=None,
        quoted_fragment_hash=None,
        anchor_unavailable_reason="source behind an authentication wall this run cannot pass",
    )
    outcome = validate_text_anchor(anchor, local_artifact=LocalTextArtifact(full_text=_FULL_TEXT))
    assert outcome.anchor_validation_status == "unvalidated"
    assert outcome.source_access_outcome == "unverified_source_access"


# ---------------------------------------------------------------------------
# Text anchors: validated.
# ---------------------------------------------------------------------------


def test_text_anchor_resolves_via_char_offsets_and_is_validated() -> None:
    anchor = _text_anchor(
        locator=TextLocator(char_start=17, char_end=32),
        quoted_fragment_hash=_FRAGMENT_HASH,
    )
    outcome = validate_text_anchor(anchor, local_artifact=LocalTextArtifact(full_text=_FULL_TEXT))
    assert outcome.anchor_validation_status == "validated"
    assert outcome.source_access_outcome == "verified"


def test_text_anchor_resolves_via_line_offsets_and_is_validated() -> None:
    multi_line_text = "line one\nline two\nline three\n"
    fragment = "line two\nline three"
    anchor = _text_anchor(
        snapshot_hash=content_hash(multi_line_text.encode("utf-8")),
        locator=TextLocator(line_start=2, line_end=3),
        quoted_fragment_hash=content_hash(fragment.encode("utf-8")),
    )
    outcome = validate_text_anchor(
        anchor, local_artifact=LocalTextArtifact(full_text=multi_line_text)
    )
    assert outcome.anchor_validation_status == "validated"


def test_text_anchor_with_symbolic_locator_and_matching_resolved_fragment_is_validated() -> None:
    anchor = _text_anchor(
        locator=TextLocator(page=3, paragraph=2),
        quoted_fragment_hash=_FRAGMENT_HASH,
    )
    outcome = validate_text_anchor(
        anchor,
        local_artifact=LocalTextArtifact(full_text=_FULL_TEXT, resolved_fragment=_FRAGMENT),
    )
    assert outcome.anchor_validation_status == "validated"


def test_text_anchor_with_no_quoted_fragment_hash_is_validated_by_snapshot_alone() -> None:
    anchor = _text_anchor(locator=None, quoted_fragment_hash=None)
    outcome = validate_text_anchor(anchor, local_artifact=LocalTextArtifact(full_text=_FULL_TEXT))
    assert outcome.anchor_validation_status == "validated"


# ---------------------------------------------------------------------------
# Text anchors: invalid (content present, referenced location absent).
# ---------------------------------------------------------------------------


def test_text_anchor_with_out_of_bounds_char_offsets_is_invalid() -> None:
    anchor = _text_anchor(locator=TextLocator(char_start=0, char_end=10_000))
    outcome = validate_text_anchor(anchor, local_artifact=LocalTextArtifact(full_text=_FULL_TEXT))
    assert outcome.anchor_validation_status == "invalid"
    assert outcome.source_access_outcome == "verified"


def test_text_anchor_with_mismatched_quoted_fragment_hash_is_invalid() -> None:
    anchor = _text_anchor(
        locator=TextLocator(char_start=17, char_end=32),
        quoted_fragment_hash="sha256:" + "f" * 64,
    )
    outcome = validate_text_anchor(anchor, local_artifact=LocalTextArtifact(full_text=_FULL_TEXT))
    assert outcome.anchor_validation_status == "invalid"


def test_text_anchor_with_symbolic_locator_and_no_resolved_fragment_is_invalid() -> None:
    anchor = _text_anchor(locator=TextLocator(page=99))
    outcome = validate_text_anchor(
        anchor,
        local_artifact=LocalTextArtifact(full_text=_FULL_TEXT, resolved_fragment=None),
    )
    assert outcome.anchor_validation_status == "invalid"


def test_text_anchor_with_fabricated_resolved_fragment_is_invalid() -> None:
    """A resolved_fragment claimed by the caller that is not even a
    substring of the available content is never trusted blindly.
    """
    anchor = _text_anchor(locator=TextLocator(page=1))
    outcome = validate_text_anchor(
        anchor,
        local_artifact=LocalTextArtifact(
            full_text=_FULL_TEXT, resolved_fragment="this text does not appear anywhere above"
        ),
    )
    assert outcome.anchor_validation_status == "invalid"


# ---------------------------------------------------------------------------
# Wrong-kind dispatch guard.
# ---------------------------------------------------------------------------


def test_validate_text_anchor_rejects_a_computational_anchor() -> None:
    anchor = _computational_anchor()
    with pytest.raises(ValueError, match="anchor_kind"):
        validate_text_anchor(anchor, local_artifact=None)


def test_validate_computational_anchor_rejects_a_text_anchor() -> None:
    anchor = _text_anchor()
    with pytest.raises(ValueError, match="anchor_kind"):
        validate_computational_anchor(anchor, local_artifact=None)


# ---------------------------------------------------------------------------
# Computational anchors.
# ---------------------------------------------------------------------------


def test_computational_anchor_with_no_local_artifact_is_unvalidated() -> None:
    anchor = _computational_anchor()
    outcome = validate_computational_anchor(anchor, local_artifact=None)
    assert outcome.anchor_validation_status == "unvalidated"
    assert outcome.source_access_outcome == "unverified_source_access"


def test_computational_anchor_json_pointer_resolves_and_is_validated() -> None:
    document = {"rows": [{"value": 10}, {"value": 42}]}
    anchor = _computational_anchor(selector=ComputationalSelector(json_pointer="/rows/1/value"))
    outcome = validate_computational_anchor(
        anchor, local_artifact=LocalComputationalArtifact(document=document)
    )
    assert outcome.anchor_validation_status == "validated"
    assert outcome.source_access_outcome == "verified"


def test_computational_anchor_json_pointer_missing_is_invalid() -> None:
    document = {"rows": [{"value": 10}]}
    anchor = _computational_anchor(selector=ComputationalSelector(json_pointer="/rows/5/value"))
    outcome = validate_computational_anchor(
        anchor, local_artifact=LocalComputationalArtifact(document=document)
    )
    assert outcome.anchor_validation_status == "invalid"
    assert outcome.source_access_outcome == "verified"


def test_computational_anchor_resolved_value_contained_in_document_is_validated() -> None:
    document: JSONValue = {"table": [{"row": "A", "column": 5}, {"row": "B", "column": 9}]}
    anchor = _computational_anchor(
        selector=ComputationalSelector(table="table", column="column", row="B")
    )
    outcome = validate_computational_anchor(
        anchor,
        local_artifact=LocalComputationalArtifact(document=document, resolved_value=9),
    )
    assert outcome.anchor_validation_status == "validated"


def test_computational_anchor_resolved_value_not_in_document_is_invalid() -> None:
    document: JSONValue = {"table": [{"row": "A", "column": 5}]}
    anchor = _computational_anchor(selector=ComputationalSelector(query="select column from t"))
    outcome = validate_computational_anchor(
        anchor,
        local_artifact=LocalComputationalArtifact(document=document, resolved_value=999),
    )
    assert outcome.anchor_validation_status == "invalid"


def test_computational_anchor_with_no_resolved_value_and_no_pointer_is_invalid() -> None:
    document: JSONValue = {"table": [{"row": "A", "column": 5}]}
    anchor = _computational_anchor(selector=ComputationalSelector(notebook_cell="cell-3"))
    outcome = validate_computational_anchor(
        anchor, local_artifact=LocalComputationalArtifact(document=document)
    )
    assert outcome.anchor_validation_status == "invalid"


def test_computational_anchor_with_anchor_unavailable_reason_is_unvalidated_regardless() -> None:
    anchor = _computational_anchor(
        run_id=None,
        recomputation_status=None,
        anchor_unavailable_reason="run output was never sealed into an EvidenceCrate",
    )
    outcome = validate_computational_anchor(
        anchor, local_artifact=LocalComputationalArtifact(document={"anything": True})
    )
    assert outcome.anchor_validation_status == "unvalidated"


# ---------------------------------------------------------------------------
# Dispatcher.
# ---------------------------------------------------------------------------


def test_validate_evidence_anchor_dispatches_by_anchor_kind() -> None:
    text_anchor = _text_anchor(locator=None, quoted_fragment_hash=None)
    computational_anchor = _computational_anchor(selector=ComputationalSelector(json_pointer="/x"))

    text_outcome = validate_evidence_anchor(
        text_anchor, local_text_artifact=LocalTextArtifact(full_text=_FULL_TEXT)
    )
    computational_outcome = validate_evidence_anchor(
        computational_anchor,
        local_computational_artifact=LocalComputationalArtifact(document={"x": 1}),
    )

    assert text_outcome.anchor_validation_status == "validated"
    assert computational_outcome.anchor_validation_status == "validated"


# ---------------------------------------------------------------------------
# No network client anywhere in this module.
# ---------------------------------------------------------------------------


def test_source_module_imports_no_network_client() -> None:
    import ast
    from pathlib import Path

    module_path = (
        Path(__file__).resolve().parents[4]
        / "services"
        / "control_plane"
        / "mrr"
        / "services"
        / "verifier"
        / "source.py"
    )
    tree = ast.parse(module_path.read_text())
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_roots.add(node.module.split(".")[0])
    assert imported_roots.isdisjoint({"httpx", "requests", "urllib3", "aiohttp", "socket"})
