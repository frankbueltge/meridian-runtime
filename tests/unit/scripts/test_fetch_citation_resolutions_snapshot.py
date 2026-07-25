"""AT5 (task-packets/N2-T02a.yaml): the snapshot writer is deterministic —
two writes of the same PARSED input (already-fetched resolution records,
never re-fetched) are byte-identical — and covers every manifest citation
handed to it. Pure functions only (:func:`build_snapshot_document`,
:func:`render_snapshot_json`); no network, no filesystem, no wall clock.
"""

from __future__ import annotations

import json

from scripts.fetch_citation_resolutions import (
    ResolutionRecord,
    build_snapshot_document,
    render_snapshot_json,
)

_RECORDS = (
    ResolutionRecord(
        citation_id="kosmos",
        identifier="arxiv:2511.02824",
        resolver="arxiv",
        resolved=True,
        resolved_title="Kosmos: An AI Scientist for Autonomous Discovery",
        resolved_detail="first author Ludovico Mitchener",
    ),
    ResolutionRecord(
        citation_id="sakana-nature",
        identifier="doi:10.1038/s41586-026-10265-5",
        resolver="doi",
        resolved=True,
        resolved_title="Towards end-to-end automation of AI research",
        resolved_container="Nature",
        resolved_detail="vol 651 (8107), pp. 914-919, 2026; first author Chris Lu",
    ),
    ResolutionRecord(
        citation_id="a-not-found-one",
        identifier="arxiv:9999.00000",
        resolver="arxiv",
        resolved=False,
        resolved_title=None,
    ),
)


def test_two_renders_of_the_same_input_are_byte_identical() -> None:
    document_1 = build_snapshot_document(
        manifest_relative_path="../citations.manifest.json",
        fetched_on="2026-07-25",
        records=_RECORDS,
    )
    document_2 = build_snapshot_document(
        manifest_relative_path="../citations.manifest.json",
        fetched_on="2026-07-25",
        records=_RECORDS,
    )
    rendered_1 = render_snapshot_json(document_1)
    rendered_2 = render_snapshot_json(document_2)
    assert rendered_1 == rendered_2


def test_rendering_is_insensitive_to_the_caller_supplied_record_order() -> None:
    """R3: "resolutions[] MUST be sorted by citation_id" — regardless of
    what order the caller's own fetch loop happened to produce them in.
    """
    forward = build_snapshot_document(
        manifest_relative_path="../citations.manifest.json",
        fetched_on="2026-07-25",
        records=_RECORDS,
    )
    reversed_records = tuple(reversed(_RECORDS))
    backward = build_snapshot_document(
        manifest_relative_path="../citations.manifest.json",
        fetched_on="2026-07-25",
        records=reversed_records,
    )
    assert render_snapshot_json(forward) == render_snapshot_json(backward)


def test_resolutions_are_sorted_by_citation_id() -> None:
    document = build_snapshot_document(
        manifest_relative_path="../citations.manifest.json",
        fetched_on="2026-07-25",
        records=_RECORDS,
    )
    ids = [resolution["citation_id"] for resolution in document["resolutions"]]
    assert ids == sorted(ids)


def test_document_covers_every_record_handed_to_it() -> None:
    document = build_snapshot_document(
        manifest_relative_path="../citations.manifest.json",
        fetched_on="2026-07-25",
        records=_RECORDS,
    )
    assert len(document["resolutions"]) == len(_RECORDS)
    covered_ids = {resolution["citation_id"] for resolution in document["resolutions"]}
    assert covered_ids == {record.citation_id for record in _RECORDS}


def test_rendered_json_has_sorted_keys_and_a_trailing_newline() -> None:
    document = build_snapshot_document(
        manifest_relative_path="../citations.manifest.json",
        fetched_on="2026-07-25",
        records=_RECORDS,
    )
    rendered = render_snapshot_json(document)

    assert rendered.endswith("\n")
    assert not rendered.endswith("\n\n")
    # sort_keys=True: re-dumping with the same option over the parsed-back
    # object must reproduce byte-identical text.
    reparsed = json.loads(rendered)
    assert json.dumps(reparsed, indent=2, sort_keys=True) + "\n" == rendered


def test_not_found_resolution_omits_optional_keys_but_keeps_resolved_title_key() -> None:
    document = build_snapshot_document(
        manifest_relative_path="../citations.manifest.json",
        fetched_on="2026-07-25",
        records=_RECORDS,
    )
    not_found = next(
        row for row in document["resolutions"] if row["citation_id"] == "a-not-found-one"
    )
    assert not_found["resolved"] is False
    assert not_found["resolved_title"] is None
    assert "resolved_container" not in not_found
    assert "resolved_detail" not in not_found


def test_document_shape_matches_the_committed_v1_schema() -> None:
    document = build_snapshot_document(
        manifest_relative_path="../citations.manifest.json",
        fetched_on="2026-07-25",
        records=_RECORDS,
    )
    assert document["schema_version"] == "citation-resolution-snapshot.v1"
    assert set(document) == {
        "schema_version",
        "manifest",
        "fetched_on",
        "resolvers",
        "note",
        "resolutions",
    }
    assert set(document["resolvers"]) == {"arxiv", "doi"}
    assert "N2-T03" in document["note"]
    assert "SUPPORT" in document["note"]
