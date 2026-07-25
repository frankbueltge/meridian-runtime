"""``FieldObservationService`` (task-packets/R2-T01.yaml R4): the read-only,
NO-NETWORK, NO-DATABASE application-layer service that parses a committed
observation-batch descriptor, integrity-verifies each declared input
fail-closed via ``mrr.domain.field_observation``'s pure gate, and — only
once that gate is clean — reuses the FROZEN N2 evaluator
(``mrr.services.citation_audit.service.CitationAuditService``, imported and
called unchanged, never rebuilt or re-scored) to assemble a
``mrr.domain.field_observation_report.FieldObservationReport``.

--- This service opens no database connection and no network connection -----

Mirrors ``mrr.services.citation_audit.service.CitationAuditService``'s own
"no database, no network" discipline one step further: this service's own
only I/O is reading the committed descriptor and the two committed inputs
it names, all from the local filesystem, and delegating to the equally
I/O-only ``CitationAuditService``. Nothing here ever opens a socket or a
database connection.

--- The gate is strictly BEFORE the evaluator (task-packets/R2-T01.yaml
    invariant) -------------------------------------------------------------

:meth:`FieldObservationService.build_report` reads and hashes BOTH declared
inputs, runs ``mrr.domain.field_observation.check_and_gate`` over both
results, and ONLY THEN constructs ``CitationAuditService`` — a mismatch
raises :class:`mrr.domain.field_observation.IntegrityGateError` before that
constructor call is ever reached, so a caller can prove the evaluator was
never invoked (e.g. by monkeypatching ``CitationAuditService.build_report``
to raise and observing ``IntegrityGateError`` instead — task-packets/
R2-T01.yaml AT3).

--- Typed refusals: three kinds, three outcomes at the CLI ------------------

:class:`FieldObservationInputError` covers every "this input cannot even be
read as data" failure — a missing/unreadable descriptor or declared input
file, invalid UTF-8, unparseable JSON, or a document whose top-level shape
does not match what this service expects (mirrors
``mrr.services.citation_audit.service.CitationAuditInputError``;
``mrr.services.cli.field_observation_main`` maps this to exit 2). Every
OTHER typed error here —
``mrr.domain.field_observation.IntegrityGateError`` (a hash-anchor
mismatch) and ``mrr.domain.citation_audit.MissingResolutionError``
(propagated unchanged from the reused N2 evaluator, a manifest citation
with no matching snapshot resolution) — is a REFUSAL about the DATA's own
integrity or internal consistency, not about file I/O
(``field_observation_main`` maps both to exit 3).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mrr.domain.citation_audit_report import CitationAuditReport
from mrr.domain.exceptions import DomainError
from mrr.domain.field_observation import (
    AnchorCheckResult,
    BatchInput,
    BatchRole,
    ObservationBatch,
    check_anchor,
    check_and_gate,
)
from mrr.domain.field_observation_report import (
    FieldObservationReport,
    build_field_observation_report,
)
from mrr.services.citation_audit.service import CitationAuditService


class FieldObservationInputError(DomainError):
    """Raised when the ``--batch`` descriptor, or one of its declared input
    files, cannot even be read as data — missing, unreadable, not valid
    UTF-8, not valid JSON, or the wrong top-level shape. Carries ``path``
    and a human-readable ``detail``; mapped to exit 2 (MRR-NFR-012
    "dependency unavailable") at the CLI, never exit 3 — this is not a
    refusal about the DATA's own integrity, it is "this input does not
    exist as usable data at all".
    """

    def __init__(self, path: Path, detail: str) -> None:
        self.path = path
        self.detail = detail
        super().__init__(f"{path}: {detail}")


def _read_json_document(path: Path) -> Any:
    """Read ``path`` as bytes, decode as UTF-8, and parse as JSON — mirrors
    ``mrr.services.citation_audit.service._read_json_document``'s identical
    three-stage typed-failure discipline.
    """
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise FieldObservationInputError(path, f"cannot read file ({exc})") from exc
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FieldObservationInputError(path, f"not valid UTF-8 ({exc})") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise FieldObservationInputError(path, f"not valid JSON ({exc})") from exc


def _require_mapping(value: Any, *, path: Path, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise FieldObservationInputError(
            path, f"{what} must be a JSON object, got {type(value).__name__}"
        )
    return value


def _require_key(document: Mapping[str, Any], key: str, *, path: Path) -> Any:
    if key not in document:
        raise FieldObservationInputError(path, f"missing required key {key!r}")
    return document[key]


def _parse_batch_input(inputs_map: Mapping[str, Any], *, role: BatchRole, path: Path) -> BatchInput:
    raw_input = _require_key(inputs_map, role, path=path)
    input_map = _require_mapping(raw_input, path=path, what=f"inputs.{role}")
    declared_path = str(_require_key(input_map, "path", path=path))
    declared_sha256 = str(_require_key(input_map, "sha256", path=path))
    return BatchInput(role=role, path=declared_path, declared_sha256=declared_sha256)


def _parse_descriptor(document: Mapping[str, Any], *, path: Path) -> ObservationBatch:
    """Parse the descriptor's declared top-level shape (task-packets/
    R2-T01.yaml R3): ``schema_version``, ``batch_id``, ``observation_kind``,
    ``audit_target``, and an ``inputs`` object naming exactly ``manifest``
    and ``snapshot``. Any additional top-level key (e.g. the descriptor's
    own ``provenance`` prose) is simply ignored — this service only reads
    the fields R1's :class:`ObservationBatch` declares.
    """
    schema_version = str(_require_key(document, "schema_version", path=path))
    batch_id = str(_require_key(document, "batch_id", path=path))
    observation_kind = str(_require_key(document, "observation_kind", path=path))
    audit_target = str(_require_key(document, "audit_target", path=path))
    raw_inputs = _require_key(document, "inputs", path=path)
    inputs_map = _require_mapping(raw_inputs, path=path, what="'inputs'")
    manifest = _parse_batch_input(inputs_map, role="manifest", path=path)
    snapshot = _parse_batch_input(inputs_map, role="snapshot", path=path)
    return ObservationBatch(
        schema_version=schema_version,
        batch_id=batch_id,
        observation_kind=observation_kind,
        audit_target=audit_target,
        manifest=manifest,
        snapshot=snapshot,
    )


def _hash_file(path: Path) -> str:
    """Read ``path``'s bytes and return its ``"sha256:<hex>"`` digest — the
    only IO this module performs beyond reading the descriptor itself.
    Mirrors ``mrr.services.citation_audit.service.CitationAuditService
    .build_report``'s identical ``f"sha256:{hashlib.sha256(...).hexdigest()}"``
    convention.
    """
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise FieldObservationInputError(path, f"cannot read file ({exc})") from exc
    return f"sha256:{hashlib.sha256(raw_bytes).hexdigest()}"


class FieldObservationService:
    """docs/design/2026-07-25-r2-derivation.md's R2-T01 architecture
    section: parses the committed observation-batch descriptor, hashes and
    gates both declared inputs, and — only once the gate is clean — builds
    the ``mrr.domain.field_observation_report.FieldObservationReport`` over
    the reused, unchanged N2 ``CitationAuditReport``. See the module
    docstring for the full design rationale — above all, that the gate runs
    strictly before ``CitationAuditService`` is ever constructed.
    """

    def build_report(self, batch_path: Path) -> FieldObservationReport:
        """Build the full :class:`FieldObservationReport` for the
        observation-batch descriptor at ``batch_path``. Its declared input
        paths are resolved relative to ``batch_path``'s OWN directory
        (task-packets/R2-T01.yaml R3), never the process's current working
        directory — so the result is identical regardless of where this is
        invoked from.

        Raises:
            FieldObservationInputError: the descriptor (or one of its
                declared inputs) is missing, unreadable, not valid
                UTF-8/JSON, or has the wrong top-level shape.
            mrr.domain.field_observation.IntegrityGateError: a declared
                input's actual sha256 does not match its pinned anchor —
                raised BEFORE ``CitationAuditService`` is ever constructed.
            mrr.domain.citation_audit.MissingResolutionError: propagated,
                unchanged, from the reused N2 evaluator — a manifest
                citation has no matching resolution in the snapshot.
        """
        document = _read_json_document(batch_path)
        mapping = _require_mapping(
            document, path=batch_path, what="the observation-batch descriptor"
        )
        batch = _parse_descriptor(mapping, path=batch_path)

        batch_dir = batch_path.resolve().parent
        resolved_paths: dict[BatchRole, Path] = {
            batch_input.role: batch_dir / batch_input.path for batch_input in batch.inputs()
        }

        results: list[AnchorCheckResult] = []
        for batch_input in batch.inputs():
            resolved_path = resolved_paths[batch_input.role]
            actual_sha256 = _hash_file(resolved_path)
            results.append(
                check_anchor(
                    batch_input.role,
                    str(resolved_path),
                    batch_input.declared_sha256,
                    actual_sha256,
                )
            )

        # --- The fail-closed gate: strictly before the evaluator below.
        # A mismatch raises here, and CitationAuditService is never
        # constructed (task-packets/R2-T01.yaml invariant / AT3).
        check_and_gate(results)

        citation_audit: CitationAuditReport = CitationAuditService().build_report(
            resolved_paths["manifest"], resolved_paths["snapshot"]
        )

        return build_field_observation_report(
            batch_id=batch.batch_id,
            observation_kind=batch.observation_kind,
            audit_target=batch.audit_target,
            anchor_results=results,
            citation_audit=citation_audit,
        )
