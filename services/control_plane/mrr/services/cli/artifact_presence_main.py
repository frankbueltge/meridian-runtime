"""``mrr audit artifacts`` (task-packets/A2-T01.yaml, "Teil 2 — Nachsehen"):
a thin argparse CLI over ``mrr.services.artifact_presence.service
.ArtifactPresenceService`` plus ``mrr.domain.artifact_presence_report
.render_markdown``/``render_json`` — mirrors ``mrr.services.cli
.support_audit_main``'s/``mrr.services.cli.anchoring_integrity_main``'s own
shape (a sibling file, not inlined into ``main.py``) and ``mrr.services.cli
.main``'s MRR-NFR-012 "explicit degradation, never a fabricated substitute"
discipline. No domain behavior lives here: this module only parses
arguments, checks dependencies in cheapest-first order, calls
``ArtifactPresenceService.build_report`` exactly once, renders exactly once,
and reports the result or a clear, typed failure.

--- `audit` is an EXISTING subcommand group — attached, not recreated -------

``mrr audit citations`` (N2-T01) already owns the top-level ``"audit"``
subparser; ``mrr audit anchoring`` (N2-T02b) and ``mrr audit support``
(N2-T03b) already attach onto it. All three are outside this packet's own
``allowed_paths`` and stay untouched — :func:`register_artifacts_subcommand`
below looks up the ALREADY-CREATED ``"audit"`` parser via its public
``.choices`` mapping, finds the ``_SubParsersAction`` that parser already
registered on itself, and attaches ``"artifacts"`` onto that SAME action
object, exactly mirroring ``support_audit_main
.register_support_subcommand``'s identical pattern. This changes nothing
about ``"citations"``'s/``"anchoring"``'s/``"support"``'s own parsers,
dispatch, or behaviour — it only adds a fourth valid choice for
``args.audit_command``.

--- No --database-url flag, deliberately -------------------------------------

Like its three siblings, this command never opens a database connection at
all (``ArtifactPresenceService`` is DB-free AND network-free by design) —
so there is no ``--database-url`` flag here, and no ``sqlalchemy.Engine`` is
ever constructed by this module.

--- Ordering invariant (MRR-NFR-012): cheapest, local checks before expensive ones ---

(1) ``--output`` must not already exist — a plain filesystem stat, the
cheapest possible check; (2) ``ArtifactPresenceService.build_report`` —
which itself reads/parses the dump, resolves the recorded root, and — for
every EvidenceAnchor with a ``snapshot_hash`` — checks the derived blob
path's presence and hash. A file-level problem there
(:class:`mrr.services.artifact_presence.service.ArtifactPresenceInputError`)
is reported as a dependency-unavailable failure (exit 2); a structural
refusal about the DUMP's own data
(:class:`mrr.domain.archive_dump.ArchiveDumpParseError`,
:class:`mrr.domain.artifact_presence.AmbiguousArtifactStoreRootError`) is a
REFUSAL (exit 3), never a crash.

--- The exit-code map ---------------------------------------------------------

- ``0``: the report was built and rendered — INCLUDING when violations are
  found (this command audits and reports, it does not gate a run — the same
  discipline as N2-T01/N2-T02b/N2-T03b). Prints the rendered report to
  stdout when ``--output`` is omitted; otherwise writes it atomically to
  ``--output`` and prints a small JSON confirmation line instead.
- ``2``: a DEPENDENCY is unavailable — ``--dump`` is missing, unreadable, or
  not valid UTF-8
  (``mrr.services.artifact_presence.service.ArtifactPresenceInputError``).
  No in-memory or partial fallback exists for this (MRR-NFR-012).
- ``3``: the audit was REFUSED — an existing ``--output`` (checked FIRST,
  before the dump is even read), a structurally malformed dump
  (``mrr.domain.archive_dump.ArchiveDumpParseError``), or an ambiguous
  recorded root across the dump's RunManifest objects
  (``mrr.domain.artifact_presence.AmbiguousArtifactStoreRootError``) —
  never a silent pass.
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

from mrr.domain.archive_dump import ArchiveDumpParseError
from mrr.domain.artifact_presence import AmbiguousArtifactStoreRootError
from mrr.domain.artifact_presence_report import render_json, render_markdown
from mrr.domain.exceptions import DomainError
from mrr.services.artifact_presence.service import (
    ArtifactPresenceInputError,
    ArtifactPresenceService,
)

#: See the module docstring's "exit-code map" section.
_EXIT_DEPENDENCY_UNAVAILABLE = 2
_EXIT_REFUSED = 3

_FORMAT_MARKDOWN = "md"
_FORMAT_JSON = "json"


def output_file_conflict(output: Path) -> bool:
    """``True`` iff ``output`` already exists (file or directory) — the
    cheapest local check, run first. Mirrors ``mrr.services.cli
    .support_audit_main.output_file_conflict``/``mrr.services.cli
    .anchoring_integrity_main.output_file_conflict`` exactly.
    """
    return output.exists()


def _add_artifacts_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    artifacts_parser = subparsers.add_parser(
        "artifacts",
        help=(
            "For every EvidenceAnchor with a snapshot_hash in a committed archive dump, "
            "derive the expected content-addressed blob path from the dump's recorded "
            "artifact-store root and check its presence and hash (task-packets/A2-T01.yaml). "
            "Read-only; opens no network or database connection, never writes to the "
            "filesystem. A run that recorded no root is reported as an OBSERVATION, never a "
            "violation."
        ),
    )
    artifacts_parser.add_argument(
        "--dump",
        required=True,
        type=Path,
        help=(
            "Path to a committed archive dump (e.g. archive/dumps/mrr_k1t04_real_run_v2.sql). "
            "Read directly — no batch descriptor, no fail-closed dump-hash gate."
        ),
    )
    artifacts_parser.add_argument(
        "--format",
        choices=(_FORMAT_MARKDOWN, _FORMAT_JSON),
        default=_FORMAT_MARKDOWN,
        help="Output format: 'md' (Markdown, default) or 'json'.",
    )
    artifacts_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="File path the rendered report is written into. Defaults to stdout. Must not exist.",
    )


def _find_subparsers_action(
    parser: argparse.ArgumentParser,
) -> argparse._SubParsersAction[argparse.ArgumentParser]:
    """Return the ``_SubParsersAction`` ``parser`` already has registered on
    itself — used by :func:`register_artifacts_subcommand` to attach
    ``artifacts`` onto the EXISTING ``audit`` group without recreating it.
    Mirrors ``mrr.services.cli.support_audit_main._find_subparsers_action``/
    ``mrr.services.cli.anchoring_integrity_main._find_subparsers_action``
    exactly.
    """
    for action in parser._actions:  # noqa: SLF001 — see the docstring above
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError(f"{parser.prog!r} has no registered subparsers action to attach onto")


def register_artifacts_subcommand(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """The ONE call ``mrr.services.cli.main.build_parser`` makes, AFTER
    ``citation_audit_main``/``anchoring_integrity_main``/
    ``support_audit_main`` have already created/attached onto the
    ``"audit"`` group on this SAME ``subparsers`` object, to attach
    ``mrr audit artifacts`` onto it. Everything else about this subcommand's
    flags and behavior lives in this module; the three siblings' own
    parsers, dispatch, and behaviour are untouched.
    """
    audit_parser = subparsers.choices["audit"]
    audit_subparsers = _find_subparsers_action(audit_parser)
    _add_artifacts_subparser(audit_subparsers)


def run_command(args: argparse.Namespace) -> int:
    """The actual execution logic for ``mrr audit artifacts`` — called both
    by ``mrr.services.cli.main.main`` and by this module's own standalone
    ``main`` (below).
    """
    # --- 1. --output conflict check FIRST — the cheapest of all checks.
    if args.output is not None and output_file_conflict(args.output):
        print(
            f"mrr audit artifacts: --output {args.output} already exists — refusing to write "
            "over it.",
            file=sys.stderr,
        )
        return _EXIT_REFUSED

    # --- 2. Build the report: ArtifactPresenceService reads/parses the
    #        dump, resolves the recorded root, and checks every anchor with
    #        a snapshot_hash.
    try:
        report = ArtifactPresenceService().build_report(args.dump)
    except ArtifactPresenceInputError as exc:
        print(
            f"mrr audit artifacts: {exc}. Refusing to fabricate a substitute result (MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE
    except (ArchiveDumpParseError, AmbiguousArtifactStoreRootError, DomainError, ValueError) as exc:
        print(
            f"mrr audit artifacts: refused — {type(exc).__name__}: {exc}",
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
        dir=args.output.parent, prefix=f".{args.output.name}.artifacts-tmp-"
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
        "dump": str(args.dump),
        "output": str(args.output),
        "format": args.format,
        "store_reference_status": report.store_reference_status,
        "violations": report.violations.model_dump(mode="json"),
        "observations": report.observations.model_dump(mode="json"),
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser for this module alone (``prog="mrr audit"``),
    usable directly (``python -m mrr.services.cli.artifact_presence_main
    artifacts ...``) without going through ``mrr.services.cli.main`` at all
    — self-contained: it registers ONLY ``"artifacts"``, never
    ``"citations"``/``"anchoring"``/``"support"``.
    """
    parser = argparse.ArgumentParser(
        prog="mrr audit",
        description="Read-only archive artifact-presence audit (task-packets/A2-T01.yaml).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_artifacts_subparser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "artifacts":
        return run_command(args)

    parser.print_help()  # pragma: no cover - unreachable while "command" is required
    return 1


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
