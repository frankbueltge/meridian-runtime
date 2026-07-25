"""``AnchoringIntegrityService`` (task-packets/N2-T02b.yaml R5): the
read-only, NO-NETWORK, NO-DATABASE application-layer service that parses a
committed anchoring-batch descriptor, integrity-verifies each declared
archive dump fail-closed via ``mrr.domain.anchoring_integrity``'s pure gate,
and — only once that gate is clean — parses each dump via
``mrr.domain.archive_dump`` and resolves every reference via
``mrr.domain.anchoring_integrity`` to assemble a
``mrr.domain.anchoring_integrity_report.AnchoringIntegrityReport``.

--- This service opens no database connection and no network connection -----

``sqlalchemy`` is never imported anywhere in this module. Its own only I/O
is reading the committed descriptor and the archive dumps it names, all
from the local filesystem.

--- The gate is strictly BEFORE any dump is parsed (task-packets/
    N2-T02b.yaml invariant) ---------------------------------------------------

:meth:`AnchoringIntegrityService.build_report` reads and hashes EVERY
declared dump's bytes FIRST, runs ``mrr.domain.anchoring_integrity
.check_and_gate`` over ALL of them, and ONLY THEN calls
``mrr.domain.archive_dump.parse_objects_copy_block`` for any of them — a
mismatch on ANY dump raises :class:`mrr.domain.anchoring_integrity
.IntegrityGateError` before ``parse_objects_copy_block`` is ever reached for
ANY dump, so a caller can prove the parser was never invoked (e.g. by
monkeypatching ``parse_objects_copy_block`` to raise ``AssertionError`` and
observing ``IntegrityGateError`` instead — task-packets/N2-T02b.yaml AT4).

--- Typed refusals: two kinds, two outcomes at the CLI -----------------------

:class:`AnchoringIntegrityInputError` covers every "this input cannot even
be read as data" failure — a missing/unreadable descriptor or declared dump
file, invalid UTF-8, unparseable JSON, the wrong top-level shape, or an
empty ``dumps[]`` list (mirrors ``mrr.services.field_observation.service
.FieldObservationInputError``; ``mrr.services.cli.anchoring_integrity_main``
maps this to exit 2). Every OTHER typed error here —
``mrr.domain.anchoring_integrity.IntegrityGateError`` (a dump-hash mismatch)
and ``mrr.domain.archive_dump.ArchiveDumpParseError`` (a strict-parser
refusal) — is a REFUSAL about the DATA's own integrity or structure, not
about file I/O (``anchoring_integrity_main`` maps both to exit 3).
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mrr.domain.anchoring_integrity import (
    DumpAnchorCheckResult,
    DumpDeclaration,
    anchor_coverage,
    check_anchor_links,
    check_and_gate,
    check_claim_references,
    check_dump_anchor,
    source_coverage,
)
from mrr.domain.anchoring_integrity_report import (
    AnchoringIntegrityReport,
    build_anchoring_integrity_report,
    build_dump_anchoring_report,
)
from mrr.domain.archive_dump import (
    extract_claims,
    extract_evidence_anchors,
    extract_source_records,
    parse_objects_copy_block,
)
from mrr.domain.exceptions import DomainError


class AnchoringIntegrityInputError(DomainError):
    """Raised when the ``--batch`` descriptor, or one of its declared
    dumps, cannot even be read as data — missing, unreadable, not valid
    UTF-8, not valid JSON, the wrong top-level shape, or an empty
    ``dumps[]`` list. Carries ``path`` and a human-readable ``detail``;
    mapped to exit 2 (MRR-NFR-012 "dependency unavailable") at the CLI,
    never exit 3 — this is not a refusal about the DATA's own integrity, it
    is "this input does not exist as usable data at all".
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
        raise AnchoringIntegrityInputError(path, f"cannot read file ({exc})") from exc
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AnchoringIntegrityInputError(path, f"not valid UTF-8 ({exc})") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AnchoringIntegrityInputError(path, f"not valid JSON ({exc})") from exc


