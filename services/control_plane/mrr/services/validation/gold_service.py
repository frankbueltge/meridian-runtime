"""``GoldValidityService`` (task-packets/N1-T02.yaml R1/R3/R4/R6): loads a
frozen gold standard, verifies that it really is frozen, and builds a
:class:`mrr.domain.gold_validity_report.GoldValidityReport` from it and a set
of system predictions.

Read-only, DB-free, network-free and model-free — like its sibling
:class:`mrr.services.validation.service.ValidationService`, it opens no
database connection and constructs no repository. It also constructs no
``ModelAdapter``: measuring a classifier is not running one.

--- Three refusals, and why each is a refusal rather than a warning ---------

1. **Hash mismatch.** A gold set declares its own ``set_id``; the loader
   recomputes the sha256 of the file's bytes and builds
   ``<set_id>@sha256:<hex>`` from what it actually read. If a registry pins a
   different hash for that ``set_id``, the set has MOVED, and every earlier
   measurement against it silently means something else. There is no partial
   answer to give, so nothing is computed.

2. **Order-gate violation.** ``labelled_at`` must be strictly after
   ``criteria_locked_at``. Labels made before (or at the same instant as) the
   criteria they claim to follow are labels made against a standard that was
   still moving. docs/design/2026-07-24-capability-roadmap-entwurf.md's N1
   requires "Validierung erst nach Kriterien-Finalisierung (als Objekt-Zustand
   erzwingbar)" — enforceable as object state is exactly this check, and a
   warning would make it prose again.

3. **Synthetic provenance.** A set whose ``label_provenance.
   producing_practice`` is :data:`SYNTHETIC_PRACTICE` exists to test this
   apparatus and is refused for real evaluation unless the caller explicitly
   opts in. Quarantine by provenance rather than by filename, because a
   filename can be copied and a required opt-in cannot be reached by accident
   (task-packets/N1-T02.yaml derived_decisions (g)).

--- Gold is rater "a" -------------------------------------------------------

Every call into :mod:`mrr.domain.agreement` from this module passes gold as
rater ``a``, the system under test as rater ``b``, and ``reference="a"``. That
one convention is what turns the shared metric core from a symmetric
inter-rater statistic into a validity measurement — see
:mod:`mrr.domain.gold_validity_report` for why that difference earns a second
report type rather than a flag.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from mrr.domain.agreement import (
    align_ratings,
    cohen_kappa,
    confusion_matrix,
    krippendorff_alpha_nominal,
    majority_baseline,
    observed_agreement,
    per_category_prf,
    total_n,
    weighted_kappa,
)
from mrr.domain.exceptions import DomainError
from mrr.domain.gold_validity_report import (
    GoldValidityReport,
    ItemValidityRow,
    build_gold_validity_report,
)

#: The reserved ``producing_practice`` marking a set built to exercise this
#: apparatus rather than to stand as a standard. See the module docstring's
#: third refusal.
SYNTHETIC_PRACTICE = "synthetic-fixture"


class GoldSetFileError(DomainError):
    """The gold set, or the predictions file, cannot be read at all — missing,
    unreadable, not UTF-8, not JSON, or the wrong top-level shape. Mirrors
    :class:`mrr.services.validation.service.AnalysisSetFileError`'s role: a
    DEPENDENCY problem (CLI exit 2), never a research result.
    """

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"{path}: {reason}")


class GoldSetFrozenHashMismatchError(DomainError):
    """The gold set's bytes do not hash to the value pinned for its
    ``set_id``. A REFUSAL (CLI exit 3): the standard moved, so no figure
    computed against it would mean what it appears to mean.
    """

    def __init__(self, set_id: str, expected: str, actual: str) -> None:
        self.set_id = set_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"gold set {set_id!r} is not frozen: registry pins {expected}, "
            f"the file's bytes hash to {actual}. Refusing to measure against a moved "
            "standard."
        )


class GoldSetCriteriaDriftError(DomainError):
    """The gold set's copy of the criteria lock disagrees with the criteria
    file it names. A REFUSAL (CLI exit 3).

    This check exists because its absence cost a round trip. The criteria lock
    instant lived in two places — the criteria file and every gold set's copy
    of it — and on 2026-08-01 the two came apart: a wall-clock reading was
    stamped as UTC, the order gate refused an honestly-labelled set, and the
    refusal looked like a finding about the labeller when it was a defect in
    the reference clock. Duplicated state that gates a decision has to be
    checked against its source, or it will drift exactly when it matters.
    """

    def __init__(self, field: str, in_set: str, in_criteria: str) -> None:
        self.field = field
        self.in_set = in_set
        self.in_criteria = in_criteria
        super().__init__(
            f"criteria drift on {field!r}: the gold set says {in_set!r}, the criteria file it "
            f"names says {in_criteria!r}. One of them is stale; refusing to guess which."
        )


class GoldSetOrderGateError(DomainError):
    """``labelled_at`` is not strictly after ``criteria_locked_at``. A REFUSAL
    (CLI exit 3) — see the module docstring's second refusal.
    """

    def __init__(self, criteria_locked_at: str, labelled_at: str) -> None:
        self.criteria_locked_at = criteria_locked_at
        self.labelled_at = labelled_at
        super().__init__(
            f"order gate violated: labelled_at {labelled_at!r} is not strictly after "
            f"criteria_locked_at {criteria_locked_at!r}. Labels made against criteria "
            "that were still moving are not a gold standard."
        )


class GoldSetSyntheticProvenanceError(DomainError):
    """The set is a synthetic test fixture and was not explicitly opted into.
    A REFUSAL (CLI exit 3) — see the module docstring's third refusal.
    """

    def __init__(self, set_id: str) -> None:
        self.set_id = set_id
        super().__init__(
            f"gold set {set_id!r} carries producing_practice={SYNTHETIC_PRACTICE!r}: it "
            "is a fixture for testing this apparatus, not a standard. Pass "
            "--allow-synthetic to measure against it anyway, and read no result from it."
        )


class GoldSetLabelError(DomainError):
    """A case's ``expected_relation``, or a prediction's label, is not one of
    the set's declared categories. A REFUSAL (CLI exit 3) — never silently
    coerced into an "other" bucket, which would quietly change the confusion
    matrix's shape.
    """

    def __init__(self, case_id: str, side: str, label: str, categories: Sequence[str]) -> None:
        self.case_id = case_id
        self.side = side
        self.label = label
        super().__init__(
            f"case {case_id!r}: {side} label {label!r} is not one of the declared "
            f"categories {list(categories)!r}"
        )


def compute_sha256(data: bytes) -> str:
    """``sha256:<hex>`` over raw bytes — the same textual form
    ``schemas/common.schema.json#/$defs/sha256`` requires, so a hash from this
    function can be written straight into a schema-validated field.
    """
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _read_json_file(path: Path) -> tuple[Any, bytes]:
    """Read ``path`` once, returning both the parsed document and the exact
    bytes read. Both are needed: the document to work with, the bytes to hash.
    Reading twice would leave a window in which the file could change between
    the hash and the parse.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise GoldSetFileError(path, f"cannot read file ({exc})") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GoldSetFileError(path, f"not valid UTF-8 ({exc})") from exc
    try:
        return json.loads(text), raw
    except json.JSONDecodeError as exc:
        raise GoldSetFileError(path, f"not valid JSON ({exc})") from exc


