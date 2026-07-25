"""Unit tests for ``mrr.domain.archive_dump`` (task-packets/N2-T02b.yaml
R1/R7, unit tier). DB-free, no-network — every dump text here is a small,
hand-built synthetic postgres COPY block, never a fixture read from disk
(the REAL committed archive dumps are exercised separately, at the contract
tier, in tests/contract/test_anchoring_integrity_acceptance.py).
"""

from __future__ import annotations

import pytest
from mrr.domain.archive_dump import (
    ArchivedObject,
    ArchiveDumpParseError,
    ClaimRow,
    EvidenceAnchorRow,
    SourceRecordRow,
    _decode_field,
    _unescape_copy_text,
    extract_claims,
    extract_evidence_anchors,
    extract_source_records,
    parse_objects_copy_block,
)

_FULL_COLUMNS = (
    "id",
    "revision",
    "api_version",
    "kind",
    "practice_id",
    "created_at",
    "created_by",
    "content_hash",
    "supersedes",
    "labels",
    "body",
)


def _row(
    *,
    object_id: str = "urn:mrr:source-record:001",
    revision: str = "1",
    api_version: str = "mrr/v1alpha1",
    kind: str = "SourceRecord",
    practice_id: str = "urn:mrr:practice:001",
    created_at: str = "2026-07-21 07:36:39.430958+00",
    created_by: str = "urn:mrr:agent-role:001",
    content_hash: str = "sha256:" + "a" * 64,
    supersedes: str = r"\N",
    labels: str = "null",
    body: str = '{"id": "x"}',
) -> str:
    """One data line for the ``_FULL_COLUMNS`` order — the same shape the
    real dumps declare (task-packets/N2-T02b.yaml derivation fact-lock)."""
    return "\t".join(
        (
            object_id,
            revision,
            api_version,
            kind,
            practice_id,
            created_at,
            created_by,
            content_hash,
            supersedes,
            labels,
            body,
        )
    )


def _copy_block(
    *,
    schema: str = "mrr_test",
    columns: tuple[str, ...] = _FULL_COLUMNS,
    rows: tuple[str, ...] = (),
) -> str:
    header = f"COPY {schema}.objects ({', '.join(columns)}) FROM stdin;"
    lines = [header, *rows, r"\."]
    return "\n".join(lines) + "\n"


def _wrap_with_unrelated_blocks(objects_block: str) -> str:
    """A dump-like text with the objects block embedded among OTHER COPY
    blocks — proves the parser locates the right block rather than assuming
    it is the only or first COPY block present."""
    return (
        "COPY mrr_test.alembic_version (version_num) FROM stdin;\n"
        "abc123\n"
        "\\.\n"
        "\n"
        f"{objects_block}\n"
        "COPY mrr_test.outbox (event_id, status) FROM stdin;\n"
        "ev-1\tpending\n"
        "\\.\n"
    )


# ---------------------------------------------------------------------------
# Happy path + column-name indexing (never fixed positions).
# ---------------------------------------------------------------------------


def test_parses_a_single_row_into_an_archived_object() -> None:
    dump_text = _copy_block(rows=(_row(),))
    objects = parse_objects_copy_block(dump_text)
    assert objects == (
        ArchivedObject(
            object_id="urn:mrr:source-record:001", kind="SourceRecord", body={"id": "x"}
        ),
    )


def test_locates_the_objects_block_among_other_copy_blocks() -> None:
    dump_text = _wrap_with_unrelated_blocks(_copy_block(rows=(_row(),)))
    objects = parse_objects_copy_block(dump_text)
    assert len(objects) == 1
    assert objects[0].object_id == "urn:mrr:source-record:001"