def _require_mapping(value: Any, *, path: Path, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AnchoringIntegrityInputError(
            path, f"{what} must be a JSON object, got {type(value).__name__}"
        )
    return value


def _require_key(document: Mapping[str, Any], key: str, *, path: Path) -> Any:
    if key not in document:
        raise AnchoringIntegrityInputError(path, f"missing required key {key!r}")
    return document[key]


def _parse_dump_declaration(entry: Any, *, index: int, path: Path) -> DumpDeclaration:
    entry_map = _require_mapping(entry, path=path, what=f"dumps[{index}]")
    schema_name = str(_require_key(entry_map, "schema_name", path=path))
    declared_path = str(_require_key(entry_map, "path", path=path))
    declared_sha256 = str(_require_key(entry_map, "sha256", path=path))
    return DumpDeclaration(
        schema_name=schema_name, path=declared_path, declared_sha256=declared_sha256
    )


class _AnchoringBatch:
    """The parsed shape of a committed anchoring-batch descriptor
    (task-packets/N2-T02b.yaml R4). Kept private to this module (unlike
    ``mrr.domain.field_observation.ObservationBatch``, which the pure
    domain layer needs for its own ``inputs()`` ordering) — descriptor
    parsing is entirely this SERVICE's job, and ``dumps`` is already
    exposed as an open, unordered list the service itself sorts before use.
    """

    __slots__ = ("schema_version", "batch_id", "observation_kind", "audit_target", "dumps")

    def __init__(
        self,
        *,
        schema_version: str,
        batch_id: str,
        observation_kind: str,
        audit_target: str,
        dumps: tuple[DumpDeclaration, ...],
    ) -> None:
        self.schema_version = schema_version
        self.batch_id = batch_id
        self.observation_kind = observation_kind
        self.audit_target = audit_target
        self.dumps = dumps

    def ordered_dumps(self) -> tuple[DumpDeclaration, ...]:
        """Every declared dump, sorted by ``schema_name`` — the ONE place
        this ordering is decided, so a caller iterating this always sees a
        deterministic order regardless of the descriptor's own JSON array
        order (mirrors ``mrr.domain.field_observation.ObservationBatch
        .inputs``'s identical role).
        """
        return tuple(sorted(self.dumps, key=lambda declaration: declaration.schema_name))


def _parse_descriptor(document: Mapping[str, Any], *, path: Path) -> _AnchoringBatch:
    """Parse the descriptor's declared top-level shape (task-packets/
    N2-T02b.yaml R4): ``schema_version``, ``batch_id``, ``observation_kind``,
    ``audit_target``, and a non-empty ``dumps`` array. Any additional
    top-level key (e.g. the descriptor's own ``provenance``/
    ``note_on_the_input_choice`` prose) is simply ignored.
    """
    schema_version = str(_require_key(document, "schema_version", path=path))
    batch_id = str(_require_key(document, "batch_id", path=path))
    observation_kind = str(_require_key(document, "observation_kind", path=path))
    audit_target = str(_require_key(document, "audit_target", path=path))
    raw_dumps = _require_key(document, "dumps", path=path)
    if not isinstance(raw_dumps, list):
        raise AnchoringIntegrityInputError(
            path, f"'dumps' must be a JSON array, got {type(raw_dumps).__name__}"
        )
    if len(raw_dumps) == 0:
        raise AnchoringIntegrityInputError(
            path, "'dumps' must declare at least one dump; got an empty list"
        )
    dumps = tuple(
        _parse_dump_declaration(entry, index=index, path=path)
        for index, entry in enumerate(raw_dumps)
    )
    return _AnchoringBatch(
        schema_version=schema_version,
        batch_id=batch_id,
        observation_kind=observation_kind,
        audit_target=audit_target,
        dumps=dumps,
    )


def _hash_bytes(raw_bytes: bytes) -> str:
    """The ``"sha256:<hex>"`` digest of already-read bytes — mirrors
    ``mrr.services.field_observation.service._hash_file``'s identical
    convention, factored to take bytes directly since this service reads
    each dump's bytes once and reuses them for both hashing and decoding.
    """
    return f"sha256:{hashlib.sha256(raw_bytes).hexdigest()}"


class AnchoringIntegrityService:
    """docs/design/2026-07-25-n2-t02-derivation.md's N2-T02b architecture
    section: parses the committed anchoring-batch descriptor, hashes and
    gates every declared dump, and — only once the gate is clean — parses
    each dump and resolves every reference to build the
    ``mrr.domain.anchoring_integrity_report.AnchoringIntegrityReport``. See
    the module docstring for the full design rationale — above all, that
    the gate runs strictly before any dump is ever parsed.
    """

    def build_report(self, batch_path: Path) -> AnchoringIntegrityReport:
        """Build the full :class:`AnchoringIntegrityReport` for the
        anchoring-batch descriptor at ``batch_path``. Its declared dump
        paths are resolved relative to ``batch_path``'s OWN directory
        (task-packets/N2-T02b.yaml R4), never the process's current working
        directory.

        Raises:
            AnchoringIntegrityInputError: the descriptor (or one of its
                declared dumps) is missing, unreadable, not valid
                UTF-8/JSON, has the wrong top-level shape, or declares an
                empty ``dumps[]`` list.
            mrr.domain.anchoring_integrity.IntegrityGateError: a declared
                dump's actual sha256 does not match its pinned anchor —
                raised BEFORE ``mrr.domain.archive_dump
                .parse_objects_copy_block`` is ever reached for ANY dump.
            mrr.domain.archive_dump.ArchiveDumpParseError: a dump's
                ``objects`` COPY block is structurally malformed — raised
                only once the gate above is already clean.
        """
        document = _read_json_document(batch_path)
        mapping = _require_mapping(document, path=batch_path, what="the anchoring-batch descriptor")
        batch = _parse_descriptor(mapping, path=batch_path)

        batch_dir = batch_path.resolve().parent
        ordered_dumps = batch.ordered_dumps()
        resolved_paths: dict[str, Path] = {
            declaration.schema_name: batch_dir / declaration.path for declaration in ordered_dumps
        }

        gate_results: list[DumpAnchorCheckResult] = []
        dump_text_by_schema: dict[str, str] = {}
        for declaration in ordered_dumps:
            resolved_path = resolved_paths[declaration.schema_name]
            try:
                raw_bytes = resolved_path.read_bytes()
            except OSError as exc:
                raise AnchoringIntegrityInputError(
                    resolved_path, f"cannot read file ({exc})"
                ) from exc
            try:
                dump_text_by_schema[declaration.schema_name] = raw_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise AnchoringIntegrityInputError(
                    resolved_path, f"not valid UTF-8 ({exc})"
                ) from exc
            actual_sha256 = _hash_bytes(raw_bytes)
            gate_results.append(
                check_dump_anchor(
                    declaration.schema_name,
                    str(resolved_path),
                    declaration.declared_sha256,
                    actual_sha256,
                )
            )

        # --- The fail-closed gate: strictly before ANY dump is parsed
        # below. A mismatch raises here, and parse_objects_copy_block is
        # never called for any dump (task-packets/N2-T02b.yaml invariant /
        # AT4).
        check_and_gate(gate_results)

        gate_results_by_schema = {result.schema_name: result for result in gate_results}

        dump_reports = []
        for declaration in ordered_dumps:
            dump_text = dump_text_by_schema[declaration.schema_name]
            objects = parse_objects_copy_block(dump_text)

            sources = extract_source_records(objects)
            anchors = extract_evidence_anchors(objects)
            claims = extract_claims(objects)

            source_record_ids = {source.source_record_id for source in sources}
            anchor_ids = {anchor.anchor_id for anchor in anchors}

            dump_reports.append(
                build_dump_anchoring_report(
                    schema_name=declaration.schema_name,
                    file_anchor=gate_results_by_schema[declaration.schema_name],
                    total_objects=len(objects),
                    object_counts_by_kind=dict(Counter(obj.kind for obj in objects)),
                    anchor_links=check_anchor_links(anchors, source_record_ids),
                    claim_references=check_claim_references(claims, anchor_ids),
                    source_coverage=source_coverage(sources, anchors),
                    anchor_coverage=anchor_coverage(anchors, claims),
                )
            )

        return build_anchoring_integrity_report(
            batch_id=batch.batch_id,
            observation_kind=batch.observation_kind,
            audit_target=batch.audit_target,
            dumps=dump_reports,
        )
