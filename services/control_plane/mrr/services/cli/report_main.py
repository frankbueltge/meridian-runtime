"""``mrr report render`` (task-packets/E8-T03.yaml): a thin argparse CLI over
``mrr.services.report.service.ReportService`` plus ``mrr.domain
.research_report.render_markdown``/``render_html``, mirroring ``mrr.services
.cli.export_main``'s/``verification_main``'s own shape (a sibling file, not
inlined into ``main.py``, so that module's own diff stays a one-line,
additive ``"report"`` subparser registration — the E2-T07 CLI-law precedent
every CLI module here follows) and ``mrr.services.cli.main``'s MRR-NFR-012
"explicit degradation, never a fabricated substitute" discipline.

This is the tooling half of MRR-FR-100/-101/-104: a third party can now
materialize a deterministic Markdown or HTML research report from one sealed
``EvidenceCrate`` with one command, in either the ``internal`` or ``public``
disclosure projection, instead of only through ``ReportService.render``
called from Python. The CLI is thin — it parses arguments, checks
dependencies in cheapest-first order, calls ``ReportService.render`` exactly
once, and reports the result or a clear, typed failure. No domain behavior
lives here (task-packets/E2-T07.yaml's CLI law): the closure/redaction/
rendering logic all remains exclusively ``ReportService``'s and ``mrr.domain
.research_report``'s business — this module never inspects a crate body, a
claim, or a correction itself.

--- No ``--artifact-root`` flag, deliberately -------------------------------

Unlike ``mrr export ro-crate``, this command never fetches artifact bytes
(``ReportService.render`` calls only ``ExportService.resolve_closure``, never
``ExportService.export`` — see that service's own module docstring) — so
there is no ``--artifact-root`` flag here at all, and no ``ArtifactStore`` is
ever constructed by this module.

--- ``--classification-file``: required with public, forbidden with internal (R4) ---

An explicit governance input, never defaulted (task-packets/E8-T03.yaml
derived_decisions (d)): ``--disclosure public`` REQUIRES
``--classification-file`` (a JSON object mapping urn -> one of the five
``mrr.domain.artifacts.Classification`` values); ``--disclosure internal``
FORBIDS it outright — supplying one is itself a refusal, not silently
ignored, so a caller can never believe an attestation file influenced an
internal render that in fact ignores it entirely (``mrr.domain
.research_report.build_report``'s own stance). Both violations, and every
file read/parse/shape/value problem, are reported as MRR-NFR-012 dependency
failures (exit 2) — the same treatment ``mrr.services.cli.verification_main``
already gives its own conditionally-shaped ``--verification-file``, and the
same code argparse itself would use for a missing required flag.

--- Ordering invariant (MRR-NFR-012): cheapest, local checks before expensive ones --

task-packets/E8-T03.yaml R4's own explicit ordering: (1) ``--output`` must
not already exist — a plain filesystem stat (:func:`output_file_conflict`);
(2) ``--classification-file`` read + parsed + validated (public only) — no
network, no database; (3) the PostgreSQL database named by
``--database-url`` must be reachable (``SELECT 1``) — the one network round
trip this command ever makes before doing real work. Only after all three
succeed is ``ReportService.render`` called, exactly once, followed by
exactly one render call (``render_markdown``/``render_html``) and one atomic
write.

--- The exit-code map (mirrors ``export_main``/``verification_main``) -------

- ``0``: the report was rendered and written. Prints a single JSON line:
  ``crate_id``, ``output``, ``format``, ``disclosure``, ``section_counts``
  (a small object counting each of the eight R1 sections' own rows).
- ``2``: a DEPENDENCY is unavailable — ``--classification-file`` missing
  when required, present when forbidden, unreadable, not valid JSON, not a
  JSON object, or containing a value outside the five declared
  ``Classification`` levels; or the PostgreSQL database is unreachable. No
  in-memory or partial fallback exists for any of these (MRR-NFR-012).
- ``3``: the render was REFUSED by everything downstream of a successful
  dependency check — ``--output`` already exists (checked FIRST, even before
  the dependency checks above, since it is the cheapest of all and needs no
  file, store, or connection at all), an unknown ``--crate-id``, or a
  resolved object that is not an ``EvidenceCrate`` — every ``mrr.domain
  .exceptions.DomainError``/plain ``ValueError`` ``ReportService.render``
  lets propagate.
- argparse's own built-in failures (a bad flag, a missing required argument,
  an invalid ``--format``/``--disclosure`` choice) use argparse's own exit
  code, ``2`` — the same overlap every other CLI module here already has
  between its own dependency-unavailable code and argparse's usage-error
  code.

--- Atomic write (R4) --------------------------------------------------------

The rendered bytes are written to a temp file in the SAME directory as
``--output`` (same filesystem, hence ``os.replace`` is atomic), and
``os.replace`` is the LAST act — mirrors ``mrr.services.export.service
.ExportService._write_export``'s identical discipline, one file instead of a
whole tree. A failure at any point before the replace leaves ``--output``
untouched (never created, never partially written) and, at most, an orphaned
temp file next to it.

--- task-packets/E8-T06.yaml R4: the one-of root group (minus --artifact-root) --

``render`` gains the SAME mutually-exclusive, ``required=True`` root group
``mrr export ro-crate`` gains (task-packets/E8-T06.yaml R2) — ``--crate-id``/
``--claim-id`` (repeatable)/``--all-claims`` — MINUS ``--artifact-root``,
which this command never had in the first place (see "No --artifact-root
flag, deliberately" above: the report never touches artifact bytes for
EITHER root). No new usage-refusal shape check is therefore needed here —
unlike ``export_main``'s own ``--artifact-root``-shape check, there is
nothing analogous to validate. ``--classification-file``'s own required-
with-public/forbidden-with-internal rule (R4, above) is UNCHANGED and
applies identically to both roots.

The exit-0 JSON line gains a ``"root"`` key (``"crate"``/``"claims"``) and a
``"claim_ids"`` key, mirroring ``export_main``'s own identical, purely
additive extension — every crate-rooted invocation's pre-existing keys keep
their pre-existing values.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

import sqlalchemy as sa
from mrr.domain.artifacts import Classification
from mrr.domain.exceptions import DomainError
from mrr.domain.research_report import Disclosure, render_html, render_markdown
from mrr.persistence.repositories import (
    PostgresEdgeRepository,
    PostgresEventLog,
    PostgresObjectRepository,
)
from mrr.services.report.service import ReportService
from sqlalchemy.exc import SQLAlchemyError

#: See the module docstring's "exit-code map" section.
_EXIT_DEPENDENCY_UNAVAILABLE = 2
_EXIT_REPORT_REFUSED = 3

#: The exact five-value ``mrr.domain.artifacts.Classification`` enum,
#: transcribed here (not imported as a runtime iterable — ``Classification``
#: is a ``typing.Literal``, which carries no iterable member list at
#: runtime) so ``--classification-file`` values can be validated against it
#: before ``ReportService.render`` is ever called.
_VALID_CLASSIFICATIONS: frozenset[str] = frozenset(
    {"PUBLIC", "INTERNAL", "RESTRICTED", "SENSITIVE", "PARTICIPANT_IDENTIFIABLE"}
)

_FORMAT_MARKDOWN = "md"
_FORMAT_HTML = "html"

_DISCLOSURE_INTERNAL = "internal"
_DISCLOSURE_PUBLIC = "public"


def output_file_conflict(output: Path) -> bool:
    """``True`` iff ``output`` already exists (file or directory) —
    task-packets/E8-T03.yaml R4: "``--output`` conflict first (existing file
    refuses, exit 3)". Unlike ``mrr export ro-crate``'s own ``--output-dir``
    (a whole directory, where an existing EMPTY directory is not a
    conflict), ``--output`` here names a single FILE this command writes
    once — any pre-existing path at all, of any kind, is a conflict.
    """
    return output.exists()


def _add_render_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    render_parser = subparsers.add_parser(
        "render",
        help=(
            "Render a deterministic Markdown or HTML research report from one sealed "
            "EvidenceCrate (task-packets/E8-T03.yaml)."
        ),
    )
    render_parser.add_argument(
        "--database-url",
        required=True,
        help="SQLAlchemy PostgreSQL URL, e.g. postgresql+psycopg://user:pass@host/db",
    )
    root_group = render_parser.add_mutually_exclusive_group(required=True)
    root_group.add_argument(
        "--crate-id",
        default=None,
        help="URN of the sealed EvidenceCrate to report on, loaded from the generic object store.",
    )
    root_group.add_argument(
        "--claim-id",
        action="append",
        default=None,
        dest="claim_id",
        help=(
            "URN of a Claim to root the report on (task-packets/E8-T06.yaml). Repeatable — "
            "the union of every given claim's own closure is reported on. Each MUST resolve "
            "to a stored Claim, else a typed refusal names it."
        ),
    )
    root_group.add_argument(
        "--all-claims",
        action="store_true",
        default=False,
        help=(
            "Root the report on EVERY claim the schema contains (task-packets/E8-T06.yaml) "
            "— each archival schema is exactly one run's world. Refuses (exit 3) if the "
            "schema has zero claims, rather than shipping a silent empty report."
        ),
    )
    render_parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="File path the rendered report is written into. Must not already exist.",
    )
    render_parser.add_argument(
        "--format",
        required=True,
        choices=(_FORMAT_MARKDOWN, _FORMAT_HTML),
        help="Output format: 'md' (Markdown) or 'html'.",
    )
    render_parser.add_argument(
        "--disclosure",
        required=True,
        choices=(_DISCLOSURE_INTERNAL, _DISCLOSURE_PUBLIC),
        help=(
            "'internal' (full content) or 'public' (MRR-FR-095 plus the fail-closed E6-T05 "
            "redaction rule). 'public' REQUIRES --classification-file; 'internal' FORBIDS it."
        ),
    )
    render_parser.add_argument(
        "--classification-file",
        type=Path,
        default=None,
        help=(
            "Path to a JSON object mapping object urn -> one of PUBLIC/INTERNAL/RESTRICTED/"
            "SENSITIVE/PARTICIPANT_IDENTIFIABLE. REQUIRED with --disclosure public; FORBIDDEN "
            "with --disclosure internal (an explicit governance input, never defaulted)."
        ),
    )


def register_report_subcommand(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """The ONE call ``mrr.services.cli.main.build_parser`` makes to register
    ``mrr report render`` — everything else about this subcommand's flags
    and behavior lives in this module.
    """
    report_parser = subparsers.add_parser(
        "report",
        help="Research-report projection rendering (task-packets/E8-T03.yaml).",
    )
    report_subparsers = report_parser.add_subparsers(dest="report_command", required=True)
    _add_render_subparser(report_subparsers)


def _load_classification_file(path: Path) -> dict[str, Classification]:
    """Read, parse, and validate ``path`` as a JSON object mapping urn ->
    one of the five declared ``Classification`` values.

    Raises:
        ValueError: ``path`` cannot be read, is not valid JSON, is not a
            JSON object, or any value is not one of the five declared
            classification levels — carries a message naming the exact
            problem (and, for a bad value, the offending urn and value).
    """
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read --classification-file {path} ({exc})") from exc

    try:
        raw_document = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--classification-file {path} is not valid JSON ({exc})") from exc

    if not isinstance(raw_document, dict):
        raise ValueError(
            f"--classification-file {path} must be a JSON object (urn -> classification), "
            f"got {type(raw_document).__name__}"
        )

    classification_by_object_id: dict[str, Classification] = {}
    for object_id, value in raw_document.items():
        if value not in _VALID_CLASSIFICATIONS:
            raise ValueError(
                f"--classification-file {path}: {object_id!r} maps to {value!r}, which is not "
                f"one of {sorted(_VALID_CLASSIFICATIONS)}"
            )
        classification_by_object_id[object_id] = value
    return classification_by_object_id


def run_command(args: argparse.Namespace) -> int:
    """The actual execution logic for ``mrr report render`` — called both by
    ``mrr.services.cli.main.main`` (the real, nested ``mrr report render``
    entry point) and by this module's own standalone ``main`` (below), which
    both parse the SAME flags via ``_add_render_subparser``. Unconditional,
    like ``mrr.services.cli.export_main.run_command`` — "report" has exactly
    one nested subcommand ("render"), so by the time either caller reaches
    this function, that is the only possibility argparse's own
    ``required=True`` subparsers already enforced.
    """
    # --- 1. --output conflict check FIRST — the cheapest of all checks (a
    #        plain filesystem stat), task-packets/E8-T03.yaml R4 ordering.
    if output_file_conflict(args.output):
        print(
            f"mrr report render: --output {args.output} already exists — refusing to write "
            "over it.",
            file=sys.stderr,
        )
        return _EXIT_REPORT_REFUSED

    # --- 2. --classification-file: required-with-public / forbidden-with-
    #        internal, then read + parsed + validated (public only).
    disclosure: Disclosure = args.disclosure
    if disclosure == _DISCLOSURE_PUBLIC and args.classification_file is None:
        print(
            "mrr report render: --disclosure public requires --classification-file "
            "(an explicit governance input, never defaulted). Refusing to fabricate a "
            "substitute result (MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE
    if disclosure == _DISCLOSURE_INTERNAL and args.classification_file is not None:
        print(
            "mrr report render: --disclosure internal forbids --classification-file — an "
            "internal render ignores any attestation and must never appear to depend on one.",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE

    classification_by_object_id: dict[str, Classification] = {}
    if args.classification_file is not None:
        try:
            classification_by_object_id = _load_classification_file(args.classification_file)
        except ValueError as exc:
            print(
                f"mrr report render: {exc}. Refusing to fabricate a substitute result "
                "(MRR-NFR-012).",
                file=sys.stderr,
            )
            return _EXIT_DEPENDENCY_UNAVAILABLE

    # --- 3. Dependency check: the database must be reachable (MRR-NFR-012).
    try:
        engine = sa.create_engine(args.database_url)
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
    except SQLAlchemyError as exc:
        print(
            "mrr report render: cannot reach the PostgreSQL database at the given "
            f"--database-url ({exc}). Refusing to fabricate a substitute result (MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE

    # --- 4. Resolve the root and render exactly once.
    crate_rooted = args.crate_id is not None
    try:
        object_repository = PostgresObjectRepository(engine)
        edge_repository = PostgresEdgeRepository(engine)
        event_log = PostgresEventLog(engine)
        report_service = ReportService(object_repository, edge_repository, event_log)
        if crate_rooted:
            model = report_service.render(
                args.crate_id,
                disclosure=disclosure,
                classification_by_object_id=classification_by_object_id,
            )
        else:
            claim_ids = None if args.all_claims else args.claim_id
            model = report_service.render_from_claims(
                claim_ids,
                disclosure=disclosure,
                classification_by_object_id=classification_by_object_id,
            )
    except (DomainError, ValueError) as exc:
        print(
            f"mrr report render: render refused — {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return _EXIT_REPORT_REFUSED
    finally:
        engine.dispose()

    rendered = render_markdown(model) if args.format == _FORMAT_MARKDOWN else render_html(model)

    # --- 5. Atomic write: temp file in the same directory, os.replace last.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(
        dir=args.output.parent, prefix=f".{args.output.name}.report-tmp-"
    )
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(rendered)
        os.replace(tmp_path, args.output)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    section_counts = {
        "header": 1,
        "methods": 1,
        "claims": len(model.claims),
        "evidence_map": len(model.evidence_map.source_records)
        + len(model.evidence_map.evidence_anchors),
        "corrections": len(model.corrections),
        "known_unknowns": len(model.known_unknowns.crate_known_unknowns)
        + sum(len(row.known_unknowns) for row in model.known_unknowns.per_claim),
        "failures": len(model.failures),
        "provenance_summary": len(model.provenance_summary),
    }
    payload = {
        "root": "crate" if crate_rooted else "claims",
        "crate_id": args.crate_id,
        "claim_ids": [] if crate_rooted else [row.claim_id for row in model.claims],
        "output": str(args.output),
        "format": args.format,
        "disclosure": disclosure,
        "section_counts": section_counts,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser for this module alone (``prog="mrr report"``),
    usable directly (``python -m mrr.services.cli.report_main render ...``)
    without going through ``mrr.services.cli.main`` at all — mirrors
    ``mrr.services.cli.export_main.build_parser``'s identical shape.
    """
    parser = argparse.ArgumentParser(
        prog="mrr report",
        description="Research-report projection rendering (task-packets/E8-T03.yaml).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_render_subparser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "render":
        return run_command(args)

    parser.print_help()  # pragma: no cover - unreachable while "command" is required
    return 1


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