def test_column_name_indexing_survives_a_reordered_header() -> None:
    """task-packets/N2-T02b.yaml R7: "column-name indexing proven by
    reordering the header columns and getting the same result"."""
    normal = parse_objects_copy_block(_copy_block(rows=(_row(),)))

    reordered_columns = (
        "body",
        "labels",
        "supersedes",
        "content_hash",
        "created_by",
        "created_at",
        "practice_id",
        "kind",
        "api_version",
        "revision",
        "id",
    )
    reordered_row = "\t".join(
        (
            '{"id": "x"}',
            "null",
            r"\N",
            "sha256:" + "a" * 64,
            "urn:mrr:agent-role:001",
            "2026-07-21 07:36:39.430958+00",
            "urn:mrr:practice:001",
            "SourceRecord",
            "mrr/v1alpha1",
            "1",
            "urn:mrr:source-record:001",
        )
    )
    reordered = parse_objects_copy_block(
        _copy_block(columns=reordered_columns, rows=(reordered_row,))
    )

    assert reordered == normal


def test_multiple_rows_are_all_returned_in_copy_order() -> None:
    rows = (
        _row(object_id="urn:mrr:a:1", kind="SourceRecord", body='{"title": "A"}'),
        _row(object_id="urn:mrr:b:1", kind="EvidenceAnchor", body='{"source_record_id": "x"}'),
        _row(object_id="urn:mrr:c:1", kind="Claim", body='{"evidence_relations": []}'),
    )
    objects = parse_objects_copy_block(_copy_block(rows=rows))
    assert [obj.object_id for obj in objects] == ["urn:mrr:a:1", "urn:mrr:b:1", "urn:mrr:c:1"]
    assert [obj.kind for obj in objects] == ["SourceRecord", "EvidenceAnchor", "Claim"]


def test_a_kind_with_more_than_one_revision_yields_one_archived_object_per_row() -> None:
    """The objects table has no notion of "latest revision only" — every row
    is its own :class:`ArchivedObject`, matching how the acceptance oracle
    was computed (docs/design/2026-07-25-n2-t02-derivation.md)."""
    rows = (
        _row(object_id="urn:mrr:claim:1", revision="1", kind="Claim", body='{"status": "draft"}'),
        _row(
            object_id="urn:mrr:claim:1", revision="2", kind="Claim", body='{"status": "accepted"}'
        ),
    )
    objects = parse_objects_copy_block(_copy_block(rows=rows))
    assert len(objects) == 2
    assert all(obj.object_id == "urn:mrr:claim:1" for obj in objects)
    assert [obj.body["status"] for obj in objects] == ["draft", "accepted"]


def test_empty_objects_block_yields_no_rows() -> None:
    assert parse_objects_copy_block(_copy_block(rows=())) == ()


# ---------------------------------------------------------------------------
# Postgres COPY text-format unescaping + the \N NULL marker.
# ---------------------------------------------------------------------------


def test_unescape_copy_text_reverses_tab_newline_carriage_return_and_backslash() -> None:
    """task-packets/N2-T02b.yaml R1: "unescapes postgres COPY text escapes
    (\\t, \\n, \\r, \\\\)". Tested directly against the low-level helper
    (rather than round-tripped through a JSON body) because JSON forbids a
    raw, unescaped control byte inside a string — a literal tab/newline/CR
    is never what a real dump's ``body`` column actually contains; only the
    ``\\\\`` escape is realistically exercised there (see the test right
    below, which mirrors the real archive's own escaped-quote pattern)."""
    raw = "a\\tb\\nc\\rd\\\\e"
    assert _unescape_copy_text(raw) == "a\tb\nc\rd\\e"


def test_decode_field_maps_null_marker_to_none() -> None:
    assert _decode_field("\\N") is None


def test_decode_field_unescapes_non_null_values() -> None:
    assert _decode_field("a\\tb\\\\c") == "a\tb\\c"


def test_unescapes_a_json_bodys_own_embedded_backslash_escape() -> None:
    """The realistic case, matching the real committed dumps: the body
    column's own JSON text legitimately contains an escaped quote (``\\"``),
    which is itself a literal backslash byte — postgres COPY TO escapes
    THAT backslash again for its own text format, so the raw dump line
    carries a DOUBLED backslash. Round-tripping through both layers must
    reproduce the original JSON text exactly."""
    original_json_text = '{"note": "a \\"quoted\\" b"}'
    dumped_body_field = original_json_text.replace("\\", "\\\\")
    dump_text = _copy_block(rows=(_row(body=dumped_body_field),))
    objects = parse_objects_copy_block(dump_text)
    assert objects[0].body == {"note": 'a "quoted" b'}


