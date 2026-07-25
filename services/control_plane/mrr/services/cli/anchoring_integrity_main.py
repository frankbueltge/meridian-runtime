"""``mrr audit anchoring`` (task-packets/N2-T02b.yaml R6): a thin argparse
CLI over ``mrr.services.anchoring_integrity.service.AnchoringIntegrityService``
plus ``mrr.domain.anchoring_integrity_report.render_markdown``/
``render_json`` — mirrors ``mrr.services.cli.citation_audit_main``'s/``mrr
.services.cli.field_observation_main``'s own shape (a sibling file, not
inlined into ``main.py``) and ``mrr.services.cli.main``'s MRR-NFR-012
"explicit degradation, never a fabricated substitute" discipline. No domain
behavior lives here: this module only parses arguments, checks dependencies
in cheapest-first order, calls ``AnchoringIntegrityService.build_report``
exactly once, renders exactly once, and reports the result or a clear,
typed failure.

--- `audit` is an EXISTING subcommand group — attached, not recreated -------

``mrr audit citations`` (N2-T01, ``mrr.services.cli.citation_audit_main``)
already owns the top-level ``"audit"`` subparser. That module is frozen
(task-packets/N2-T02b.yaml forbidden_changes) and this packet's own
allowed_paths does not include it, so :func:`register_anchoring_subcommand`
below does NOT call ``subparsers.add_parser("audit", ...)`` a second time
(argparse would reject that as a conflicting subparser name) — instead it
looks up the ALREADY-CREATED ``"audit"`` parser
(``citation_audit_main.register_audit_subcommand`` must have already run on
the same ``subparsers`` object) via its public ``.choices`` mapping, finds
the ``_SubParsersAction`` that parser already registered on itself, and
attaches ``"anchoring"`` onto that SAME action object. This changes nothing
about ``"citations"``'s own parser, dispatch, or behaviour — it only adds a
second valid choice for ``args.audit_command``.

--- No --database-url flag, deliberately -------------------------------------

Like ``mrr audit citations``/``mrr observe field``, this command never opens
a database connection at all (``AnchoringIntegrityService`` is DB-free AND
network-free by design) — so there is no ``--database-url`` flag here, and
no ``sqlalchemy.Engine`` is ever constructed by this module.

--- Ordering invariant (MRR-NFR-012): cheapest, local checks before expensive ones ---

task-packets/N2-T02b.yaml R6's own explicit ordering: (1) ``--output`` must
not already exist — a plain filesystem stat, the cheapest possible check;
(2) ``AnchoringIntegrityService.build_report`` — which itself reads/parses
the descriptor and hashes every declared dump, runs the fail-closed
integrity gate, and only then (if the gate is clean) parses each dump and
resolves every reference. A file-level problem there
(:class:`mrr.services.anchoring_integrity.service.AnchoringIntegrityInputError`)
is reported as a dependency-unavailable failure (exit 2); a fail-closed
gate mismatch (:class:`mrr.domain.anchoring_integrity.IntegrityGateError`)
or a dump-structure refusal
(:class:`mrr.domain.archive_dump.ArchiveDumpParseError`) is a REFUSAL
(exit 3), never a crash.

--- The exit-code map ---------------------------------------------------------

- ``0``: the report was built and rendered — INCLUDING when violations are
  found (task-packets/N2-T02b.yaml R6: "this packet audits and reports, it
  does not gate a run"). Prints the rendered report to stdout when
  ``--output`` is omitted; otherwise writes it atomically to ``--output``
  and prints a small JSON confirmation line instead.
- ``2``: a DEPENDENCY is unavailable — ``--batch`` or one of its declared
  dumps is missing, unreadable, not valid UTF-8/JSON, has the wrong
  top-level shape, or ``dumps[]`` is empty
  (``mrr.services.anchoring_integrity.service.AnchoringIntegrityInputError``).
  No in-memory or partial fallback exists for any of these (MRR-NFR-012).
- ``3``: the audit was REFUSED — an existing ``--output`` (checked FIRST,
  before the descriptor is even read), a fail-closed dump-hash mismatch
  (``mrr.domain.anchoring_integrity.IntegrityGateError``), or a
  structurally malformed dump
  (``mrr.domain.archive_dump.ArchiveDumpParseError``) — never a silent
  pass.
- argparse's own built-in failures (a bad flag, a missing required
  argument, an invalid ``--format`` choice) use argparse's own exit code, 2
  — the same overlap every other CLI module here already has.

--- Atomic write --------------------------------------------------------------

The rendered bytes are written to a temp file in the SAME directory as
``--output`` (same filesystem, hence ``os.replace`` is atomic), and
``os.replace`` is the LAST act — mirrors every other CLI module here's
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

from mrr.domain.anchoring_integrity import IntegrityGateError
from mrr.domain.anchoring_integrity_report import render_json, render_markdown
from mrr.domain.archive_dump import ArchiveDumpParseError
from mrr.domain.exceptions import DomainError
from mrr.services.anchoring_integrity.service import (
    AnchoringIntegrityInputError,
    AnchoringIntegrityService,
)

#: See the module docstring's "exit-code map" section.
_EXIT_DEPENDENCY_UNAVAILABLE = 2
_EXIT_REFUSED = 3

_FORMAT_MARKDOWN = "md"
_FORMAT_JSON = "json"


def output_file_conflict(output: Path) -> bool:
    """``True`` iff ``output`` already exists (file or directory) —
    task-packets/N2-T02b.yaml R6's own "cheapest local checks first".
    Mirrors ``mrr.services.cli.citation_audit_main.output_file_conflict``
    exactly.
    """
    return output.exists()


def _add_anchoring_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    anchoring_parser = subparsers.add_parser(
        "anchoring",
        help=(
            "Resolve every EvidenceAnchor to its SourceRecord and every Claim reference to "
            "its EvidenceAnchor over the committed archive dumps, behind a fail-closed "
            "file-hash gate (task-packets/N2-T02b.yaml). Read-only; opens no network or "
            "database connection. Verifies that an anchor points at a really archived "
            "source, NOT that the source supports the claim (N2-T03)."
        ),
    )
    anchoring_parser.add_argument(
        "--batch",
        required=True,
        type=Path,
        help=(
            "Path to the committed anchoring-batch descriptor (e.g. corpora/"
            "archive-integrity/anchoring-batch.v1.json). Its declared dump paths are "
            "resolved relative to this file's own directory, never the current working "
            "directory."
        ),
    )
    anchoring_parser.add_argument(
        "--format",
        choices=(_FORMAT_MARKDOWN, _FORMAT_JSON),
        default=_FORMAT_MARKDOWN,
        help="Output format: 'md' (Markdown, default) or 'json'.",
    )
    anchoring_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="File path the rendered report is written into. Defaults to stdout. Must not exist.",
    )


def _find_subparsers_action(
    parser: argparse.ArgumentParser,
) -> argparse._SubParsersAction[argparse.ArgumentParser]:
    """Return the ``_SubParsersAction`` ``parser`` already has registered on
    itself (i.e. the object ``parser.add_subparsers(...)`` returned when it
    was created) — used by :func:`register_anchoring_subcommand` to attach
    ``anchoring`` onto the EXISTING ``audit`` group without recreating it.
    See the module docstring's "`audit` is an EXISTING subcommand group"
    section for why this is necessary rather than a second ``add_parser
    ("audit", ...)`` call.
    """
    for action in parser._actions:  # noqa: SLF001 — see the docstring above
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError(f"{parser.prog!r} has no registered subparsers action to attach onto")


def register_anchoring_subcommand(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """The ONE call ``mrr.services.cli.main.build_parser`` makes, AFTER
    ``citation_audit_main.register_audit_subcommand`` has already created
    the ``"audit"`` group on this SAME ``subparsers`` object, to attach
    ``mrr audit anchoring`` onto it. Everything else about this
    subcommand's flags and behavior lives in this module; ``"citations"``'s
    own parser, dispatch, and behaviour are untouched.
    """
    audit_parser = subparsers.choices["audit"]
    audit_subparsers = _find_subparsers_action(audit_parser)
    _add_anchoring_subparser(audit_subparsers)


def run_command(args: argparse.Namespace) -> int:
    """The actual execution logic for ``mrr audit anchoring`` — called both
    by ``mrr.services.cli.main.main`` and by this module's own standalone
    ``main`` (below).
    """
    # --- 1. --output conflict check FIRST — the cheapest of all checks.
    if args.output is not None and output_file_conflict(args.output):
        print(
            f"mrr audit anchoring: --output {args.output} already exists — refusing to write "
            "over it.",
            file=sys.stderr,
        )
        return _EXIT_REFUSED

    # --- 2. Build the report: AnchoringIntegrityService reads/parses the
    #        descriptor and hashes every declared dump, then runs the
    #        fail-closed gate BEFORE any dump is ever parsed.
    try:
        report = AnchoringIntegrityService().build_report(args.batch)
    except AnchoringIntegrityInputError as exc:
        print(
            f"mrr audit anchoring: {exc}. Refusing to fabricate a substitute result (MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE
    except (IntegrityGateError, ArchiveDumpParseError, DomainError, ValueError) as exc:
        print(
            f"mrr audit anchoring: refused — {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return _EXIT_REFUSED

    rendered = render_markdown(report) if args.format == _FORMAT_MARKDOWN else render_json(report)

    # --- 3. Emit: stdout if no --output, else an atomic write. Exit 0 —
    #        INCLUDING when the report itself found violations; this
    #        command audits and reports, it does not gate a run.
    if args.output is None:
        sys.stdout.write(rendered)
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(
        dir=args.output.parent, prefix=f".{args.output.name}.anchoring-tmp-"
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
        "dump_anchors_matched": all(dump.file_anchor.matched for dump in report.dumps),
        "violations": {
            dump.schema_name: dump.violations.model_dump(mode="json") for dump in report.dumps
        },
        "observations": {
            dump.schema_name: dump.observations.model_dump(mode="json") for dump in report.dumps
        },
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser for this module alone (``prog="mrr audit"``),
    usable directly (``python -m mrr.services.cli.anchoring_integrity_main
    anchoring ...``) without going through ``mrr.services.cli.main`` at all
    — self-contained: it registers ONLY ``"anchoring"``, never
    ``"citations"`` (that remains ``citation_audit_main.build_parser``'s own
    standalone entry point).
    """
    parser = argparse.ArgumentParser(
        prog="mrr audit",
        description="Read-only archive-anchoring-integrity audit (task-packets/N2-T02b.yaml).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_anchoring_subparser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "anchoring":
        return run_command(args)

    parser.print_help()  # pragma: no cover - unreachable while "command" is required
    return 1


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
