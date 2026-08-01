"""``mrr validate agreement`` (task-packets/N1-T01.yaml R5): a thin argparse
CLI over ``mrr.services.validation.service.ValidationService`` plus
``mrr.domain.agreement_report.render_markdown``/``render_json`` — mirrors
``mrr.services.cli.report_main``'s own shape (a sibling file, not inlined
into ``main.py``, so that module's own diff stays a one-line, additive
``"validate"`` subparser registration) and ``mrr.services.cli.main``'s
MRR-NFR-012 "explicit degradation, never a fabricated substitute"
discipline. No domain behavior lives here: this module only parses
arguments, checks dependencies in cheapest-first order, calls
``ValidationService.build_report`` exactly once, renders exactly once, and
reports the result or a clear, typed failure.

--- No --database-url flag, deliberately -------------------------------------

Unlike every other CLI module in ``mrr.services.cli``, this one never opens
a database connection at all (``ValidationService`` is DB-free by design —
see that module's own docstring) — so there is no ``--database-url`` flag
here, and no ``sqlalchemy.Engine`` is ever constructed by this module.

--- Ordering invariant (MRR-NFR-012): cheapest, local checks before expensive ones ---

task-packets/N1-T01.yaml R5's own explicit ordering: (1) ``--output`` must
not already exist — a plain filesystem stat, the cheapest possible check,
mirroring ``mrr.services.cli.report_main.output_file_conflict``; (2)
``ValidationService.build_report`` — which itself reads and parses the
three input files (the crosswalk plus the two source files it names) before
doing any computation. A file-level problem there
(:class:`mrr.services.validation.service.AnalysisSetFileError`) is reported
as a dependency-unavailable failure (exit 2); anything past that point —
incomplete alignment, an unmapped raw label, or any
``mrr.domain.agreement`` typed error — is a REFUSAL (exit 3), never a crash.

--- The exit-code map ---------------------------------------------------------

- ``0``: the report was built and rendered. Prints the rendered report to
  stdout when ``--output`` is omitted; otherwise writes it atomically to
  ``--output`` and prints a small JSON confirmation line instead.
- ``2``: a DEPENDENCY is unavailable — the crosswalk file, or either source
  file it names, is missing, unreadable, unparseable, or has the wrong
  top-level shape (``mrr.services.validation.service.AnalysisSetFileError``).
  No in-memory or partial fallback exists for any of these (MRR-NFR-012).
- ``3``: the validation was REFUSED — an existing ``--output`` (checked
  FIRST, before the analysis-set is even read), an incomplete alignment, an
  unmapped raw label, or any other ``mrr.domain.exceptions.DomainError``/
  plain ``ValueError`` ``ValidationService.build_report`` lets propagate.
  Per task-packets/N1-T01.yaml R1's own explicit carve-out, an UNDEFINED
  kappa/alpha (zero expected-chance variation — e.g. the model-collapse
  theory stratum's own real data) is NOT a refusal: it is a successfully
  BUILT and rendered report whose affected metric fields are
  null-with-reason, exiting 0 like any other successful run.
- argparse's own built-in failures (a bad flag, a missing required
  argument, an invalid ``--format`` choice) use argparse's own exit code, 2
  — the same overlap every other CLI module here already has.

--- Atomic write --------------------------------------------------------------

The rendered bytes are written to a temp file in the SAME directory as
``--output`` (same filesystem, hence ``os.replace`` is atomic), and
``os.replace`` is the LAST act — mirrors ``mrr.services.cli.report_main``'s
identical discipline. A failure at any point before the replace leaves
``--output`` untouched.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from mrr.domain.agreement_report import render_json, render_markdown
from mrr.domain.exceptions import DomainError
from mrr.domain.gold_validity_report import render_json as render_gold_json
from mrr.domain.gold_validity_report import render_markdown as render_gold_markdown
from mrr.services.validation.gold_service import (
    GoldSetFileError,
    GoldValidityService,
)
from mrr.services.validation.service import AnalysisSetFileError, ValidationService

#: See the module docstring's "exit-code map" section.
_EXIT_DEPENDENCY_UNAVAILABLE = 2
_EXIT_REFUSED = 3

_FORMAT_MARKDOWN = "md"
_FORMAT_JSON = "json"


def output_file_conflict(output: Path) -> bool:
    """``True`` iff ``output`` already exists (file or directory) —
    task-packets/N1-T01.yaml R5's own "cheapest local checks first". Mirrors
    ``mrr.services.cli.report_main.output_file_conflict`` exactly.
    """
    return output.exists()


def _add_agreement_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    agreement_parser = subparsers.add_parser(
        "agreement",
        help=(
            "Compute a stratified, hash-anchored inter-instance agreement report over "
            "the model-collapse blind-vs-pipeline verification set (task-packets/"
            "N1-T01.yaml). Read-only; touches no database."
        ),
    )
    agreement_parser.add_argument(
        "--analysis-set",
        required=True,
        type=Path,
        help=(
            "Path to the declared, committed agreement crosswalk (e.g. corpora/"
            "model-collapse/verification/agreement-crosswalk.v1.json). Self-describing: "
            "it names the two source files it aligns, resolved relative to its own "
            "directory."
        ),
    )
    agreement_parser.add_argument(
        "--format",
        choices=(_FORMAT_MARKDOWN, _FORMAT_JSON),
        default=_FORMAT_MARKDOWN,
        help="Output format: 'md' (Markdown, default) or 'json'.",
    )
    agreement_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="File path the rendered report is written into. Defaults to stdout. Must not exist.",
    )


def _add_gold_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    gold_parser = subparsers.add_parser(
        "gold",
        help=(
            "Measure a set of evidence-relation classifications against a FROZEN gold "
            "standard (task-packets/N1-T02.yaml). Validity, not reliability. Read-only; "
            "touches no database, invokes no model, makes no network call."
        ),
    )
    gold_parser.add_argument(
        "--gold-set",
        required=True,
        type=Path,
        help=(
            "Path to the frozen gold-label set (schemas/gold-label-set.schema.json). Its "
            "bytes are hashed and, when --expect-sha256 is given, must match."
        ),
    )
    gold_parser.add_argument(
        "--predictions",
        required=True,
        type=Path,
        help=(
            'Path to the system\'s labels: {"system_id": ..., "predictions": '
            "{case_id: relation}}. Must cover exactly the gold set's cases."
        ),
    )
    gold_parser.add_argument(
        "--expect-sha256",
        default=None,
        help=(
            "The sha256 the gold set's bytes MUST have, as pinned in "
            "benchmarks/meridianbench/fixtures/FROZEN.json. A mismatch is a refusal: the "
            "standard moved."
        ),
    )
    gold_parser.add_argument(
        "--criteria",
        default=None,
        type=Path,
        help=(
            "Path to the criteria file the set names. When given, the set's own copies of the "
            "criteria lock are checked against it and any disagreement is refused — the lock "
            "instant lives in two places, and on 2026-08-01 the two came apart."
        ),
    )
    gold_parser.add_argument(
        "--allow-synthetic",
        action="store_true",
        help=(
            "Measure against a set whose provenance marks it a test fixture rather than a "
            "standard. Off by default; read no result from a run that needed it."
        ),
    )
    gold_parser.add_argument(
        "--format",
        choices=(_FORMAT_MARKDOWN, _FORMAT_JSON),
        default=_FORMAT_MARKDOWN,
        help="Output format: 'md' (Markdown, default) or 'json'.",
    )
    gold_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="File path the rendered report is written into. Defaults to stdout. Must not exist.",
    )


def register_validation_subcommand(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """The ONE call ``mrr.services.cli.main.build_parser`` makes to register
    ``mrr validate agreement`` and ``mrr validate gold`` — everything else
    about these subcommands' flags and behavior lives in this module.

    The two are deliberately siblings under one ``validate`` group and are
    deliberately NOT one command with a flag: ``agreement`` reports
    reliability between two instances, ``gold`` reports validity against a
    standard, and those are different claims. Keeping them separate at the
    command line is the same boundary
    :mod:`mrr.domain.gold_validity_report` keeps in the type system.
    """
    validate_parser = subparsers.add_parser(
        "validate",
        help="Read-only validation harness (task-packets/N1-T01.yaml, N1-T02.yaml).",
    )
    validate_subparsers = validate_parser.add_subparsers(dest="validate_command", required=True)
    _add_agreement_subparser(validate_subparsers)
    _add_gold_subparser(validate_subparsers)


def _selected_subcommand(args: argparse.Namespace) -> str:
    """Which nested ``validate`` subcommand was chosen.

    ``mrr.services.cli.main``'s parser records it as ``validate_command``;
    this module's own standalone parser records it as ``command``. Reading
    both, rather than making the two parsers agree, keeps
    ``register_validation_subcommand``'s contract with ``main.py``
    unchanged — that dest name is what ``main.py`` already routes on for
    every other group.
    """
    return str(getattr(args, "validate_command", None) or getattr(args, "command", ""))


def run_command(args: argparse.Namespace) -> int:
    """Dispatch to the chosen ``mrr validate`` subcommand — called both by
    ``mrr.services.cli.main.main`` and by this module's own standalone
    ``main`` (below).

    No longer unconditional as in N1-T01: "validate" now has two nested
    subcommands, and argparse's own ``required=True`` guarantees one of them
    was chosen by the time this function runs.
    """
    if _selected_subcommand(args) == "gold":
        return run_gold_command(args)
    return run_agreement_command(args)


def run_agreement_command(args: argparse.Namespace) -> int:
    """``mrr validate agreement`` — inter-instance RELIABILITY (N1-T01).
    Behaviour unchanged from N1-T01; only the function's name is new, so the
    sibling ``gold`` command can live beside it without either reading as the
    default.
    """
    # --- 1. --output conflict check FIRST — the cheapest of all checks.
    if args.output is not None and output_file_conflict(args.output):
        print(
            f"mrr validate agreement: --output {args.output} already exists — refusing to "
            "write over it.",
            file=sys.stderr,
        )
        return _EXIT_REFUSED

    # --- 2. Build the report: ValidationService reads/parses the three
    #        input files, validates alignment, and computes every metric.
    try:
        report = ValidationService().build_report(args.analysis_set)
    except AnalysisSetFileError as exc:
        print(
            f"mrr validate agreement: {exc}. Refusing to fabricate a substitute result "
            "(MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE
    except (DomainError, ValueError) as exc:
        print(
            f"mrr validate agreement: refused — {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return _EXIT_REFUSED

    rendered = render_markdown(report) if args.format == _FORMAT_MARKDOWN else render_json(report)

    # --- 3. Emit: stdout if no --output, else an atomic write.
    if args.output is None:
        sys.stdout.write(rendered)
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(
        dir=args.output.parent, prefix=f".{args.output.name}.validate-tmp-"
    )
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(rendered)
        os.replace(tmp_path, args.output)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    payload = {
        "analysis_set": str(args.analysis_set),
        "output": str(args.output),
        "format": args.format,
        "crosswalk_sha256": report.crosswalk_sha256,
        "strata": [stratum.stratum_id for stratum in report.strata],
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


def _write_atomically(output: Path, rendered: str) -> None:
    """Write ``rendered`` to ``output`` via a temp file in the SAME directory,
    with ``os.replace`` as the last act — identical discipline to
    :func:`run_agreement_command`'s own write, factored out so the two
    subcommands cannot drift apart on the one operation that must not be got
    wrong.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(dir=output.parent, prefix=f".{output.name}.validate-tmp-")
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(rendered)
        os.replace(tmp_path, output)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def run_gold_command(args: argparse.Namespace) -> int:
    """``mrr validate gold`` — VALIDITY against a frozen gold standard
    (task-packets/N1-T02.yaml R6).

    Same ordering discipline as its sibling: the cheapest local check first,
    then the loads, then the computation. Same exit-code map, with the three
    N1-T02 refusals (moved standard, violated order gate, synthetic set used
    as a standard) all landing on 3 — each is a RESULT about the standard,
    not a tool failure, and each names itself on stderr.
    """
    # --- 1. --output conflict check FIRST — the cheapest of all checks.
    if args.output is not None and output_file_conflict(args.output):
        print(
            f"mrr validate gold: --output {args.output} already exists — refusing to "
            "write over it.",
            file=sys.stderr,
        )
        return _EXIT_REFUSED

    service = GoldValidityService()

    # --- 2. Load the standard and the predictions. A file that cannot be read
    #        at all is a dependency problem (2); anything the standard itself
    #        is wrong about is a refusal (3).
    try:
        gold_set = service.load_gold_set(
            args.gold_set,
            expected_sha256=args.expect_sha256,
            allow_synthetic=args.allow_synthetic,
            criteria_path=args.criteria,
        )
        system_id, predictions = service.load_predictions(args.predictions)
    except GoldSetFileError as exc:
        print(
            f"mrr validate gold: {exc}. Refusing to fabricate a substitute result (MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE
    except (DomainError, ValueError) as exc:
        print(
            f"mrr validate gold: refused — {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return _EXIT_REFUSED

    # --- 3. Measure.
    try:
        report = service.build_report(gold_set, system_id=system_id, predictions=predictions)
    except (DomainError, ValueError) as exc:
        print(
            f"mrr validate gold: refused — {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return _EXIT_REFUSED

    rendered = (
        render_gold_markdown(report)
        if args.format == _FORMAT_MARKDOWN
        else render_gold_json(report)
    )

    if args.output is None:
        sys.stdout.write(rendered)
        return 0

    _write_atomically(args.output, rendered)

    payload = {
        "gold_set": str(args.gold_set),
        "predictions": str(args.predictions),
        "output": str(args.output),
        "format": args.format,
        "fixture_set_id": report.fixture_set_id,
        "n": report.n,
        "observed_agreement": report.observed_agreement,
        "majority_baseline": report.majority_baseline,
        "below_power": report.below_power,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser for this module alone (``prog="mrr validate"``),
    usable directly (``python -m mrr.services.cli.validation_main
    agreement ...``) without going through ``mrr.services.cli.main`` at
    all — mirrors ``mrr.services.cli.report_main.build_parser``'s identical
    shape.
    """
    parser = argparse.ArgumentParser(
        prog="mrr validate",
        description="Read-only validation harness (task-packets/N1-T01.yaml, N1-T02.yaml).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_agreement_subparser(subparsers)
    _add_gold_subparser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command in ("agreement", "gold"):
        return run_command(args)

    parser.print_help()  # pragma: no cover - unreachable while "command" is required
    return 1


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
