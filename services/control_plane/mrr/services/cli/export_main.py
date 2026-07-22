"""``mrr export ro-crate`` (task-packets/E8-T01.yaml): a thin argparse CLI
over ``mrr.services.export.service.ExportService``, mirroring
``mrr.services.cli.verification_main``'s own shape (a sibling file, not
inlined into ``main.py``, so that module's own diff stays a one-line,
additive ``"export"`` subparser registration — the E2-T07 CLI-law precedent
both modules follow) and ``mrr.services.cli.main``'s MRR-NFR-012 "explicit
degradation, never a fabricated substitute" discipline.

This is the tooling half of MRR-FR-055's first requirement: a third party
can now materialize an inspectable, offline-verifiable directory from one
sealed ``EvidenceCrate`` with one command, instead of only through
``ExportService.export`` called from Python. The CLI is thin — it parses
arguments, checks dependencies in cheapest-first order, calls
``ExportService.export`` exactly once, and reports the result or a clear,
typed failure. No domain behavior lives here (task-packets/E2-T07.yaml's CLI
law): the R2 closure rule, the R3 atomic-write discipline, and every
refusal condition all remain exclusively ``ExportService``'s (and
``mrr.domain.ro_crate``'s) business — this module never inspects a crate
body, walks an edge, or opens an artifact blob itself.

--- Ordering invariant (MRR-NFR-012): cheapest, local checks before expensive ones --

task-packets/E8-T01.yaml R5's own explicit ordering: (1) ``--output-dir``
must not already exist as a file or non-empty directory — a plain
filesystem stat, checked via ``mrr.services.export.service
.output_path_conflict`` (the SAME function ``ExportService.export`` itself
uses as its own authoritative, caller-independent guarantee — see that
module's own docstring for why one function, not two copies); (2)
``--artifact-root`` must already exist and be readable as a directory — a
plain filesystem stat, no store constructed yet; (3) the PostgreSQL database
named by ``--database-url`` must be reachable (``SELECT 1``) — the one
network round trip this command ever makes before doing real work. Only
after all three succeed is ``ExportService.export`` called, exactly once.

Unlike ``mrr run``'s own ``--artifact-root`` handling (which CREATES that
directory, since a run WRITES fresh artifacts into it),
``--artifact-root`` here is never created or written to — export only
READS artifact bytes that must already exist, so a missing or unreadable
root is reported as a dependency failure, never silently made to exist.

--- The exit-code map (mirrors ``verification_main``, K1-T05 derived_decisions (d)) ---

- ``0``: the export completed (``ExportService.export`` returned). Prints a
  single JSON line: ``crate_id``, ``output_dir``, ``object_count``,
  ``artifact_count``, ``total_bytes``.
- ``2``: a DEPENDENCY is unavailable — ``--artifact-root`` does not exist or
  is not a readable directory, or the PostgreSQL database is unreachable. No
  in-memory or partial fallback exists for either (MRR-NFR-012); the export
  simply does not happen, and says so.
- ``3``: the export was REFUSED by everything downstream of a successful
  dependency check — ``--output-dir`` already exists (file or non-empty
  directory; checked FIRST, even before the dependency checks above, since
  it is the cheapest of all three and needs no store or connection at all),
  an unknown ``--crate-id``, a resolved object that is not an
  ``EvidenceCrate``, an unresolvable referenced urn, or missing artifact
  bytes — every ``mrr.domain.exceptions.DomainError``/plain ``ValueError``
  ``ExportService.export`` lets propagate.
- argparse's own built-in failures (a bad flag, a missing required argument)
  use argparse's own exit code, ``2`` — the same overlap
  ``mrr.services.cli.main``/``verification_main`` already have between their
  own dependency-unavailable code and argparse's usage-error code.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import sqlalchemy as sa
from mrr.adapters.object_store.local import LocalFilesystemArtifactStore
from mrr.domain.exceptions import DomainError
from mrr.persistence.repositories import (
    PostgresEdgeRepository,
    PostgresEventLog,
    PostgresObjectRepository,
)
from mrr.services.export.service import ExportService, output_path_conflict
from sqlalchemy.exc import SQLAlchemyError

#: See the module docstring's "exit-code map" section.
_EXIT_DEPENDENCY_UNAVAILABLE = 2
_EXIT_EXPORT_REFUSED = 3


def _add_ro_crate_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    ro_crate_parser = subparsers.add_parser(
        "ro-crate",
        help=(
            "Export one sealed EvidenceCrate and its provenance neighborhood into a "
            "self-contained, offline-verifiable RO-Crate 1.1 directory (task-packets/"
            "E8-T01.yaml)."
        ),
    )
    ro_crate_parser.add_argument(
        "--database-url",
        required=True,
        help="SQLAlchemy PostgreSQL URL, e.g. postgresql+psycopg://user:pass@host/db",
    )
    ro_crate_parser.add_argument(
        "--artifact-root",
        required=True,
        type=Path,
        help=(
            "Root directory of an existing, readable local content-addressed artifact "
            "store (never created or written to by this command — export only reads "
            "artifact bytes that must already exist)."
        ),
    )
    ro_crate_parser.add_argument(
        "--crate-id",
        required=True,
        help="URN of the sealed EvidenceCrate to export, loaded from the generic object store.",
    )
    ro_crate_parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help=(
            "Directory the export tree is written into. Must not already exist as a file "
            "or as a non-empty directory (refused before any other check runs)."
        ),
    )


def register_export_subcommand(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """The ONE call ``mrr.services.cli.main.build_parser`` makes to register
    ``mrr export ro-crate`` — everything else about this subcommand's flags
    and behavior lives in this module.
    """
    export_parser = subparsers.add_parser(
        "export",
        help="RO-Crate-compatible export (task-packets/E8-T01.yaml).",
    )
    export_subparsers = export_parser.add_subparsers(dest="export_command", required=True)
    _add_ro_crate_subparser(export_subparsers)


def run_command(args: argparse.Namespace) -> int:
    """The actual execution logic for ``mrr export ro-crate`` — called both
    by ``mrr.services.cli.main.main`` (the real, nested ``mrr export
    ro-crate`` entry point) and by this module's own standalone ``main``
    (below), which both parse the SAME flags via ``_add_ro_crate_subparser``.
    Unconditional, like ``mrr.services.cli.verification_main.run_command`` —
    "export" has exactly one nested subcommand ("ro-crate"), so by the time
    either caller reaches this function, that is the only possibility
    argparse's own ``required=True`` subparsers already enforced.
    """
    # --- 1. --output-dir refusal check FIRST — the cheapest of all three
    #        checks (a plain filesystem stat), and the one this command
    #        never wants to run past even if a database happened to be
    #        reachable (MRR-NFR-012 ordering, task-packets/E8-T01.yaml R5).
    if output_path_conflict(args.output_dir):
        print(
            f"mrr export ro-crate: --output-dir {args.output_dir} already exists (as a file, "
            "or as a non-empty directory) — refusing to write over or into it.",
            file=sys.stderr,
        )
        return _EXIT_EXPORT_REFUSED

    # --- 2. --artifact-root must already exist and be readable. Never
    #        created here — unlike `mrr run`, export only READS artifact
    #        bytes that must already exist (see the module docstring).
    if not args.artifact_root.is_dir():
        print(
            f"mrr export ro-crate: --artifact-root {args.artifact_root} does not exist or is "
            "not a directory. Refusing to fabricate a substitute result (MRR-NFR-012).",
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
            "mrr export ro-crate: cannot reach the PostgreSQL database at the given "
            f"--database-url ({exc}). Refusing to fabricate a substitute result (MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE

    # --- 4. Resolve the crate and export exactly once.
    try:
        object_repository = PostgresObjectRepository(engine)
        edge_repository = PostgresEdgeRepository(engine)
        event_log = PostgresEventLog(engine)
        artifact_store = LocalFilesystemArtifactStore(args.artifact_root)
        export_service = ExportService(
            object_repository, edge_repository, event_log, artifact_store
        )
        result = export_service.export(args.crate_id, args.output_dir)
    except (DomainError, ValueError) as exc:
        print(
            f"mrr export ro-crate: export refused — {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return _EXIT_EXPORT_REFUSED
    finally:
        engine.dispose()

    payload = {
        "crate_id": result.crate_id,
        "output_dir": str(result.output_dir),
        "object_count": result.object_count,
        "artifact_count": result.artifact_count,
        "total_bytes": result.total_bytes,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser for this module alone (``prog="mrr export"``),
    usable directly (``python -m mrr.services.cli.export_main ro-crate
    ...``) without going through ``mrr.services.cli.main`` at all — mirrors
    ``mrr.services.cli.verification_main.build_parser``'s identical shape.
    """
    parser = argparse.ArgumentParser(
        prog="mrr export",
        description="RO-Crate-compatible export (task-packets/E8-T01.yaml).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_ro_crate_subparser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "ro-crate":
        return run_command(args)

    parser.print_help()  # pragma: no cover - unreachable while "command" is required
    return 1


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
