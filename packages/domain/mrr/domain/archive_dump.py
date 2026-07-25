"""Pure, no-IO, stdlib-only parser for the ``objects`` table's postgres COPY
block inside a committed archive dump (task-packets/N2-T02b.yaml R1). Takes
ALREADY-READ dump text and returns typed rows — reading the dump file is the
SERVICE's job (``mrr.services.anchoring_integrity.service
.AnchoringIntegrityService``), never this module's, mirroring
``mrr.domain.citation_audit``'s own "takes already-loaded values" precedent.

--- Why a generic ``objects`` table at all -----------------------------------

docs/design/2026-07-25-n2-t02-derivation.md's fact-lock: there are no
``source_records``/``evidence_anchors`` tables. Persistence is generic — one
``objects`` table (``id, revision, api_version, kind, practice_id,
created_at, created_by, content_hash, supersedes, labels, body``), with the
domain object's own JSONB in ``body`` — exported by ``pg_dump`` as a postgres
COPY text-format block. This module locates and parses exactly that block.

--- Strict by design: column-name indexing, never fixed positions -----------

The COPY header (``COPY <schema>.objects (<columns>) FROM stdin;``) is the
SOLE source of truth for field order — this module indexes every row by the
column NAMES parsed from that header, never by a hard-coded position, so a
reordered header (task-packets/N2-T02b.yaml R7: "column-name indexing proven
by reordering the header columns and getting the same result") still parses
correctly. Every deviation is a typed :class:`ArchiveDumpParseError` naming
what was expected and where (task-packets/N2-T02b.yaml derived_decisions
(e)): no ``objects`` COPY block at all, a required column absent from the
header, a data row whose field count differs from the header's, or a body
that is not valid JSON. No row is ever silently skipped and no missing field
is ever silently defaulted — a lenient parser that skipped an unexpected row
would under-report exactly the integrity failures this audit exists to find.

--- Postgres COPY text-format escaping, unescaped exactly ------------------

:func:`_unescape_copy_text` reverses exactly the four escapes postgres COPY
TO ever emits in text format for the byte values this table's columns can
hold: ``\\t``, ``\\n``, ``\\r``, ``\\\\``. The NULL marker ``\\N`` is a
WHOLE-FIELD marker (never a within-field escape) and maps to Python ``None``
— never to the two-character literal string ``"\\N"``.

--- Two layers: generic rows, then the derived typed views ------------------

:func:`parse_objects_copy_block` returns every row as a generic, frozen
:class:`ArchivedObject` (``object_id``, ``kind``, ``body``) — ALL rows, of
EVERY kind, in COPY order, including every historical revision of an object
that has more than one (this table has no notion of "latest revision only";
the acceptance oracle at docs/design/2026-07-25-n2-t02-derivation.md was
computed the identical way — over every row, e.g. a Claim's 4 rows across 2
revisions in ``mrr_k1t04_real_run_v2`` each contribute their own
``evidence_relations``/``counterevidence_relations`` to the 45-reference
total, not just its latest revision's 17).

:func:`extract_source_records`/:func:`extract_evidence_anchors`/
:func:`extract_claims` are the "derived, typed views the integrity core
consumes" (task-packets/N2-T02b.yaml R1): they filter :class:`ArchivedObject`
by ``kind`` and pull the specific JSON keys
``mrr.domain.anchoring_integrity``'s pure functions need, raising
:class:`ArchiveDumpParseError` (never a ``KeyError``/``TypeError``) if an
expected key is absent or the wrong shape. ``mrr.domain.anchoring_integrity``
itself never parses JSON or touches ``ArchivedObject.body`` directly.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: The columns :class:`ArchivedObject` cannot be built without. Every OTHER
#: column the real dumps declare (``revision``, ``api_version``,
#: ``practice_id``, ``created_at``, ``created_by``, ``content_hash``,
#: ``supersedes``, ``labels``) is still counted for the row's field-count
#: check, but this module has no use for their values.
_REQUIRED_COLUMNS: tuple[str, ...] = ("id", "kind", "body")

#: The postgres COPY header line for the ``objects`` table, e.g.
#: ``COPY mrr_k1t04_real_run_v2.objects (id, revision, ..., body) FROM
#: stdin;`` — schema-qualified, columns in whatever order the dump declares.
_COPY_HEADER_PATTERN = re.compile(
    r"^COPY\s+(?P<schema>[A-Za-z_][A-Za-z0-9_]*)\.objects\s*\((?P<columns>[^)]*)\)\s*FROM\s+stdin;\s*$",
    re.MULTILINE,
)

#: The exact terminator line postgres COPY TO emits after the last data row.
_TERMINATOR_PATTERN = re.compile(r"^\\\.$", re.MULTILINE)

#: The whole-field postgres NULL marker — never a within-field escape.
_NULL_MARKER = "\\N"


class ArchiveDumpParseError(Exception):
    """Base class for every typed refusal this module raises — always
    naming what was expected and where (task-packets/N2-T02b.yaml R1), never
    a silent skip or a defaulted field.
    """


@dataclass(frozen=True, slots=True)
class ArchivedObject:
    """One row of the ``objects`` table's COPY block — generic across every
    ``kind`` the dump declares (task-packets/N2-T02b.yaml R1). ``body`` is
    always a parsed JSON object (never ``None``, never a bare JSON scalar or
    array); a row whose ``body`` column is postgres NULL or fails to parse
    as a JSON object raises :class:`ArchiveDumpParseError` before this
    dataclass is ever constructed.
    """

    object_id: str
    kind: str
    body: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class SourceRecordRow:
    """The derived, typed view of one ``SourceRecord`` object body
    (task-packets/N2-T02b.yaml R1) — exactly the two fields
    ``mrr.domain.anchoring_integrity``'s coverage checks and this report's
    "named unanchored sources" rendering need.
    """

    source_record_id: str
    title: str


@dataclass(frozen=True, slots=True)
class EvidenceAnchorRow:
    """The derived, typed view of one ``EvidenceAnchor`` object body
    (task-packets/N2-T02b.yaml R1): its own id and the ``SourceRecord`` id it
    declares itself anchored to (``EvidenceAnchor.source_record_id`` in the
    real body — the reference ``mrr.domain.anchoring_integrity
    .check_anchor_links`` resolves).
    """

    anchor_id: str
    source_record_id: str


@dataclass(frozen=True, slots=True)
class ClaimRow:
    """The derived, typed view of one ``Claim`` object body (task-packets/
    N2-T02b.yaml R1): its own id and BOTH relation lists that declare
    ``EvidenceAnchor`` references — ``evidence_relations`` AND
    ``counterevidence_relations`` (task-packets: "Claim->anchor references
    come from each Claim's evidence_relations AND counterevidence_relations")
    — each an ordered tuple of anchor ids, exactly as declared in the body,
    never deduplicated (duplicate/repeated references are preserved so the
    reference COUNT this module's caller reports matches the real body).
    """

    claim_id: str
    evidence_relations: tuple[str, ...]
    counterevidence_relations: tuple[str, ...]


def _unescape_copy_text(raw: str) -> str:
    """Reverse postgres COPY TO's own text-format escaping for the four
    escapes it ever emits for this table's column values: ``\\t``, ``\\n``,
    ``\\r``, ``\\\\`` (task-packets/N2-T02b.yaml R1). Processed left to
    right in a single pass so an escaped backslash followed by a literal
    ``t``/``n``/``r`` (e.g. the four raw characters ``\\\\t``, meaning one
    literal backslash then a literal ``t``) is never misread as ``\\t``
    (one escaped tab character).
    """
    out: list[str] = []
    i = 0
    length = len(raw)
    while i < length:
        char = raw[i]
        if char == "\\" and i + 1 < length:
            nxt = raw[i + 1]
            if nxt == "t":
                out.append("\t")
                i += 2
                continue
            if nxt == "n":
                out.append("\n")
                i += 2
                continue
            if nxt == "r":
                out.append("\r")
                i += 2
                continue
            if nxt == "\\":
                out.append("\\")
                i += 2
                continue
        out.append(char)
        i += 1
    return "".join(out)


def _decode_field(raw: str) -> str | None:
    """A single tab-delimited field's raw COPY text: the whole-field NULL
    marker ``\\N`` maps to ``None`` (task-packets/N2-T02b.yaml R1: "The NULL
    marker \\N maps to None, never to the literal string '\\N'"); anything
    else is unescaped via :func:`_unescape_copy_text`.
    """
    if raw == _NULL_MARKER:
        return None
    return _unescape_copy_text(raw)


def parse_objects_copy_block(dump_text: str) -> tuple[ArchivedObject, ...]:
    """Locate the ``objects`` table's postgres COPY block in ``dump_text``
    and parse every data row into a generic :class:`ArchivedObject`, in COPY
    order (task-packets/N2-T02b.yaml R1). Pure — ``dump_text`` is already-
    read text; no file I/O happens here.

    Raises:
        ArchiveDumpParseError: no ``COPY <schema>.objects (...) FROM
            stdin;`` header line is found; the header is missing a required
            column (``id``, ``kind``, or ``body``); the block has no
            terminator line (a lone ``\\.``) after its header; a data row's
            field count does not match the header's column count; or a
            row's ``body`` column is postgres NULL, not valid JSON, or valid
            JSON that is not itself a JSON object.
    """
    header_match = _COPY_HEADER_PATTERN.search(dump_text)
    if header_match is None:
        raise ArchiveDumpParseError(
            "no 'objects' COPY block found — expected a line matching "
            "'COPY <schema>.objects (<columns>) FROM stdin;'"
        )

    columns = [column.strip() for column in header_match.group("columns").split(",")]

    missing_columns = [column for column in _REQUIRED_COLUMNS if column not in columns]
    if missing_columns:
        raise ArchiveDumpParseError(
            f"'objects' COPY header is missing required column(s) {missing_columns!r}; "
            f"header declares {columns!r}"
        )

    body_start = header_match.end()
    if dump_text[body_start : body_start + 2] == "\r\n":
        body_start += 2
    elif dump_text[body_start : body_start + 1] == "\n":
        body_start += 1

    terminator_match = _TERMINATOR_PATTERN.search(dump_text, body_start)
    if terminator_match is None:
        raise ArchiveDumpParseError(
            "'objects' COPY block has no terminator line ('\\.') after its header"
        )

    block_text = dump_text[body_start : terminator_match.start()]
    if block_text.endswith("\r\n"):
        block_text = block_text[:-2]
    elif block_text.endswith("\n"):
        block_text = block_text[:-1]

    data_lines = block_text.split("\n") if block_text else []

    id_index = columns.index("id")
    kind_index = columns.index("kind")
    body_index = columns.index("body")

    objects: list[ArchivedObject] = []
    for row_number, line in enumerate(data_lines, start=1):
        raw_fields = line.split("\t")
        if len(raw_fields) != len(columns):
            raise ArchiveDumpParseError(
                f"'objects' row {row_number} has {len(raw_fields)} field(s), expected "
                f"{len(columns)} (header columns: {columns!r}); row: {line!r}"
            )

        fields = [_decode_field(raw_field) for raw_field in raw_fields]

        object_id = fields[id_index]
        if object_id is None:
            raise ArchiveDumpParseError(f"'objects' row {row_number}: 'id' is NULL")

        kind = fields[kind_index]
        if kind is None:
            raise ArchiveDumpParseError(
                f"'objects' row {row_number} (id={object_id!r}): 'kind' is NULL"
            )

        raw_body = fields[body_index]
        if raw_body is None:
            raise ArchiveDumpParseError(
                f"'objects' row {row_number} (id={object_id!r}): 'body' is NULL, expected "
                "valid JSON"
            )

        try:
            parsed_body = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise ArchiveDumpParseError(
                f"'objects' row {row_number} (id={object_id!r}): 'body' is not valid JSON ({exc})"
            ) from exc

        if not isinstance(parsed_body, dict):
            raise ArchiveDumpParseError(
                f"'objects' row {row_number} (id={object_id!r}): 'body' must be a JSON "
                f"object, got {type(parsed_body).__name__}"
            )

        objects.append(ArchivedObject(object_id=object_id, kind=kind, body=parsed_body))

    return tuple(objects)


def _require_string_field(body: Mapping[str, Any], key: str, *, kind: str, object_id: str) -> str:
    value = body.get(key)
    if not isinstance(value, str):
        raise ArchiveDumpParseError(
            f"{kind} {object_id!r}: body has no string {key!r} field (got "
            f"{type(value).__name__ if key in body else 'missing key'})"
        )
    return value


def _require_string_list_field(
    body: Mapping[str, Any], key: str, *, kind: str, object_id: str
) -> tuple[str, ...]:
    value = body.get(key, [])
    if value is None:
        value = []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ArchiveDumpParseError(
            f"{kind} {object_id!r}: body's {key!r} field must be a JSON array of strings, "
            f"got {value!r}"
        )
    return tuple(value)


def extract_source_records(objects: Sequence[ArchivedObject]) -> tuple[SourceRecordRow, ...]:
    """Every ``SourceRecord`` row's derived typed view, in the same order as
    ``objects`` (a caller wanting a deterministic order sorts separately —
    mirrors this module's own "no ordering opinion baked into extraction"
    stance; ``mrr.domain.anchoring_integrity``'s pure functions do their own
    explicit sorting by primary id).
    """
    return tuple(
        SourceRecordRow(
            source_record_id=obj.object_id,
            title=_require_string_field(
                obj.body, "title", kind="SourceRecord", object_id=obj.object_id
            ),
        )
        for obj in objects
        if obj.kind == "SourceRecord"
    )


def extract_evidence_anchors(objects: Sequence[ArchivedObject]) -> tuple[EvidenceAnchorRow, ...]:
    """Every ``EvidenceAnchor`` row's derived typed view — see
    :func:`extract_source_records` for the ordering note.
    """
    return tuple(
        EvidenceAnchorRow(
            anchor_id=obj.object_id,
            source_record_id=_require_string_field(
                obj.body, "source_record_id", kind="EvidenceAnchor", object_id=obj.object_id
            ),
        )
        for obj in objects
        if obj.kind == "EvidenceAnchor"
    )


def extract_claims(objects: Sequence[ArchivedObject]) -> tuple[ClaimRow, ...]:
    """Every ``Claim`` row's derived typed view — one entry per COPY row,
    i.e. one per revision, never deduplicated by ``claim_id`` (see the
    module docstring's "two layers" section for why). See
    :func:`extract_source_records` for the ordering note.
    """
    return tuple(
        ClaimRow(
            claim_id=obj.object_id,
            evidence_relations=_require_string_list_field(
                obj.body, "evidence_relations", kind="Claim", object_id=obj.object_id
            ),
            counterevidence_relations=_require_string_list_field(
                obj.body, "counterevidence_relations", kind="Claim", object_id=obj.object_id
            ),
        )
        for obj in objects
        if obj.kind == "Claim"
    )
