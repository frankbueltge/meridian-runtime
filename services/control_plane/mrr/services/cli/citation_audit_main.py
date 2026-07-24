"""``mrr audit citations`` (task-packets/N2-T01.yaml R5): a thin argparse CLI
over ``mrr.services.citation_audit.service.CitationAuditService`` plus
``mrr.domain.citation_audit_report.render_markdown``/``render_json`` —
mirrors ``mrr.services.cli.validation_main``'s own shape (a sibling file, not
inlined into ``main.py``, so that module's own diff stays a one-line,
additive ``"audit"`` subparser registration) and ``mrr.services.cli.main``'s
MRR-NFR-012 "explicit degradation, never a fabricated substitute" discipline.
No domain behavior lives here: this module only parses arguments, checks
dependencies in cheapest-first order, calls
``CitationAuditService.build_report`` exactly once, renders exactly once, and
reports the result or a clear, typed failure.

--- No --database-url flag, deliberately -------------------------------------

Like ``mrr validate agreement``, this command never opens a database
connection at all (``CitationAuditService`` is DB-free AND network-free by
design — see that module's own docstring) — so there is no ``--database-url``
flag here, and no ``sqlalchemy.Engine`` is ever constructed by this module.

--- Ordering invariant (MRR-NFR-012): cheapest, local checks before expensive ones ---

task-packets/N2-T01.yaml R5's own explicit ordering: (1) ``--output`` must
not already exist — a plain filesystem stat, the cheapest possible check,
mirroring ``mrr.services.cli.validation_main.output_file_conflict``; (2)
``CitationAuditService.build_report`` — which itself reads and parses the
two input files before classifying anything. A file-level problem there
(:class:`mrr.services.citation_audit.service.CitationAuditInputError`) is
reported as a dependency-unavailable failure (exit 2); a structural gap
between the manifest and the snapshot
(:class:`mrr.domain.citation_audit.MissingResolutionError`) is a REFUSAL
(exit 3), never a crash.

--- The exit-code map ---------------------------------------------------------

- ``0``: the report was built and rendered. Prints the rendered report to
  stdout when ``--output`` is omitted; otherwise writes it atomically to
  ``--output`` and prints a small JSON confirmation line instead.
- ``2``: a DEPENDENCY is unavailable — ``--manifest`` or ``--snapshot`` is
  missing, unreadable, not valid UTF-8/JSON, or has the wrong top-level
  shape (``mrr.services.citation_audit.service.CitationAuditInputError``).
  No in-memory or partial fallback exists for any of these (MRR-NFR-012).
- ``3``: the audit was REFUSED — an existing ``--output`` (checked FIRST,
  before either input is even read), or a manifest citation with no matching
  resolution in the snapshot
  (``mrr.domain.citation_audit.MissingResolutionError``) — a structural gap,
  never a silent per-citation verdict.
- argparse's own built-in failures (a bad flag, a missing required
  argument, an invalid ``--format`` choice) use argparse's own exit code, 2
  — the same overlap every other CLI module here already has.

--- Atomic write --------------------------------------------------------------

The rendered bytes are written to a temp file in the SAME directory as
``--output`` (same filesystem, hence ``os.replace`` is atomic), and
``os.replace`` is the LAST act — mirrors ``mrr.services.cli.validation_main``'s
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

from mrr.domain.citation_audit import MissingResolutionError
from mrr.domain.citation_audit_report import render_json, render_markdown
from mrr.domain.exceptions import DomainError
from mrr.services.citation_audit.service import CitationAuditInputError, CitationAuditService

#: See the module docstring's "exit-code map" section.
_EXIT_DEPENDENCY_UNAVAILABLE = 2
_EXIT_REFUSED = 3

_FORMAT_MARKDOWN = "md"
_FORMAT_JSON = "json"


def output_file_conflict(output: Path) -> bool:
    """``True`` iff ``output`` already exists (file or directory) —
    task-packets/N2-T01.yaml R5's own "cheapest local checks first". Mirrors
    ``mrr.services.cli.validation_main.output_file_conflict`` exactly.
    """
    return output.exists()


def _add_citations_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    citations_parser = subparsers.add_parser(
        "citations",
        help=(
            "Classify every citation in a committed citation manifest against its committed "
            "resolution snapshot as resolved / not_found / title_mismatch / unverifiable / "
            "malformed (task-packets/N2-T01.yaml). Read-only; opens no network or database "
            "connection. Verifies EXISTENCE and TITLE only — never support or number "
            "consistency."
        ),
    )
    citations_parser.add_argument(
        "--manifest",
        required=True,
        type=Path,
        help=(
            "Path to the committed citation manifest (e.g. corpora/e2e-survey/citations"
            ".manifest.json)."
        ),
    )
    citations_parser.add_argument(
        "--snapshot",
        required=True,
        type=Path,
        help=(
            "Path to the committed resolution snapshot (e.g. corpora/e2e-survey/verification/"
            "resolution-snapshot.json). Never re-fetched by this command."
        ),
    )
    citations_parser.add_argument(
        "--format",
        choices=(_FORMAT_MARKDOWN, _FORMAT_JSON),
        default=_FORMAT_MARKDOWN,
        help="Output format: 'md' (Markdown, default) or 'json'.",
    )
    citations_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="File path the rendered report is written into. Defaults to stdout. Must not exist.",
    )


def register_audit_subcommand(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """The ONE call ``mrr.services.cli.main.build_parser`` makes to register
    ``mrr audit citations`` — everything else about this subcommand's flags
    and behavior lives in this module.
    """
    audit_parser = subparsers.add_parser(
        "audit",
        help="Read-only citation audit harness (task-packets/N2-T01.yaml).",
    )
    audit_subparsers = audit_parser.add_subparsers(dest="audit_command", required=True)
    _add_citations_subparser(audit_subparsers)


def run_command(args: argparse.Namespace) -> int:
    """The actual execution logic for ``mrr audit citations`` — called both
    by ``mrr.services.cli.main.main`` and by this module's own standalone
    ``main`` (below). Unconditional, like
    ``mrr.services.cli.validation_main.run_command`` — "audit" has exactly
    one nested subcommand ("citations") in this packet, so by the time
    either caller reaches this function that is the only possibility
    argparse's own ``required=True`` subparsers already enforced.
    """
    # --- 1. --output conflict check FIRST — the cheapest of all checks.
    if args.output is not None and output_file_conflict(args.output):
        print(
            f"mrr audit citations: --output {args.output} already exists — refusing to write "
            "over it.",
            file=sys.stderr,
        )
        return _EXIT_REFUSED

    # --- 2. Build the report: CitationAuditService reads/parses the two
    #        input files before classifying anything.
    try:
        report = CitationAuditService().build_report(args.manifest, args.snapshot)
    except CitationAuditInputError as exc:
        print(
            f"mrr audit citations: {exc}. Refusing to fabricate a substitute result (MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE
    except (MissingResolutionError, DomainError, ValueError) as exc:
        print(
            f"mrr audit citations: refused — {type(exc).__name__}: {exc}",
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
        dir=args.output.parent, prefix=f".{args.output.name}.audit-tmp-"
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
        "manifest": str(args.manifest),
        "snapshot": str(args.snapshot),
        "output": str(args.output),
        "format": args.format,
        "snapshot_sha256": report.snapshot_sha256,
        "summary": report.summary.model_dump(mode="json"),
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser for this module alone (``prog="mrr audit"``),
    usable directly (``python -m mrr.services.cli.citation_audit_main
    citations ...``) without going through ``mrr.services.cli.main`` at all
    — mirrors ``mrr.services.cli.validation_main.build_parser``'s identical
    shape.
    """
    parser = argparse.ArgumentParser(
        prog="mrr audit",
        description="Read-only citation audit harness (task-packets/N2-T01.yaml).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_citations_subparser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "citations":
        return run_command(args)

    parser.print_help()  # pragma: no cover - unreachable while "command" is required
    return 1


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
