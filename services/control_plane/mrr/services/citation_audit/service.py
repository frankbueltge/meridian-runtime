"""``CitationAuditService`` (task-packets/N2-T01.yaml R4): the read-only,
NO-NETWORK, NO-DATABASE application-layer service that composes
``mrr.domain.citation_audit``'s pure classification core into a
``mrr.domain.citation_audit_report.CitationAuditReport``, over a committed
citation manifest and its committed resolution snapshot.

--- This service opens no database connection and no network connection -----

Mirrors ``mrr.services.validation.service.ValidationService``'s own "no
database" discipline, one step further: the resolution snapshot this service
reads is itself the ALREADY-COMPLETED result of the one network round trip
this audit ever needed (arXiv API / Crossref REST, task-packets/N2-T01.yaml
derived_decisions (b)) — run once, at derivation, and committed to the
repository. This service never calls out to either API; it only reads the
two given filesystem paths (``--manifest``, ``--snapshot``).

--- Typed refusals: two kinds, two exit codes at the CLI ---------------------

:class:`CitationAuditInputError` covers every "this input cannot even be
read as data" failure — a missing file, invalid UTF-8, unparseable JSON, or
a document whose top-level shape does not match what this service expects
(mirrors ``mrr.services.validation.service.AnalysisSetFileError``;
``mrr.services.cli.citation_audit_main`` maps this to exit 2). Every OTHER
typed error here — ``mrr.domain.citation_audit.MissingResolutionError`` — is
a REFUSAL about the DATA's own internal consistency (a manifest citation
with no matching snapshot resolution), not about file I/O
(``citation_audit_main`` maps this to exit 3, mirroring ``validation_main``'s
own refusal bucket).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mrr.domain.citation_audit import (
    CitationEntry,
    CitationResolution,
    classify_citations,
)
from mrr.domain.citation_audit_report import CitationAuditReport, build_citation_audit_report
from mrr.domain.exceptions import DomainError


class CitationAuditInputError(DomainError):
    """Raised when ``--manifest`` or ``--snapshot`` cannot even be read as
    data — missing, unreadable, not valid UTF-8, not valid JSON, or the
    wrong top-level shape. Carries ``path`` and a human-readable ``detail``;
    mapped to exit 2 (MRR-NFR-012 "dependency unavailable") at the CLI,
    never exit 3 — this is not a refusal about the DATA's own consistency,
    it is "this input does not exist as usable data at all".
    """

    def __init__(self, path: Path, detail: str) -> None:
        self.path = path
        self.detail = detail
        super().__init__(f"{path}: {detail}")


def _read_json_document(path: Path) -> tuple[Any, bytes]:
    """Read ``path`` as bytes, decode as UTF-8, and parse as JSON — returns
    ``(document, raw_bytes)`` so a caller needing the exact file bytes (for a
    content hash) never has to re-encode a decoded string, which could drift
    from the original bytes on disk.
    """
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise CitationAuditInputError(path, f"cannot read file ({exc})") from exc
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CitationAuditInputError(path, f"not valid UTF-8 ({exc})") from exc
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CitationAuditInputError(path, f"not valid JSON ({exc})") from exc
    return document, raw_bytes


def _require_mapping(value: Any, *, path: Path, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CitationAuditInputError(
            path, f"{what} must be a JSON object, got {type(value).__name__}"
        )
    return value


def _require_key(document: Mapping[str, Any], key: str, *, path: Path) -> Any:
    if key not in document:
        raise CitationAuditInputError(path, f"missing required key {key!r}")
    return document[key]


def _parse_manifest(
    document: Mapping[str, Any], *, path: Path
) -> tuple[str, tuple[CitationEntry, ...]]:
    audit_target = str(_require_key(document, "audit_target", path=path))
    raw_citations = _require_key(document, "citations", path=path)
    if not isinstance(raw_citations, list):
        raise CitationAuditInputError(path, "'citations' must be a JSON array")

    entries: list[CitationEntry] = []
    for raw_entry in raw_citations:
        entry_map = _require_mapping(raw_entry, path=path, what="a citations[] element")
        citation_id = str(_require_key(entry_map, "citation_id", path=path))
        cited_as = str(_require_key(entry_map, "cited_as", path=path))
        cited_url = str(_require_key(entry_map, "cited_url", path=path))
        identifiers = _require_mapping(
            _require_key(entry_map, "identifiers", path=path),
            path=path,
            what=f"citations[{citation_id!r}].identifiers",
        )
        arxiv_id = identifiers.get("arxiv")
        doi = identifiers.get("doi")
        claimed_title = entry_map.get("claimed_title")
        entries.append(
            CitationEntry(
                citation_id=citation_id,
                cited_as=cited_as,
                cited_url=cited_url,
                arxiv_id=str(arxiv_id) if arxiv_id is not None else None,
                doi=str(doi) if doi is not None else None,
                claimed_title=str(claimed_title) if claimed_title is not None else None,
            )
        )
    return audit_target, tuple(entries)


def _parse_snapshot(document: Mapping[str, Any], *, path: Path) -> tuple[CitationResolution, ...]:
    raw_resolutions = _require_key(document, "resolutions", path=path)
    if not isinstance(raw_resolutions, list):
        raise CitationAuditInputError(path, "'resolutions' must be a JSON array")

    resolutions: list[CitationResolution] = []
    for raw_resolution in raw_resolutions:
        resolution_map = _require_mapping(raw_resolution, path=path, what="a resolutions[] element")
        citation_id = str(_require_key(resolution_map, "citation_id", path=path))
        resolved = _require_key(resolution_map, "resolved", path=path)
        if not isinstance(resolved, bool):
            raise CitationAuditInputError(
                path, f"resolutions[{citation_id!r}].resolved must be a JSON boolean"
            )
        resolved_title = resolution_map.get("resolved_title")
        unverifiable = resolution_map.get("unverifiable", False)
        if not isinstance(unverifiable, bool):
            raise CitationAuditInputError(
                path, f"resolutions[{citation_id!r}].unverifiable must be a JSON boolean"
            )
        resolutions.append(
            CitationResolution(
                citation_id=citation_id,
                resolved=resolved,
                resolved_title=str(resolved_title) if resolved_title is not None else None,
                unverifiable=unverifiable,
            )
        )
    return tuple(resolutions)


class CitationAuditService:
    """docs/design/2026-07-24-n2-derivation.md's N2-T01 architecture
    section: loads the committed manifest + resolution snapshot, classifies
    every citation via the ``mrr.domain.citation_audit`` pure core, and
    builds the ``mrr.domain.citation_audit_report.CitationAuditReport``. See
    the module docstring for the full design rationale — above all, that
    this class opens no database connection and no network connection.
    """

    def build_report(self, manifest_path: Path, snapshot_path: Path) -> CitationAuditReport:
        """Build the full :class:`CitationAuditReport` for the citation
        manifest at ``manifest_path`` against the resolution snapshot at
        ``snapshot_path``.

        Raises:
            CitationAuditInputError: either path is missing, unreadable, not
                valid UTF-8/JSON, or has the wrong top-level shape.
            mrr.domain.citation_audit.MissingResolutionError: a manifest
                citation has no matching resolution in the snapshot.
        """
        manifest_document, _manifest_bytes = _read_json_document(manifest_path)
        manifest_mapping = _require_mapping(
            manifest_document, path=manifest_path, what="the manifest"
        )
        audit_target, entries = _parse_manifest(manifest_mapping, path=manifest_path)

        snapshot_document, snapshot_bytes = _read_json_document(snapshot_path)
        snapshot_mapping = _require_mapping(
            snapshot_document, path=snapshot_path, what="the snapshot"
        )
        resolutions = _parse_snapshot(snapshot_mapping, path=snapshot_path)

        snapshot_sha256 = f"sha256:{hashlib.sha256(snapshot_bytes).hexdigest()}"

        verdicts = classify_citations(entries, resolutions)

        return build_citation_audit_report(
            audit_target=audit_target,
            manifest_path=str(manifest_path),
            snapshot_path=str(snapshot_path),
            snapshot_sha256=snapshot_sha256,
            verdicts=verdicts,
        )