def test_null_marker_maps_to_none_never_to_the_literal_backslash_n_string() -> None:
    """task-packets/N2-T02b.yaml R1: "The NULL marker \\N maps to None,
    never to the literal string '\\N'". Exercised through a row whose
    nullable columns (supersedes, and here also created_by, both otherwise
    irrelevant to ArchivedObject) are \\N — the parse must succeed (proving
    \\N is recognised as a whole-field marker, not swallowed into the
    surrounding text) and the extracted id/kind/body must be exactly as
    declared, never containing a stray "\\N" substring.
    """
    dump_text = _copy_block(rows=(_row(supersedes=r"\N", created_by=r"\N"),))
    objects = parse_objects_copy_block(dump_text)
    assert objects[0].object_id == "urn:mrr:source-record:001"
    assert r"\N" not in objects[0].object_id
    assert r"\N" not in objects[0].kind


# ---------------------------------------------------------------------------
# Every named deviation is a typed refusal (task-packets/N2-T02b.yaml R1/R7).
# ---------------------------------------------------------------------------


def test_no_objects_copy_block_raises_archive_dump_parse_error() -> None:
    dump_text = "COPY mrr_test.alembic_version (version_num) FROM stdin;\nabc\n\\.\n"
    with pytest.raises(ArchiveDumpParseError, match="no 'objects' COPY block"):
        parse_objects_copy_block(dump_text)


def test_missing_required_column_id_raises_archive_dump_parse_error() -> None:
    columns = tuple(column for column in _FULL_COLUMNS if column != "id")
    dump_text = _copy_block(columns=columns, rows=())
    with pytest.raises(ArchiveDumpParseError, match="missing required column"):
        parse_objects_copy_block(dump_text)


def test_missing_required_column_kind_raises_archive_dump_parse_error() -> None:
    columns = tuple(column for column in _FULL_COLUMNS if column != "kind")
    dump_text = _copy_block(columns=columns, rows=())
    with pytest.raises(ArchiveDumpParseError, match="missing required column"):
        parse_objects_copy_block(dump_text)


def test_missing_required_column_body_raises_archive_dump_parse_error() -> None:
    columns = tuple(column for column in _FULL_COLUMNS if column != "body")
    dump_text = _copy_block(columns=columns, rows=())
    with pytest.raises(ArchiveDumpParseError, match="missing required column"):
        parse_objects_copy_block(dump_text)


def test_wrong_field_count_raises_archive_dump_parse_error() -> None:
    dump_text = _copy_block(rows=("too\tfew\tfields",))
    with pytest.raises(ArchiveDumpParseError, match="field\\(s\\), expected"):
        parse_objects_copy_block(dump_text)


def test_invalid_json_body_raises_archive_dump_parse_error() -> None:
    dump_text = _copy_block(rows=(_row(body="{not valid json"),))
    with pytest.raises(ArchiveDumpParseError, match="not valid JSON"):
        parse_objects_copy_block(dump_text)


def test_body_that_is_a_json_array_not_object_raises_archive_dump_parse_error() -> None:
    dump_text = _copy_block(rows=(_row(body="[1, 2, 3]"),))
    with pytest.raises(ArchiveDumpParseError, match="must be a JSON object"):
        parse_objects_copy_block(dump_text)


def test_null_body_raises_archive_dump_parse_error() -> None:
    dump_text = _copy_block(rows=(_row(body=r"\N"),))
    with pytest.raises(ArchiveDumpParseError, match="'body' is NULL"):
        parse_objects_copy_block(dump_text)


