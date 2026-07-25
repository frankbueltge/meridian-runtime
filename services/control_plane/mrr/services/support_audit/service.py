"""``SupportAuditService`` (task-packets/N2-T03b.yaml): the read-only,
NO-NETWORK, NO-DATABASE, MODEL-FREE application-layer service that parses a
committed support-batch descriptor, integrity-verifies its two declared
inputs fail-closed via ``mrr.domain.support_audit``'s pure gate, and — only
once that gate is clean — parses the claim manifest and the content
snapshot and evaluates every claim via ``mrr.domain.support_audit`` to
assemble a ``mrr.domain.support_audit_report.SupportAuditReport``.

--- This service opens no database connection and no network connection ----

``sqlalchemy`` is never imported anywhere in this module, and neither is
``urllib`` or any HTTP client. Its own only I/O is reading the committed
descriptor and the two files it names, all from the local filesystem.

--- The gate is strictly BEFORE either input is ever parsed as domain data --

:meth:`SupportAuditService.build_report` reads and hashes BOTH declared
inputs' bytes FIRST, runs ``mrr.domain.support_audit.check_and_gate`` over
BOTH of them, and ONLY THEN parses either one's JSON content — a mismatch on
EITHER input raises :class:`mrr.domain.support_audit.IntegrityGateError`
before ``json.loads`` is ever called on ANY input's bytes, so a caller can
prove neither file was parsed (e.g. by monkeypatching ``json.loads`` to
raise ``AssertionError`` and observing ``IntegrityGateError`` instead —
task-packets/N2-T03b.yaml acceptance_criteria: "a test corrupts one input's
bytes and asserts ... that NO claim was evaluated").

--- Typed refusals: two kinds, two outcomes at the CLI -----------------------

:class:`SupportAuditInputError` covers every "this input cannot even be
read as data" failure — a missing/unreadable descriptor or declared input
file, invalid UTF-8, unparseable JSON, the wrong top-level shape, or a
claim manifest entry whose ``citation_id`` has no corresponding entry in the
content snapshot at all (a structural mismatch between the two committed
inputs, never silently treated as "excerpt unavailable" — that legitimate
case is instead ``excerpt_available: false`` INSIDE a present snapshot
entry, which this service maps to ``excerpt_text=None`` and hands to the
domain layer as a normal absent-excerpt evaluation, not an error). Mirrors
``mrr.services.anchoring_integrity.service.AnchoringIntegrityInputError``;
``mrr.services.cli.support_audit_main`` maps this to exit 2. Every OTHER
typed error here — ``mrr.domain.support_audit.IntegrityGateError`` (a
declared-vs-actual sha256 mismatch) — is a REFUSAL about the DATA's own
integrity, not about file I/O (``support_audit_main`` maps it to exit 3).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mrr.domain.exceptions import DomainError
from mrr.domain.support_audit import (
    AnchorCheckResult,
    BatchInputDeclaration,
    SupportBatch,
    SupportBatchRole,
    build_exclusion_verdict,
    check_anchor,
    check_and_gate,
    evaluate_figure_claim,
    evaluate_quotation_claim,
)
from mrr.domain.support_audit_report import SupportAuditReport, build_support_audit_report


class SupportAuditInputError(DomainError):
    """Raised when the ``--batch`` descriptor, or one of its two declared
    inputs (the claim manifest, the content snapshot), cannot even be read
    as data — missing, unreadable, not valid UTF-8, not valid JSON, the
    wrong top-level shape, or a claim whose ``citation_id`` has no matching
    entry in the content snapshot at all. Carries ``path`` and a human-
    readable ``detail``; mapped to exit 2 at the CLI, never exit 3 — this is
    not a refusal about the DATA's own integrity, it is "this input does not
    exist as usable data at all".
    """

    def __init__(self, path: Path, detail: str) -> None:
        self.path = path
        self.detail = detail
        super().__init__(f"{path}: {detail}")


def _read_json_document(path: Path) -> Any:
    """Read ``path`` as bytes, decode as UTF-8, and parse as JSON — mirrors
    ``mrr.services.anchoring_integrity.service._read_json_document``'s
    identical three-stage typed-failure discipline.
    """
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise SupportAuditInputError(path, f"cannot read file ({exc})") from exc
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SupportAuditInputError(path, f"not valid UTF-8 ({exc})") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SupportAuditInputError(path, f"not valid JSON ({exc})") from exc


def _require_mapping(value: Any, *, path: Path, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SupportAuditInputError(
            path, f"{what} must be a JSON object, got {type(value).__name__}"
        )
    return value


def _require_key(document: Mapping[str, Any], key: str, *, path: Path) -> Any:
    if key not in document:
        raise SupportAuditInputError(path, f"missing required key {key!r}")
    return document[key]


def _hash_bytes(raw_bytes: bytes) -> str:
    """The ``"sha256:<hex>"`` digest of already-read bytes — mirrors
    ``mrr.services.anchoring_integrity.service._hash_bytes`` exactly.
    """
    return f"sha256:{hashlib.sha256(raw_bytes).hexdigest()}"


# ---------------------------------------------------------------------------
# The support-batch descriptor (schema_version, batch_id, audit_target,
# inputs.{claims_manifest,content_snapshot}).
# ---------------------------------------------------------------------------


def _parse_batch_input_declaration(
    document: Mapping[str, Any], *, role: SupportBatchRole, path: Path
) -> BatchInputDeclaration:
    entry = _require_mapping(
        _require_key(document, role, path=path), path=path, what=f"inputs.{role}"
    )
    declared_path = str(_require_key(entry, "path", path=path))
    declared_sha256 = str(_require_key(entry, "sha256", path=path))
    return BatchInputDeclaration(role=role, path=declared_path, declared_sha256=declared_sha256)


def _parse_batch_descriptor(document: Mapping[str, Any], *, path: Path) -> SupportBatch:
    schema_version = str(_require_key(document, "schema_version", path=path))
    batch_id = str(_require_key(document, "batch_id", path=path))
    audit_target = str(_require_key(document, "audit_target", path=path))
    inputs = _require_mapping(
        _require_key(document, "inputs", path=path), path=path, what="'inputs'"
    )
    claims_manifest = _parse_batch_input_declaration(inputs, role="claims_manifest", path=path)
    content_snapshot = _parse_batch_input_declaration(inputs, role="content_snapshot", path=path)
    return SupportBatch(
        schema_version=schema_version,
        batch_id=batch_id,
        audit_target=audit_target,
        claims_manifest=claims_manifest,
        content_snapshot=content_snapshot,
    )


# ---------------------------------------------------------------------------
# The claim manifest (corpora/research-records/claims.manifest.json shape).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ParsedFigureClaim:
    claim_id: str
    citation_id: str
    tokens: tuple[str, ...]
    anchor_terms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ParsedQuotationClaim:
    claim_id: str
    citation_id: str
    text: str


@dataclass(frozen=True, slots=True)
class _ParsedExclusion:
    claim_id: str
    citation_id: str
    exclusion_reason: str


def _require_string_array(value: Any, *, path: Path, what: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SupportAuditInputError(path, f"{what} must be a JSON array of strings")
    return tuple(value)


_ParsedClaims = tuple[
    int,
    tuple[_ParsedFigureClaim, ...],
    tuple[_ParsedQuotationClaim, ...],
    tuple[_ParsedExclusion, ...],
]


def _parse_claims_manifest(document: Mapping[str, Any], *, path: Path) -> _ParsedClaims:
    raw_window = _require_key(document, "anchor_window_chars", path=path)
    if not isinstance(raw_window, int) or isinstance(raw_window, bool):
        raise SupportAuditInputError(path, "'anchor_window_chars' must be an integer")

    raw_claims = _require_key(document, "claims", path=path)
    if not isinstance(raw_claims, list):
        raise SupportAuditInputError(path, "'claims' must be a JSON array")

    figure_claims: list[_ParsedFigureClaim] = []
    quotation_claims: list[_ParsedQuotationClaim] = []
    exclusion_claims: list[_ParsedExclusion] = []

    for index, raw_entry in enumerate(raw_claims):
        entry = _require_mapping(raw_entry, path=path, what=f"claims[{index}]")
        claim_id = str(_require_key(entry, "claim_id", path=path))
        citation_id = str(_require_key(entry, "citation_id", path=path))
        kind = entry.get("kind")

        if kind == "figure":
            tokens = _require_string_array(
                entry.get("tokens"), path=path, what=f"claims[{claim_id!r}].tokens"
            )
            anchor_terms = _require_string_array(
                entry.get("anchor_terms"), path=path, what=f"claims[{claim_id!r}].anchor_terms"
            )
            figure_claims.append(
                _ParsedFigureClaim(
                    claim_id=claim_id,
                    citation_id=citation_id,
                    tokens=tokens,
                    anchor_terms=anchor_terms,
                )
            )
        elif kind == "quotation":
            text = entry.get("text")
            if not isinstance(text, str):
                raise SupportAuditInputError(path, f"claims[{claim_id!r}].text must be a string")
            quotation_claims.append(
                _ParsedQuotationClaim(claim_id=claim_id, citation_id=citation_id, text=text)
            )
        elif kind == "excluded":
            reason = entry.get("exclusion_reason")
            if not isinstance(reason, str):
                raise SupportAuditInputError(
                    path, f"claims[{claim_id!r}].exclusion_reason must be a string"
                )
            exclusion_claims.append(
                _ParsedExclusion(
                    claim_id=claim_id, citation_id=citation_id, exclusion_reason=reason
                )
            )
        else:
            raise SupportAuditInputError(
                path,
                f"claims[{claim_id!r}].kind {kind!r} is not one of 'figure'/'quotation'/'excluded'",
            )

    return raw_window, tuple(figure_claims), tuple(quotation_claims), tuple(exclusion_claims)


# ---------------------------------------------------------------------------
# The content snapshot (corpora/research-records/verification/
# content-snapshot.json shape, N2-T03a's output).
# ---------------------------------------------------------------------------


def _parse_content_snapshot(document: Mapping[str, Any], *, path: Path) -> dict[str, str | None]:
    raw_excerpts = _require_key(document, "excerpts", path=path)
    if not isinstance(raw_excerpts, list):
        raise SupportAuditInputError(path, "'excerpts' must be a JSON array")

    excerpt_by_citation: dict[str, str | None] = {}
    for index, raw_entry in enumerate(raw_excerpts):
        entry = _require_mapping(raw_entry, path=path, what=f"excerpts[{index}]")
        citation_id = str(_require_key(entry, "citation_id", path=path))
        available = entry.get("excerpt_available")
        if not isinstance(available, bool):
            raise SupportAuditInputError(
                path, f"excerpts[{citation_id!r}].excerpt_available must be a boolean"
            )
        if not available:
            excerpt_by_citation[citation_id] = None
            continue
        text = entry.get("excerpt_text")
        if not isinstance(text, str):
            raise SupportAuditInputError(
                path,
                f"excerpts[{citation_id!r}] declares excerpt_available true but excerpt_text "
                "is not a string",
            )
        excerpt_by_citation[citation_id] = text
    return excerpt_by_citation


def _resolve_excerpt(
    citation_id: str, excerpt_by_citation: Mapping[str, str | None], *, path: Path
) -> str | None:
    """Looks up ``citation_id`` in the parsed content snapshot. A citation_id
    entirely ABSENT from the snapshot is a structural mismatch between the
    two committed inputs (SupportAuditInputError) — never silently treated
    as "excerpt unavailable", which is a legitimate, DIFFERENT fact the
    snapshot itself already encodes via ``excerpt_available: false``
    (mapped to ``None`` by :func:`_parse_content_snapshot`, and handled as a
    normal absent-excerpt evaluation downstream, not an error).
    """
    if citation_id not in excerpt_by_citation:
        raise SupportAuditInputError(
            path, f"no excerpt entry for citation_id {citation_id!r} declared by the claim manifest"
        )
    return excerpt_by_citation[citation_id]


class SupportAuditService:
    """docs/design/2026-07-25-n2-t03-derivation.md's N2-T03b architecture
    section: parses the committed support-batch descriptor, hashes and gates
    its two declared inputs, and — only once the gate is clean — parses both
    and evaluates every claim to build the ``mrr.domain.support_audit_report
    .SupportAuditReport``. See the module docstring for the full design
    rationale — above all, that the gate runs strictly before either input
    is ever parsed as domain data.
    """

    def build_report(self, batch_path: Path) -> SupportAuditReport:
        """Build the full :class:`SupportAuditReport` for the support-batch
        descriptor at ``batch_path``. Its declared input paths are resolved
        relative to ``batch_path``'s OWN directory, never the process's
        current working directory.

        Raises:
            SupportAuditInputError: the descriptor (or one of its two
                declared inputs) is missing, unreadable, not valid
                UTF-8/JSON, has the wrong top-level shape, or a claim
                manifest entry names a citation_id with no corresponding
                content-snapshot entry at all.
            mrr.domain.support_audit.IntegrityGateError: a declared input's
                actual sha256 does not match its pinned anchor — raised
                BEFORE either input's JSON is ever parsed as domain data.
        """
        document = _read_json_document(batch_path)
        mapping = _require_mapping(document, path=batch_path, what="the support-batch descriptor")
        batch = _parse_batch_descriptor(mapping, path=batch_path)

        batch_dir = batch_path.resolve().parent
        ordered_inputs = batch.inputs()
        resolved_paths: dict[SupportBatchRole, Path] = {
            declaration.role: batch_dir / declaration.path for declaration in ordered_inputs
        }

        gate_results: list[AnchorCheckResult] = []
        raw_text_by_role: dict[SupportBatchRole, str] = {}
        for declaration in ordered_inputs:
            resolved_path = resolved_paths[declaration.role]
            try:
                raw_bytes = resolved_path.read_bytes()
            except OSError as exc:
                raise SupportAuditInputError(resolved_path, f"cannot read file ({exc})") from exc
            try:
                raw_text_by_role[declaration.role] = raw_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SupportAuditInputError(resolved_path, f"not valid UTF-8 ({exc})") from exc
            actual_sha256 = _hash_bytes(raw_bytes)
            gate_results.append(
                check_anchor(
                    declaration.role,
                    str(resolved_path),
                    declaration.declared_sha256,
                    actual_sha256,
                )
            )

        # --- The fail-closed gate: strictly before EITHER input is ever
        # parsed as domain data below. A mismatch raises here, and
        # json.loads is never called on either input's bytes (task-packets/
        # N2-T03b.yaml acceptance_criteria).
        check_and_gate(gate_results)

        claims_manifest_path = resolved_paths["claims_manifest"]
        content_snapshot_path = resolved_paths["content_snapshot"]

        try:
            claims_document: Any = json.loads(raw_text_by_role["claims_manifest"])
        except json.JSONDecodeError as exc:
            raise SupportAuditInputError(claims_manifest_path, f"not valid JSON ({exc})") from exc
        try:
            snapshot_document: Any = json.loads(raw_text_by_role["content_snapshot"])
        except json.JSONDecodeError as exc:
            raise SupportAuditInputError(content_snapshot_path, f"not valid JSON ({exc})") from exc

        claims_mapping = _require_mapping(
            claims_document, path=claims_manifest_path, what="the claim manifest"
        )
        anchor_window_chars, figure_claims, quotation_claims, exclusion_claims = (
            _parse_claims_manifest(claims_mapping, path=claims_manifest_path)
        )

        snapshot_mapping = _require_mapping(
            snapshot_document, path=content_snapshot_path, what="the content snapshot"
        )
        excerpt_by_citation = _parse_content_snapshot(snapshot_mapping, path=content_snapshot_path)

        figure_verdicts = [
            evaluate_figure_claim(
                claim_id=claim.claim_id,
                citation_id=claim.citation_id,
                tokens=claim.tokens,
                anchor_terms=claim.anchor_terms,
                anchor_window_chars=anchor_window_chars,
                excerpt_text=_resolve_excerpt(
                    claim.citation_id, excerpt_by_citation, path=content_snapshot_path
                ),
            )
            for claim in figure_claims
        ]
        quotation_verdicts = [
            evaluate_quotation_claim(
                claim_id=claim.claim_id,
                citation_id=claim.citation_id,
                quote_text=claim.text,
                excerpt_text=_resolve_excerpt(
                    claim.citation_id, excerpt_by_citation, path=content_snapshot_path
                ),
            )
            for claim in quotation_claims
        ]
        exclusion_verdicts = [
            build_exclusion_verdict(
                claim_id=claim.claim_id,
                citation_id=claim.citation_id,
                exclusion_reason=claim.exclusion_reason,
            )
            for claim in exclusion_claims
        ]

        return build_support_audit_report(
            batch_id=batch.batch_id,
            audit_target=batch.audit_target,
            figure_verdicts=figure_verdicts,
            quotation_verdicts=quotation_verdicts,
            exclusion_verdicts=exclusion_verdicts,
        )
