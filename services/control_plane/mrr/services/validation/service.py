"""``ValidationService`` (task-packets/N1-T01.yaml): the read-only,
DB-FREE application-layer service that composes ``mrr.domain.agreement``'s
pure metric core into a stratified ``mrr.domain.agreement_report
.AgreementReport``, over the committed model-collapse verification set.

--- This service opens no database connection (invariant) -------------------

Every other ``services/control_plane/mrr/services/*`` package in this repo
wraps ``ObjectRepository``/``EdgeRepository``/an event log — this one wraps
NOTHING but the filesystem. The only three files it ever reads are (a) the
caller-given ``--analysis-set`` path (task-packets/N1-T01.yaml R3's declared,
committed crosswalk, or a structurally identical fixture), and (b)/(c) the
two file paths that crosswalk itself names under its own ``source_files``
key, resolved RELATIVE TO THE CROSSWALK FILE'S OWN DIRECTORY (never the
caller's current working directory) — so the three-file bundle stays valid
as long as its own internal relative layout is preserved, independent of
where a caller happens to invoke ``mrr validate agreement`` from. There is
no ``sqlalchemy.create_engine`` call, no ``ObjectRepository``, and nothing
here is ever passed to ``insert_revision``/``append``/``ArtifactStore.put``
— this is a strict subset of ``mrr.services.report.service.ReportService``'s
own "writes NOTHING" discipline, going one step further: it also READS
nothing from a database.

--- Why the crosswalk does not duplicate raw labels --------------------------

The crosswalk fixture (task-packets/N1-T01.yaml R3) declares the item
ALIGNMENT (which blind item corresponds to which corpus ``entry_id``) and
the per-stratum LABEL MAP (raw label -> common-space label) — it does NOT
duplicate each item's own raw label. Raw labels are read live, here, from
the two source files it names (``corpus-entries.json[].evidence_relation``,
``blind-returns.json[].verdict``) every time a report is built. This means
the crosswalk can never silently drift out of sync with the corpus it
describes: if either source file changed underneath it, the alignment
validation below (:func:`_load_corpus_lookup`/``_load_blind_lookup`` plus
the per-item lookups in :meth:`ValidationService.build_report`) would either
still succeed (because the change did not touch an aligned item) or fail
loudly with a typed refusal naming the exact missing/mismatched item —
never silently reporting a stale crosswalk's own baked-in numbers.

--- Typed refusals: two kinds, two exit codes at the CLI ---------------------

:class:`AnalysisSetFileError` covers every "this input cannot even be read
as data" failure — a missing file, unparseable JSON, or a document whose
top-level shape does not match what this service expects — mirroring
``mrr.services.cli.main``'s/``report_main``'s MRR-NFR-012 "dependency
unavailable" treatment (``mrr.services.cli.validation_main`` maps this to
exit 2). Every OTHER typed error here — :class:`MissingAlignedItemError`,
:class:`TitleMismatchError`, :class:`UnmappedLabelError`, and whatever
:mod:`mrr.domain.agreement` itself raises (mismatched raters, unknown
category label, duplicate categories) — is a REFUSAL about the DATA's own
internal consistency, not about file I/O (``validation_main`` maps these to
exit 3, mirroring ``report_main``'s own "everything downstream of a
successful dependency check" refusal bucket).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from mrr.domain.agreement import (
    AlignedRating,
    align_ratings,
    cohen_kappa,
    col_marginals,
    confusion_matrix,
    krippendorff_alpha_nominal,
    majority_baseline,
    observed_agreement,
    per_category_prf,
    row_marginals,
    weighted_kappa,
)
from mrr.domain.agreement_report import (
    AgreementReport,
    ItemAgreementRow,
    build_agreement_report,
    build_stratum_report,
)
from mrr.domain.exceptions import DomainError

#: The one rater named as the reference for asymmetric precision/recall/F1
#: and the majority-class baseline (task-packets/N1-T01.yaml R1/R2) — read
#: from the crosswalk's own ``reference_rater`` field, but this constant
#: documents the expected, and so far only ever committed, value.
_EXPECTED_RATER_IDS = ("pipeline", "blind")


class AnalysisSetFileError(DomainError):
    """Raised when the ``--analysis-set`` crosswalk file, or either of the
    two source files it names, cannot even be read as data — missing,
    unreadable, not valid JSON, or the wrong top-level shape. Carries
    ``path`` and a human-readable ``detail``; mapped to exit 2 (MRR-NFR-012
    "dependency unavailable") at the CLI, never exit 3 — this is not a
    refusal about the DATA's own consistency, it is "this input does not
    exist as usable data at all".
    """

    def __init__(self, path: Path, detail: str) -> None:
        self.path = path
        self.detail = detail
        super().__init__(f"{path}: {detail}")


class MissingAlignedItemError(DomainError):
    """Raised when the crosswalk declares an item alignment
    (``blind_item``/``corpus_entry_id``) that does not actually resolve in
    the loaded source file on the named side — "every aligned item present
    in both raters, else typed refusal naming the gap, never a silent
    partial" (task-packets/N1-T01.yaml R4). Carries ``stratum_id``,
    ``blind_item``, ``corpus_entry_id``, and ``side`` (``"corpus"`` or
    ``"blind"`` — which loaded file the item could not be found in).
    """

    def __init__(self, stratum_id: str, blind_item: str, corpus_entry_id: str, side: str) -> None:
        self.stratum_id = stratum_id
        self.blind_item = blind_item
        self.corpus_entry_id = corpus_entry_id
        self.side = side
        super().__init__(
            f"stratum {stratum_id!r}: crosswalk declares alignment "
            f"(blind_item={blind_item!r}, corpus_entry_id={corpus_entry_id!r}), but no "
            f"matching entry was found on the {side!r} side — incomplete alignment"
        )


class TitleMismatchError(DomainError):
    """Raised when the crosswalk's own declared ``title`` for an aligned
    item disagrees with the title actually recorded in the loaded source
    file — a drift/integrity check between the committed, transcribed
    crosswalk and the (also committed, but separately editable) source
    files it describes. Carries ``stratum_id``, ``item_id``,
    ``declared_title``, ``actual_title``, and ``source`` (which file the
    actual title came from).
    """

    def __init__(
        self, stratum_id: str, item_id: str, declared_title: str, actual_title: str, source: str
    ) -> None:
        self.stratum_id = stratum_id
        self.item_id = item_id
        self.declared_title = declared_title
        self.actual_title = actual_title
        self.source = source
        super().__init__(
            f"stratum {stratum_id!r}, item {item_id!r}: crosswalk declares title "
            f"{declared_title!r}, but {source} records {actual_title!r}"
        )


class UnmappedLabelError(DomainError):
    """Raised when an item's raw label (read live from the corpus/blind
    source file) is not a key in the crosswalk's own declared
    ``label_map[rater].map_to_common`` for that stratum — a label with no
    honest common-space mapping (task-packets/N1-T01.yaml stop_condition 1:
    "never widen the mapping by guessing"). Carries ``stratum_id``,
    ``rater``, ``item_id``, and the offending ``raw_label``.
    """

    def __init__(self, stratum_id: str, rater: str, item_id: str, raw_label: str) -> None:
        self.stratum_id = stratum_id
        self.rater = rater
        self.item_id = item_id
        self.raw_label = raw_label
        super().__init__(
            f"stratum {stratum_id!r}, item {item_id!r}: rater {rater!r} raw label "
            f"{raw_label!r} has no entry in the crosswalk's declared label_map"
        )


def _read_json(path: Path) -> Any:
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AnalysisSetFileError(path, f"cannot read file ({exc})") from exc
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise AnalysisSetFileError(path, f"not valid JSON ({exc})") from exc


def _require_mapping(value: Any, *, path: Path, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AnalysisSetFileError(
            path, f"{what} must be a JSON object, got {type(value).__name__}"
        )
    return value


def _require_key(document: Mapping[str, Any], key: str, *, path: Path) -> Any:
    if key not in document:
        raise AnalysisSetFileError(path, f"missing required key {key!r}")
    return document[key]


def _load_corpus_lookup(path: Path) -> dict[str, Mapping[str, Any]]:
    raw = _read_json(path)
    if not isinstance(raw, list):
        raise AnalysisSetFileError(path, f"expected a JSON array, got {type(raw).__name__}")
    lookup: dict[str, Mapping[str, Any]] = {}
    for entry in raw:
        entry_map = _require_mapping(entry, path=path, what="a corpus-entries.json element")
        entry_id = entry_map.get("entry_id")
        if not isinstance(entry_id, str):
            raise AnalysisSetFileError(path, "a corpus-entries.json element has no string entry_id")
        lookup[entry_id] = entry_map
    return lookup


def _load_blind_lookup(path: Path) -> dict[str, Mapping[str, Any]]:
    raw = _read_json(path)
    document = _require_mapping(raw, path=path, what="blind-returns.json")
    lookup: dict[str, Mapping[str, Any]] = {}
    for section in ("works", "papers"):
        rows = document.get(section)
        if not isinstance(rows, list):
            raise AnalysisSetFileError(
                path, f"blind-returns.json[{section!r}] must be a JSON array"
            )
        for row in rows:
            row_map = _require_mapping(
                row, path=path, what=f"a blind-returns.json[{section}] element"
            )
            item = row_map.get("item")
            if not isinstance(item, str):
                raise AnalysisSetFileError(
                    path, f"a blind-returns.json[{section}] element has no string item"
                )
            lookup[item] = row_map
    return lookup


class ValidationService:
    """docs/design/2026-07-24-n1-derivation.md's N1-T01 architecture
    section: loads the declared crosswalk plus the two source files it
    names, validates the alignment is total, maps each rater into the
    common label space per stratum, invokes ``mrr.domain.agreement``, and
    builds the ``mrr.domain.agreement_report.AgreementReport``. See the
    module docstring for the full design rationale — above all, that this
    class opens no database connection and constructs no repository.
    """

    def build_report(self, analysis_set_path: Path) -> AgreementReport:
        """Build the full stratified :class:`AgreementReport` for the
        crosswalk (or structurally identical fixture) at
        ``analysis_set_path``.

        Raises:
            AnalysisSetFileError: the crosswalk file, or either source file
                it names, is missing, unreadable, unparseable, or has the
                wrong top-level shape.
            MissingAlignedItemError: a declared item alignment does not
                resolve on the corpus or blind side.
            TitleMismatchError: a declared item's title disagrees with the
                title actually recorded in the corpus or blind source file.
            UnmappedLabelError: an item's raw label has no entry in the
                crosswalk's declared label map for that rater/stratum.
            mrr.domain.agreement.MismatchedRatersError: unreachable in
                practice here (this method always builds both raters' label
                mappings over the identical item id set derived from the
                same crosswalk items list), retained as a possible
                propagation from ``mrr.domain.agreement.align_ratings`` for
                completeness.
            mrr.domain.agreement.DuplicateCategoryError: the crosswalk's own
                ``ordered_categories`` for a stratum contains a repeated
                label.
            mrr.domain.agreement.UnknownCategoryLabelError: a common-space
                label produced by the crosswalk's own label map is not a
                member of that stratum's own ``ordered_categories`` — a
                crosswalk-authoring error, never silently absorbed.
        """
        crosswalk_bytes = self._read_crosswalk_bytes(analysis_set_path)
        try:
            crosswalk_document = json.loads(crosswalk_bytes.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise AnalysisSetFileError(analysis_set_path, f"not valid UTF-8 ({exc})") from exc
        except json.JSONDecodeError as exc:
            raise AnalysisSetFileError(analysis_set_path, f"not valid JSON ({exc})") from exc
        crosswalk = _require_mapping(
            crosswalk_document, path=analysis_set_path, what="the crosswalk"
        )
        crosswalk_sha256 = f"sha256:{hashlib.sha256(crosswalk_bytes).hexdigest()}"

        reference_rater = _require_key(crosswalk, "reference_rater", path=analysis_set_path)
        if reference_rater not in _EXPECTED_RATER_IDS:
            raise AnalysisSetFileError(
                analysis_set_path,
                f"reference_rater {reference_rater!r} is not one of {_EXPECTED_RATER_IDS!r}",
            )

        source_files = _require_mapping(
            _require_key(crosswalk, "source_files", path=analysis_set_path),
            path=analysis_set_path,
            what="source_files",
        )
        crosswalk_dir = analysis_set_path.resolve().parent
        corpus_path = (crosswalk_dir / str(source_files["corpus_entries"])).resolve()
        blind_path = (crosswalk_dir / str(source_files["blind_returns"])).resolve()

        corpus_by_entry_id = _load_corpus_lookup(corpus_path)
        blind_by_item = _load_blind_lookup(blind_path)

        strata_dict = _require_mapping(
            _require_key(crosswalk, "strata", path=analysis_set_path),
            path=analysis_set_path,
            what="strata",
        )

        stratum_reports = []
        for stratum_id in sorted(strata_dict):
            stratum_spec = _require_mapping(
                strata_dict[stratum_id], path=analysis_set_path, what=f"strata[{stratum_id!r}]"
            )
            stratum_reports.append(
                self._build_one_stratum(
                    stratum_id=stratum_id,
                    stratum_spec=stratum_spec,
                    reference_rater=reference_rater,
                    corpus_by_entry_id=corpus_by_entry_id,
                    blind_by_item=blind_by_item,
                    crosswalk_path=analysis_set_path,
                )
            )

        return build_agreement_report(
            reference_rater=reference_rater,
            crosswalk_path=str(analysis_set_path),
            crosswalk_sha256=crosswalk_sha256,
            strata=stratum_reports,
        )

    @staticmethod
    def _read_crosswalk_bytes(path: Path) -> bytes:
        try:
            return path.read_bytes()
        except OSError as exc:
            raise AnalysisSetFileError(path, f"cannot read file ({exc})") from exc

    def _build_one_stratum(
        self,
        *,
        stratum_id: str,
        stratum_spec: Mapping[str, Any],
        reference_rater: str,
        corpus_by_entry_id: Mapping[str, Mapping[str, Any]],
        blind_by_item: Mapping[str, Mapping[str, Any]],
        crosswalk_path: Path,
    ) -> Any:
        what = f"strata[{stratum_id!r}]"
        ordered_categories_raw = _require_key(
            stratum_spec, "ordered_categories", path=crosswalk_path
        )
        if not isinstance(ordered_categories_raw, list) or not all(
            isinstance(c, str) for c in ordered_categories_raw
        ):
            raise AnalysisSetFileError(
                crosswalk_path, f"{what}.ordered_categories must be a JSON array of strings"
            )
        ordered_categories = tuple(ordered_categories_raw)
        label_map = _require_mapping(
            _require_key(stratum_spec, "label_map", path=crosswalk_path),
            path=crosswalk_path,
            what=f"{what}.label_map",
        )
        pipeline_side = _require_mapping(
            _require_key(label_map, "pipeline", path=crosswalk_path),
            path=crosswalk_path,
            what=f"{what}.label_map.pipeline",
        )
        blind_side = _require_mapping(
            _require_key(label_map, "blind", path=crosswalk_path),
            path=crosswalk_path,
            what=f"{what}.label_map.blind",
        )
        pipeline_map = _require_mapping(
            _require_key(pipeline_side, "map_to_common", path=crosswalk_path),
            path=crosswalk_path,
            what=f"{what}.label_map.pipeline.map_to_common",
        )
        blind_map = _require_mapping(
            _require_key(blind_side, "map_to_common", path=crosswalk_path),
            path=crosswalk_path,
            what=f"{what}.label_map.blind.map_to_common",
        )
        items = _require_key(stratum_spec, "items", path=crosswalk_path)
        if not isinstance(items, list):
            raise AnalysisSetFileError(crosswalk_path, f"{what}.items must be a JSON array")

        pipeline_labels: dict[str, str] = {}
        blind_labels: dict[str, str] = {}
        item_rows: list[ItemAgreementRow] = []

        for item_spec in items:
            item_map = _require_mapping(item_spec, path=crosswalk_path, what=f"{what}.items[]")
            blind_item = str(_require_key(item_map, "blind_item", path=crosswalk_path))
            corpus_entry_id = str(_require_key(item_map, "corpus_entry_id", path=crosswalk_path))
            declared_title = str(_require_key(item_map, "title", path=crosswalk_path))

            corpus_entry = corpus_by_entry_id.get(corpus_entry_id)
            if corpus_entry is None:
                raise MissingAlignedItemError(stratum_id, blind_item, corpus_entry_id, "corpus")
            blind_entry = blind_by_item.get(blind_item)
            if blind_entry is None:
                raise MissingAlignedItemError(stratum_id, blind_item, corpus_entry_id, "blind")

            corpus_title = corpus_entry.get("title")
            if isinstance(corpus_title, str) and corpus_title != declared_title:
                raise TitleMismatchError(
                    stratum_id, blind_item, declared_title, corpus_title, "corpus-entries.json"
                )
            blind_title = blind_entry.get("title")
            if isinstance(blind_title, str) and blind_title != declared_title:
                raise TitleMismatchError(
                    stratum_id, blind_item, declared_title, blind_title, "blind-returns.json"
                )

            if "evidence_relation" not in corpus_entry:
                raise AnalysisSetFileError(
                    crosswalk_path,
                    f"corpus entry {corpus_entry_id!r} has no evidence_relation field",
                )
            if "verdict" not in blind_entry:
                raise AnalysisSetFileError(
                    crosswalk_path, f"blind entry {blind_item!r} has no verdict field"
                )
            raw_pipeline = str(corpus_entry["evidence_relation"])
            raw_blind = str(blind_entry["verdict"])

            if raw_pipeline not in pipeline_map:
                raise UnmappedLabelError(stratum_id, "pipeline", blind_item, raw_pipeline)
            if raw_blind not in blind_map:
                raise UnmappedLabelError(stratum_id, "blind", blind_item, raw_blind)

            common_pipeline = pipeline_map[raw_pipeline]
            common_blind = blind_map[raw_blind]

            pipeline_labels[blind_item] = common_pipeline
            blind_labels[blind_item] = common_blind
            item_rows.append(
                ItemAgreementRow(
                    item_id=blind_item,
                    corpus_entry_id=corpus_entry_id,
                    title=declared_title,
                    rater_a_label=common_pipeline,
                    rater_b_label=common_blind,
                    agree=common_pipeline == common_blind,
                )
            )

        ratings: tuple[AlignedRating, ...] = align_ratings(pipeline_labels, blind_labels)
        matrix = confusion_matrix(ratings, ordered_categories)

        rater_a_id = "pipeline"
        rater_b_id = "blind"
        reference_side: Literal["a", "b"] = "a" if reference_rater == rater_a_id else "b"

        prevalence_a = list(zip(ordered_categories, row_marginals(matrix), strict=True))
        prevalence_b = list(zip(ordered_categories, col_marginals(matrix), strict=True))

        return build_stratum_report(
            stratum_id=stratum_id,
            rater_a_id=rater_a_id,
            rater_b_id=rater_b_id,
            reference_rater=reference_rater,
            categories=ordered_categories,
            confusion_matrix=matrix,
            n=len(item_rows),
            observed_agreement=observed_agreement(matrix),
            majority_baseline=majority_baseline(matrix, reference=reference_side),
            prevalence_a=prevalence_a,
            prevalence_b=prevalence_b,
            cohen_kappa=cohen_kappa(matrix),
            weighted_kappa_linear=weighted_kappa(matrix, weights="linear"),
            weighted_kappa_quadratic=weighted_kappa(matrix, weights="quadratic"),
            krippendorff_alpha=krippendorff_alpha_nominal(matrix),
            per_category=per_category_prf(matrix, ordered_categories, reference=reference_side),
            items=tuple(item_rows),
        )