def _require_mapping(value: Any, *, path: Path, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GoldSetFileError(path, f"{what} must be a JSON object, got {type(value).__name__}")
    return value


def _require_str(document: Mapping[str, Any], key: str, *, path: Path) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise GoldSetFileError(path, f"missing or empty required string key {key!r}")
    return value


def _parse_timestamp(value: str, *, path: Path, key: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GoldSetFileError(path, f"{key} is not an ISO 8601 timestamp: {value!r}") from exc


class GoldLabelSet:
    """A loaded, validated gold standard. Constructed only by
    :meth:`GoldValidityService.load_gold_set`, which is the single place the
    hash check, the order gate and the provenance quarantine happen — so a
    ``GoldLabelSet`` in hand has already passed all three.
    """

    def __init__(
        self,
        *,
        path: Path,
        set_id: str,
        sha256: str,
        categories: tuple[str, ...],
        criteria_version: str,
        criteria_locked_at: str,
        criteria_lock_content_hash: str,
        labelled_at: str,
        producing_practice: str,
        account: str,
        encounter_id: str | None,
        blind_to_measured_labels: bool,
        criteria: Mapping[str, str],
        cases: tuple[Mapping[str, Any], ...],
    ) -> None:
        self.path = path
        self.set_id = set_id
        self.sha256 = sha256
        self.categories = categories
        self.criteria_version = criteria_version
        self.criteria_locked_at = criteria_locked_at
        self.criteria_lock_content_hash = criteria_lock_content_hash
        self.labelled_at = labelled_at
        self.producing_practice = producing_practice
        self.account = account
        self.encounter_id = encounter_id
        self.blind_to_measured_labels = blind_to_measured_labels
        self.criteria = dict(criteria)
        self.cases = cases

    @property
    def fixture_set_id(self) -> str:
        """``<set_id>@sha256:<hex>`` — the exact string that goes into
        ``benchmarks.meridianbench.promotion.EvaluationProfile.fixture_set_id``
        so a promotion decision records WHICH frozen set it was computed
        against (task-packets/N1-T02.yaml R3).
        """
        return f"{self.set_id}@{self.sha256}"

    def gold_labels(self) -> dict[str, str]:
        """``case_id -> expected_relation`` for the DECIDABLE cases only — the
        mapping passed to :func:`mrr.domain.agreement.align_ratings` as rater
        "a".

        Undecidable cases are absent by design (criteria v2,
        ``R-undecidable-is-a-finding``). They are not scored, because there is
        no correct answer to score against; they are counted instead, and the
        count is reported. Folding them into the four labels would let a
        criteria set that fails to decide look exactly like one that decides
        well.
        """
        return {
            str(case["case_id"]): str(case["expected_relation"])
            for case in self.cases
            if not case.get("undecidable", False)
        }

    def undecidable_case_ids(self) -> tuple[str, ...]:
        """The cases the labelling practice could not decide under these
        criteria, named rather than merely counted — coverage is a property of
        the criteria, and which cases defeated them is the useful half.
        """
        return tuple(str(case["case_id"]) for case in self.cases if case.get("undecidable", False))

    def tie_broken_case_ids(self) -> tuple[str, ...]:
        """The cases where the conservative tie-break rule, rather than the
        definitions, produced the label (criteria v2,
        ``R-conservative-supports`` as amended).

        This is the count Ulysses' objection exists to make visible: without
        it the corroboration ceiling is a point estimate whose distance from
        its own alternative cannot be recovered.
        """
        return tuple(
            str(case["case_id"]) for case in self.cases if case.get("tie_with") is not None
        )


class GoldValidityService:
    """Builds a :class:`GoldValidityReport` from a frozen gold set and a set of
    system predictions. Read-only, DB-free, network-free, model-free.
    """

    def load_gold_set(
        self,
        path: Path,
        *,
        expected_sha256: str | None = None,
        allow_synthetic: bool = False,
        criteria_path: Path | None = None,
    ) -> GoldLabelSet:
        """Load, verify and return the gold standard at ``path``.

        Args:
            path: the committed gold-set JSON file.
            expected_sha256: when given, the hash the set's bytes MUST have.
                Supplied by the freeze registry; a mismatch is a refusal.
            allow_synthetic: opt in to loading a set whose provenance marks it
                a test fixture. Defaults to ``False`` — see the module
                docstring's third refusal.
            criteria_path: the criteria file the set names. When given, the
                set's own copies of the criteria lock are checked against it
                and any disagreement is a refusal. Optional only because older
                sets predate the check; supply it whenever it exists.

        Raises:
            GoldSetFileError: unreadable, non-UTF-8, non-JSON, or wrong shape.
            GoldSetFrozenHashMismatchError: the bytes moved.
            GoldSetOrderGateError: labels predate their own criteria.
            GoldSetSyntheticProvenanceError: a fixture used as a standard.
            GoldSetLabelError: an ``expected_relation`` outside the declared
                categories.
        """
        document_raw, raw_bytes = _read_json_file(path)
        document = _require_mapping(document_raw, path=path, what="gold label set")
        actual_sha256 = compute_sha256(raw_bytes)

        set_id = _require_str(document, "set_id", path=path)

        if expected_sha256 is not None and expected_sha256 != actual_sha256:
            raise GoldSetFrozenHashMismatchError(set_id, expected_sha256, actual_sha256)

        categories_raw = document.get("categories")
        if not isinstance(categories_raw, list) or len(categories_raw) < 2:
            raise GoldSetFileError(path, "categories must be a JSON array of at least two labels")
        categories = tuple(str(category) for category in categories_raw)
        if len(set(categories)) != len(categories):
            raise GoldSetFileError(path, f"categories contains duplicates: {list(categories)!r}")

        criteria_version = _require_str(document, "criteria_version", path=path)
        criteria_locked_at = _require_str(document, "criteria_locked_at", path=path)
        criteria_lock_content_hash = _require_str(document, "criteria_lock_content_hash", path=path)
        labelled_at = _require_str(document, "labelled_at", path=path)

        # --- Criteria drift, checked BEFORE the order gate, because a gate
        #     evaluated against a stale copy of its own reference produces a
        #     confident wrong answer rather than an honest refusal.
        if criteria_path is not None:
            criteria_document, criteria_bytes = _read_json_file(criteria_path)
            criteria = _require_mapping(criteria_document, path=criteria_path, what="criteria")
            actual_criteria_hash = compute_sha256(criteria_bytes)
            if criteria_lock_content_hash != actual_criteria_hash:
                raise GoldSetCriteriaDriftError(
                    "criteria_lock_content_hash",
                    criteria_lock_content_hash,
                    actual_criteria_hash,
                )
            criteria_own_lock = criteria.get("locked_at")
            if isinstance(criteria_own_lock, str) and criteria_own_lock != criteria_locked_at:
                raise GoldSetCriteriaDriftError(
                    "criteria_locked_at", criteria_locked_at, criteria_own_lock
                )

        # --- The order gate. Strictly after, not merely "not before": labels
        #     stamped at the same instant as the lock cannot be shown to have
        #     followed it.
        locked_dt = _parse_timestamp(criteria_locked_at, path=path, key="criteria_locked_at")
        labelled_dt = _parse_timestamp(labelled_at, path=path, key="labelled_at")
        if labelled_dt <= locked_dt:
            raise GoldSetOrderGateError(criteria_locked_at, labelled_at)

        provenance = _require_mapping(
            document.get("label_provenance"), path=path, what="label_provenance"
        )
        producing_practice = _require_str(provenance, "producing_practice", path=path)
        account = _require_str(provenance, "account", path=path)
        encounter_id_raw = provenance.get("encounter_id")
        encounter_id = str(encounter_id_raw) if isinstance(encounter_id_raw, str) else None
        blind = bool(provenance.get("blind_to_measured_labels", False))

        if producing_practice == SYNTHETIC_PRACTICE and not allow_synthetic:
            raise GoldSetSyntheticProvenanceError(set_id)

        criteria_raw = document.get("criteria") or {}
        criteria = _require_mapping(criteria_raw, path=path, what="criteria")

        cases_raw = document.get("cases")
        if not isinstance(cases_raw, list) or not cases_raw:
            raise GoldSetFileError(path, "cases must be a non-empty JSON array")

        cases: list[Mapping[str, Any]] = []
        seen_case_ids: set[str] = set()
        for index, case_raw in enumerate(cases_raw):
            case = _require_mapping(case_raw, path=path, what=f"cases[{index}]")
            case_id = _require_str(case, "case_id", path=path)
            if case_id in seen_case_ids:
                raise GoldSetFileError(path, f"duplicate case_id {case_id!r}")
            seen_case_ids.add(case_id)
            _require_str(case, "excerpt", path=path)
            _require_str(case, "claim_text", path=path)

            # Criteria v2 R-record-the-decider: which rule or definition
            # actually produced this label. Required on every case, decidable
            # or not — "the criteria could not settle it" is itself a decider.
            _require_str(case, "decided_by", path=path)

            if case.get("undecidable", False):
                # R-undecidable-is-a-finding. Not scored, but not silent
                # either: an undecidable case without a stated reason would be
                # indistinguishable from a lazy one.
                _require_str(case, "undecidable_reason", path=path)
                if case.get("expected_relation") is not None:
                    raise GoldSetFileError(
                        path,
                        f"case {case_id!r} is marked undecidable but still carries an "
                        "expected_relation — it is one or the other, never both",
                    )
            else:
                expected_relation = _require_str(case, "expected_relation", path=path)
                if expected_relation not in categories:
                    raise GoldSetLabelError(case_id, "gold", expected_relation, categories)
                _require_str(case, "expected_rationale", path=path)

            # R-conservative-supports as amended: when the tie-break rule fired
            # rather than the definitions, the runner-up is on the record. A
            # tie_with outside the declared categories is a typo, not a label.
            tie_with = case.get("tie_with")
            if tie_with is not None:
                if not isinstance(tie_with, str) or tie_with not in categories:
                    raise GoldSetLabelError(case_id, "tie_with", str(tie_with), categories)
                if tie_with == case.get("expected_relation"):
                    raise GoldSetFileError(
                        path,
                        f"case {case_id!r}: tie_with equals the label itself — a tie is with "
                        "the runner-up, not with the winner",
                    )

            cases.append(case)

        if not any(not case.get("undecidable", False) for case in cases):
            raise GoldSetFileError(
                path,
                "every case is marked undecidable — there is nothing to measure against. "
                "That is a finding about the criteria, not a gold standard.",
            )

        return GoldLabelSet(
            path=path,
            set_id=set_id,
            sha256=actual_sha256,
            categories=categories,
            criteria_version=criteria_version,
            criteria_locked_at=criteria_locked_at,
            criteria_lock_content_hash=criteria_lock_content_hash,
            labelled_at=labelled_at,
            producing_practice=producing_practice,
            account=account,
            encounter_id=encounter_id,
            blind_to_measured_labels=blind,
            criteria=criteria,
            cases=tuple(cases),
        )

    def load_predictions(self, path: Path) -> tuple[str, dict[str, str]]:
        """Load a predictions file: ``{"system_id": ..., "predictions":
        {case_id: label}}``. Returns the system id and the mapping.

        Deliberately a dumb, explicit format. A prediction file is whatever a
        system emitted; this service's job is to compare it to gold, not to
        run it.
        """
        document_raw, _ = _read_json_file(path)
        document = _require_mapping(document_raw, path=path, what="predictions")
        system_id = _require_str(document, "system_id", path=path)
        predictions_raw = _require_mapping(
            document.get("predictions"), path=path, what="predictions.predictions"
        )
        predictions: dict[str, str] = {}
        for case_id, label in predictions_raw.items():
            if not isinstance(label, str) or not label:
                raise GoldSetFileError(
                    path, f"prediction for case {case_id!r} is not a non-empty string"
                )
            predictions[str(case_id)] = label
        if not predictions:
            raise GoldSetFileError(path, "predictions must not be empty")
        return system_id, predictions

    def build_report(
        self,
        gold_set: GoldLabelSet,
        *,
        system_id: str,
        predictions: Mapping[str, str],
    ) -> GoldValidityReport:
        """Compute the validity report. Gold is rater "a" throughout.

        Raises:
            GoldSetLabelError: a prediction uses a label outside the declared
                categories.
            mrr.domain.agreement.MismatchedRatersError: the prediction set and
                the gold set do not cover exactly the same cases — propagated
                unchanged, because a partial measurement over an unstated
                subset is precisely the kind of quiet result this apparatus
                exists to prevent.
        """
        gold_labels = gold_set.gold_labels()

        for case_id, label in predictions.items():
            if label not in gold_set.categories:
                raise GoldSetLabelError(case_id, "system", label, gold_set.categories)

        # A system may well have produced a label for a case the criteria could
        # not settle. That prediction is not wrong — there is simply nothing to
        # score it against — so it is dropped from the matrix rather than
        # counted as an error. Dropped EXPLICITLY, by intersecting with the
        # decidable set, so that align_ratings' own mismatch refusal still
        # catches every other kind of coverage gap.
        undecidable = set(gold_set.undecidable_case_ids())
        scored_predictions = {
            case_id: label for case_id, label in predictions.items() if case_id not in undecidable
        }

        ratings = align_ratings(gold_labels, scored_predictions)
        matrix = confusion_matrix(ratings, gold_set.categories)

        items = tuple(
            ItemValidityRow(
                case_id=rating.item_id,
                gold_label=rating.rater_a_label,
                system_label=rating.rater_b_label,
                correct=rating.rater_a_label == rating.rater_b_label,
            )
            for rating in ratings
        )

        return build_gold_validity_report(
            gold_set_id=gold_set.set_id,
            gold_set_sha256=gold_set.sha256,
            criteria_version=gold_set.criteria_version,
            criteria_locked_at=gold_set.criteria_locked_at,
            criteria_lock_content_hash=gold_set.criteria_lock_content_hash,
            labelled_at=gold_set.labelled_at,
            label_provenance=gold_set.account,
            producing_practice=gold_set.producing_practice,
            encounter_id=gold_set.encounter_id,
            blind_to_measured_labels=gold_set.blind_to_measured_labels,
            system_id=system_id,
            categories=gold_set.categories,
            confusion_matrix=matrix,
            n=total_n(matrix),
            observed_agreement=observed_agreement(matrix),
            majority_baseline=majority_baseline(matrix, reference="a"),
            cohen_kappa=cohen_kappa(matrix),
            weighted_kappa_linear=weighted_kappa(matrix, weights="linear"),
            weighted_kappa_quadratic=weighted_kappa(matrix, weights="quadratic"),
            krippendorff_alpha=krippendorff_alpha_nominal(matrix),
            per_category=per_category_prf(matrix, gold_set.categories, reference="a"),
            items=items,
            undecidable_case_ids=gold_set.undecidable_case_ids(),
            tie_broken_case_ids=gold_set.tie_broken_case_ids(),
        )


__all__ = [
    "SYNTHETIC_PRACTICE",
    "GoldLabelSet",
    "GoldSetFileError",
    "GoldSetCriteriaDriftError",
    "GoldSetFrozenHashMismatchError",
    "GoldSetLabelError",
    "GoldSetOrderGateError",
    "GoldSetSyntheticProvenanceError",
    "GoldValidityService",
    "compute_sha256",
]
