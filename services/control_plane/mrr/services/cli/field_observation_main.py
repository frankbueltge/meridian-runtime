"""``mrr observe field`` (task-packets/R2-T01.yaml R5): a thin argparse CLI
over ``mrr.services.field_observation.service.FieldObservationService`` plus
``mrr.domain.field_observation_report.render_markdown``/``render_json`` —
mirrors ``mrr.services.cli.citation_audit_main``'s own shape (a sibling
file, not inlined into ``main.py``, so that module's own diff stays a
one-line, additive ``"observe"`` subparser registration) and
``mrr.services.cli.main``'s MRR-NFR-012 "explicit degradation, never a
fabricated substitute" discipline. No domain behavior lives here: this
module only parses arguments, checks dependencies in cheapest-first order,
calls ``FieldObservationService.build_report`` exactly once, renders
exactly once, and reports the result or a clear, typed failure.

--- No --database-url flag, deliberately -------------------------------------

Like ``mrr audit citations``/``mrr validate agreement``, this command never
opens a database connection at all (``FieldObservationService`` is DB-free
AND network-free by design — see that module's own docstring) — so there is
no ``--database-url`` flag here, and no ``sqlalchemy.Engine`` is ever
constructed by this module.

--- Ordering invariant (MRR-NFR-012): cheapest, local checks before expensive ones ---

task-packets/R2-T01.yaml R5's own explicit ordering: (1) ``--output`` must
not already exist — a plain filesystem stat, the cheapest possible check,
mirroring ``mrr.services.cli.citation_audit_main.output_file_conflict``; (2)
``FieldObservationService.build_report`` — which itself reads and parses
the descriptor and its declared inputs, runs the fail-closed integrity
gate, and only then (if the gate is clean) reuses the frozen N2 evaluator. A
file-level problem there
(:class:`mrr.services.field_observation.service.FieldObservationInputError`)
is reported as a dependency-unavailable failure (exit 2); an integrity-gate
mismatch (:class:`mrr.domain.field_observation.IntegrityGateError`) or a
structural gap the reused N2 evaluator finds
(:class:`mrr.domain.citation_audit.MissingResolutionError`) is a REFUSAL
(exit 3), never a crash.

--- The exit-code map ---------------------------------------------------------

- ``0``: the report was built and rendered. Prints the rendered report to
  stdout when ``--output`` is omitted; otherwise writes it atomically to
  ``--output`` and prints a small JSON confirmation line instead.
- ``2``: a DEPENDENCY is unavailable — ``--batch`` or one of its declared
  inputs is missing, unreadable, not valid UTF-8/JSON, or has the wrong
  top-level shape
  (``mrr.services.field_observation.service.FieldObservationInputError``).
  No in-memory or partial fallback exists for any of these (MRR-NFR-012).
- ``3``: the observation was REFUSED — an existing ``--output`` (checked
  FIRST, before the descriptor is even read), a fail-closed integrity-anchor
  mismatch (``mrr.domain.field_observation.IntegrityGateError``), or a
  manifest citation with no matching resolution in the snapshot
  (``mrr.domain.citation_audit.MissingResolutionError``, propagated
  unchanged from the reused N2 evaluator) — never a silent pass.
- argparse's own built-in failures (a bad flag, a missing required
  argument, an invalid ``--format`` choice) use argparse's own exit code, 2
  — the same overlap every other CLI module here already has.

--- Atomic write --------------------------------------------------------------

The rendered bytes are written to a temp file in the SAME directory as
``--output`` (same filesystem, hence ``os.replace`` is atomic), and
``os.replace`` is the LAST act — mirrors ``mrr.services.cli
.citation_audit_main``'s identical discipline. A failure at any point
before the replace leaves ``--output`` untouched.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from mrr.domain.citation_audit import MissingResolutionError
from mrr.domain.exceptions import DomainError
from mrr.domain.field_observation import IntegrityGateError
from mrr.domain.field_observation_report import render_json, render_markdown
from mrr.services.field_observation.service import (
    FieldObservationInputError,
    FieldObservationService,
)

#: See the module docstring's "exit-code map" section.
_EXIT_DEPENDENCY_UNAVAILABLE = 2
_EXIT_REFUSED = 3

_FORMAT_MARKDOWN = "md"
_FORMAT_JSON = "json"


def output_file_conflict(output: Path) -> bool:
    """``True`` iff ``output`` already exists (file or directory) —
    task-packets/R2-T01.yaml R5's own "cheapest local checks first". Mirrors
    ``mrr.services.cli.citation_audit_main.output_file_conflict`` exactly.
    """
    return output.exists()


def _add_field_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    field_parser = subparsers.add_parser(
        "field",
        help=(
            "Integrity-verify a committed, hash-anchored observation-batch descriptor "
            "fail-closed, then reuse the frozen N2 citation-audit evaluator over its "
            "manifest+snapshot (task-packets/R2-T01.yaml). Read-only; opens no network "
            "or database connection. Observes only: emits no self-modification proposal "
            "and runs no optimizer against the evaluator."
        ),
    )
    field_parser.add_argument(
        "--batch",
        required=True,
        type=Path,
        help=(
            "Path to the committed observation-batch descriptor (e.g. corpora/e2e-survey/"
            "observation-batch.v1.json). Its declared input paths are resolved relative to "
            "this file's own directory, never the current working directory."
        ),
    )
    field_parser.add_argument(
        "--format",
        choices=(_FORMAT_MARKDOWN, _FORMAT_JSON),
        default=_FORMAT_MARKDOWN,
        help="Output format: 'md' (Markdown, default) or 'json'.",
    )
    field_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="File path the rendered report is written into. Defaults to stdout. Must not exist.",
    )


def register_observe_subcommand(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """The ONE call ``mrr.services.cli.main.build_parser`` makes to register
    ``mrr observe field`` — everything else about this subcommand's flags
    and behavior lives in this module.
    """
    observe_parser = subparsers.add_parser(
        "observe",
        help="Read-only field-observation harness (task-packets/R2-T01.yaml).",
    )
    observe_subparsers = observe_parser.add_subparsers(dest="observe_command", required=True)
    _add_field_subparser(observe_subparsers)


def run_command(args: argparse.Namespace) -> int:
    """The actual execution logic for ``mrr observe field`` — called both
    by ``mrr.services.cli.main.main`` and by this module's own standalone
    ``main`` (below). Unconditional, like
    ``mrr.services.cli.citation_audit_main.run_command`` — "observe" has
    exactly one nested subcommand ("field") in this packet, so by the time
    either caller reaches this function that is the only possibility
    argparse's own ``required=True`` subparsers already enforced.
    """
    # --- 1. --output conflict check FIRST — the cheapest of all checks.
    if args.output is not None and output_file_conflict(args.output):
        print(
            f"mrr observe field: --output {args.output} already exists — refusing to write "
            "over it.",
            file=sys.stderr,
        )
        return _EXIT_REFUSED

    # --- 2. Build the report: FieldObservationService reads/parses the
    #        descriptor and its declared inputs, then runs the fail-closed
    #        integrity gate BEFORE the frozen N2 evaluator is ever reached.
    try:
        report = FieldObservationService().build_report(args.batch)
    except FieldObservationInputError as exc:
        print(
            f"mrr observe field: {exc}. Refusing to fabricate a substitute result (MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE
    except IntegrityGateError as exc:
        print(
            f"mrr observe field: refused — integrity gate failed: {exc}",
            file=sys.stderr,
        )
        return _EXIT_REFUSED
    except (MissingResolutionError, DomainError, ValueError) as exc:
        print(
            f"mrr observe field: refused — {type(exc).__name__}: {exc}",
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
        dir=args.output.parent, prefix=f".{args.output.name}.observe-tmp-"
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
        "batch": str(args.batch),
        "output": str(args.output),
        "format": args.format,
        "batch_id": report.batch_id,
        "anchors_matched": all(row.matched for row in report.anchors),
        "citation_summary": report.citation_audit.summary.model_dump(mode="json"),
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser for this module alone (``prog="mrr observe"``),
    usable directly (``python -m mrr.services.cli.field_observation_main
    field ...``) without going through ``mrr.services.cli.main`` at all —
    mirrors ``mrr.services.cli.citation_audit_main.build_parser``'s
    identical shape.
    """
    parser = argparse.ArgumentParser(
        prog="mrr observe",
        description="Read-only field-observation harness (task-packets/R2-T01.yaml).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_field_subparser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "field":
        return run_command(args)

    parser.print_help()  # pragma: no cover - unreachable while "command" is required
    return 1


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