def test_missing_terminator_raises_archive_dump_parse_error() -> None:
    header = f"COPY mrr_test.objects ({', '.join(_FULL_COLUMNS)}) FROM stdin;"
    dump_text = header + "\n" + _row() + "\n"  # no trailing "\."
    with pytest.raises(ArchiveDumpParseError, match="no terminator line"):
        parse_objects_copy_block(dump_text)


def test_no_row_is_silently_skipped_the_bad_row_is_reported_not_dropped() -> None:
    rows = (_row(object_id="urn:mrr:a:1"), _row(object_id="urn:mrr:b:1", body="{not json"))
    with pytest.raises(ArchiveDumpParseError, match="urn:mrr:b:1"):
        parse_objects_copy_block(_copy_block(rows=rows))


# ---------------------------------------------------------------------------
# Derived typed views (SourceRecordRow / EvidenceAnchorRow / ClaimRow).
# ---------------------------------------------------------------------------


def test_extract_source_records_pulls_id_and_title() -> None:
    objects = (
        ArchivedObject(object_id="urn:mrr:sr:1", kind="SourceRecord", body={"title": "A Work"}),
        ArchivedObject(
            object_id="urn:mrr:ea:1", kind="EvidenceAnchor", body={"source_record_id": "x"}
        ),
    )
    assert extract_source_records(objects) == (
        SourceRecordRow(source_record_id="urn:mrr:sr:1", title="A Work"),
    )


def test_extract_source_records_raises_on_missing_title() -> None:
    objects = (ArchivedObject(object_id="urn:mrr:sr:1", kind="SourceRecord", body={}),)
    with pytest.raises(ArchiveDumpParseError, match="title"):
        extract_source_records(objects)


def test_extract_evidence_anchors_pulls_id_and_source_record_id() -> None:
    objects = (
        ArchivedObject(
            object_id="urn:mrr:ea:1",
            kind="EvidenceAnchor",
            body={"source_record_id": "urn:mrr:sr:1"},
        ),
    )
    assert extract_evidence_anchors(objects) == (
        EvidenceAnchorRow(anchor_id="urn:mrr:ea:1", source_record_id="urn:mrr:sr:1"),
    )


def test_extract_evidence_anchors_raises_on_missing_source_record_id() -> None:
    objects = (ArchivedObject(object_id="urn:mrr:ea:1", kind="EvidenceAnchor", body={}),)
    with pytest.raises(ArchiveDumpParseError, match="source_record_id"):
        extract_evidence_anchors(objects)


def test_extract_claims_pulls_both_relation_lists() -> None:
    objects = (
        ArchivedObject(
            object_id="urn:mrr:claim:1",
            kind="Claim",
            body={
                "evidence_relations": ["urn:mrr:ea:1"],
                "counterevidence_relations": ["urn:mrr:ea:2", "urn:mrr:ea:3"],
            },
        ),
    )
    assert extract_claims(objects) == (
        ClaimRow(
            claim_id="urn:mrr:claim:1",
            evidence_relations=("urn:mrr:ea:1",),
            counterevidence_relations=("urn:mrr:ea:2", "urn:mrr:ea:3"),
        ),
    )


def test_extract_claims_treats_absent_relation_lists_as_empty_not_an_error() -> None:
    objects = (ArchivedObject(object_id="urn:mrr:claim:1", kind="Claim", body={}),)
    assert extract_claims(objects) == (
        ClaimRow(claim_id="urn:mrr:claim:1", evidence_relations=(), counterevidence_relations=()),
    )


def test_extract_claims_raises_when_a_relation_list_is_not_a_list_of_strings() -> None:
    objects = (
        ArchivedObject(
            object_id="urn:mrr:claim:1", kind="Claim", body={"evidence_relations": [1, 2]}
        ),
    )
    with pytest.raises(ArchiveDumpParseError, match="evidence_relations"):
        extract_claims(objects)


def test_extraction_functions_ignore_objects_of_other_kinds() -> None:
    objects = (ArchivedObject(object_id="urn:mrr:mp:1", kind="MethodProfile", body={}),)
    assert extract_source_records(objects) == ()
    assert extract_evidence_anchors(objects) == ()
    assert extract_claims(objects) == ()
