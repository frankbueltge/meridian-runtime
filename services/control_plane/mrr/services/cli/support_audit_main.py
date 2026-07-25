"""``mrr audit support`` (task-packets/N2-T03b.yaml): a thin argparse CLI
over ``mrr.services.support_audit.service.SupportAuditService`` plus
``mrr.domain.support_audit_report.render_markdown``/``render_json`` —
mirrors ``mrr.services.cli.anchoring_integrity_main``'s/``mrr.services.cli
.citation_audit_main``'s own shape (a sibling file, not inlined into
``main.py``) and ``mrr.services.cli.main``'s MRR-NFR-012 "explicit
degradation, never a fabricated substitute" discipline. No domain behavior
lives here: this module only parses arguments, checks dependencies in
cheapest-first order, calls ``SupportAuditService.build_report`` exactly
once, renders exactly once, and reports the result or a clear, typed
failure.

--- `audit` is an EXISTING subcommand group — attached, not recreated -------

``mrr audit citations`` (N2-T01, ``mrr.services.cli.citation_audit_main``)
already owns the top-level ``"audit"`` subparser, and ``mrr audit
anchoring`` (N2-T02b, ``mrr.services.cli.anchoring_integrity_main``) already
attaches onto it. Both are frozen (task-packets/N2-T03b.yaml
forbidden_changes for ``citation_audit_main``; ``anchoring_integrity_main``
is untouched by this packet's own allowed_paths) and this packet does not
recreate the ``"audit"`` group — :func:`register_support_subcommand` below
looks up the ALREADY-CREATED ``"audit"`` parser via its public ``.choices``
mapping, finds the ``_SubParsersAction`` that parser already registered on
itself, and attaches ``"support"`` onto that SAME action object, exactly
mirroring ``anchoring_integrity_main.register_anchoring_subcommand``'s
identical pattern. This changes nothing about ``"citations"``'s or
``"anchoring"``'s own parsers, dispatch, or behaviour — it only adds a third
valid choice for ``args.audit_command``.

--- No --database-url flag, deliberately -------------------------------------

Like ``mrr audit citations``/``mrr audit anchoring``, this command never
opens a database connection at all (``SupportAuditService`` is DB-free,
network-free, AND model-free by design) — so there is no ``--database-url``
flag here, and no ``sqlalchemy.Engine`` is ever constructed by this module.

--- Ordering invariant (MRR-NFR-012): cheapest, local checks before expensive ones ---

(1) ``--output`` must not already exist — a plain filesystem stat, the
cheapest possible check; (2) ``SupportAuditService.build_report`` — which
itself reads/parses the descriptor and hashes its two declared inputs, runs
the fail-closed integrity gate, and only then (if the gate is clean) parses
both inputs and evaluates every claim. A file-level problem there
(:class:`mrr.services.support_audit.service.SupportAuditInputError`) is
reported as a dependency-unavailable failure (exit 2); a fail-closed gate
mismatch (:class:`mrr.domain.support_audit.IntegrityGateError`) is a
REFUSAL (exit 3), never a crash.

--- The exit-code map (task-packets/N2-T03b.yaml acceptance_criteria) --------

- ``0``: the report was built and rendered — INCLUDING when the report finds
  ``quotation_altered`` violations. This command audits and reports, it does
  not gate a run: found violations do NOT change the exit code (exactly the
  same discipline as N2-T01/N2-T02b). Prints the rendered report to stdout
  when ``--output`` is omitted; otherwise writes it atomically to
  ``--output`` and prints a small JSON confirmation line instead.
- ``2``: a DEPENDENCY is unavailable — ``--batch`` or one of its two
  declared inputs is missing, unreadable, not valid UTF-8/JSON, has the
  wrong top-level shape, or a claim manifest entry names a citation_id with
  no matching content-snapshot entry at all
  (``mrr.services.support_audit.service.SupportAuditInputError``). No
  in-memory or partial fallback exists for any of these (MRR-NFR-012).
- ``3``: the audit was REFUSED — an existing ``--output`` (checked FIRST,
  before the descriptor is even read), or a fail-closed input-hash mismatch
  (``mrr.domain.support_audit.IntegrityGateError``) — never a silent pass.
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

from mrr.domain.exceptions import DomainError
from mrr.domain.support_audit import IntegrityGateError
from mrr.domain.support_audit_report import render_json, render_markdown
from mrr.services.support_audit.service import SupportAuditInputError, SupportAuditService

#: See the module docstring's "exit-code map" section.
_EXIT_DEPENDENCY_UNAVAILABLE = 2
_EXIT_REFUSED = 3

_FORMAT_MARKDOWN = "md"
_FORMAT_JSON = "json"


def output_file_conflict(output: Path) -> bool:
    """``True`` iff ``output`` already exists (file or directory) — the
    cheapest local check, run first. Mirrors ``mrr.services.cli
    .anchoring_integrity_main.output_file_conflict``/``mrr.services.cli
    .citation_audit_main.output_file_conflict`` exactly.
    """
    return output.exists()


def _add_support_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    support_parser = subparsers.add_parser(
        "support",
        help=(
            "Decide, for every hand-transcribed figure and verbatim quotation claim, whether "
            "the checked excerpt (the abstract captured by N2-T03a) carries it — behind a "
            "fail-closed file-hash gate (task-packets/N2-T03b.yaml). Read-only; opens no "
            "network, database, or model connection. Verifies presence in an abstract, NOT "
            "that the source supports the claim in substance; reports absence as an "
            "observation, never as refutation."
        ),
    )
    support_parser.add_argument(
        "--batch",
        required=True,
        type=Path,
        help=(
            "Path to the committed support-batch descriptor (e.g. corpora/research-records/"
            "support-batch.v1.json). Its two declared input paths are resolved relative to "
            "this file's own directory, never the current working directory."
        ),
    )
    support_parser.add_argument(
        "--format",
        choices=(_FORMAT_MARKDOWN, _FORMAT_JSON),
        default=_FORMAT_MARKDOWN,
        help="Output format: 'md' (Markdown, default) or 'json'.",
    )
    support_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="File path the rendered report is written into. Defaults to stdout. Must not exist.",
    )


def _find_subparsers_action(
    parser: argparse.ArgumentParser,
) -> argparse._SubParsersAction[argparse.ArgumentParser]:
    """Return the ``_SubParsersAction`` ``parser`` already has registered on
    itself — used by :func:`register_support_subcommand` to attach
    ``support`` onto the EXISTING ``audit`` group without recreating it.
    Mirrors ``mrr.services.cli.anchoring_integrity_main._find_subparsers_action``
    exactly.
    """
    for action in parser._actions:  # noqa: SLF001 — see the docstring above
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError(f"{parser.prog!r} has no registered subparsers action to attach onto")


def register_support_subcommand(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """The ONE call ``mrr.services.cli.main.build_parser`` makes, AFTER
    ``citation_audit_main.register_audit_subcommand`` (and, in whatever
    order that module registers it, ``anchoring_integrity_main
    .register_anchoring_subcommand``) have already created/attached onto the
    ``"audit"`` group on this SAME ``subparsers`` object, to attach
    ``mrr audit support`` onto it. Everything else about this subcommand's
    flags and behavior lives in this module; ``"citations"``'s and
    ``"anchoring"``'s own parsers, dispatch, and behaviour are untouched.
    """
    audit_parser = subparsers.choices["audit"]
    audit_subparsers = _find_subparsers_action(audit_parser)
    _add_support_subparser(audit_subparsers)


def run_command(args: argparse.Namespace) -> int:
    """The actual execution logic for ``mrr audit support`` — called both by
    ``mrr.services.cli.main.main`` and by this module's own standalone
    ``main`` (below).
    """
    # --- 1. --output conflict check FIRST — the cheapest of all checks.
    if args.output is not None and output_file_conflict(args.output):
        print(
            f"mrr audit support: --output {args.output} already exists — refusing to write "
            "over it.",
            file=sys.stderr,
        )
        return _EXIT_REFUSED

    # --- 2. Build the report: SupportAuditService reads/parses the
    #        descriptor and hashes its two declared inputs, then runs the
    #        fail-closed gate BEFORE either input is ever parsed as domain
    #        data.
    try:
        report = SupportAuditService().build_report(args.batch)
    except SupportAuditInputError as exc:
        print(
            f"mrr audit support: {exc}. Refusing to fabricate a substitute result (MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE
    except (IntegrityGateError, DomainError, ValueError) as exc:
        print(
            f"mrr audit support: refused — {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return _EXIT_REFUSED

    rendered = render_markdown(report) if args.format == _FORMAT_MARKDOWN else render_json(report)

    # --- 3. Emit: stdout if no --output, else an atomic write. Exit 0 —
    #        INCLUDING when the report itself found quotation_altered
    #        violations; this command audits and reports, it does not gate
    #        a run.
    if args.output is None:
        sys.stdout.write(rendered)
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(
        dir=args.output.parent, prefix=f".{args.output.name}.support-tmp-"
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
        "counts": report.counts.model_dump(mode="json"),
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser for this module alone (``prog="mrr audit"``),
    usable directly (``python -m mrr.services.cli.support_audit_main
    support ...``) without going through ``mrr.services.cli.main`` at all —
    self-contained: it registers ONLY ``"support"``, never ``"citations"``
    or ``"anchoring"``.
    """
    parser = argparse.ArgumentParser(
        prog="mrr audit",
        description="Read-only, model-free support audit (task-packets/N2-T03b.yaml).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_support_subparser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "support":
        return run_command(args)

    parser.print_help()  # pragma: no cover - unreachable while "command" is required
    return 1


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
