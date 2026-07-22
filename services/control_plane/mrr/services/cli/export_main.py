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

--- task-packets/E8-T06.yaml R2: the one-of root group -------------------------

``ro-crate`` gains a mutually-exclusive, ``required=True`` argparse group of
THREE flags — ``--crate-id`` (as today), ``--claim-id`` (repeatable, via
``action="append"``), ``--all-claims`` (``store_true``) — exactly one of
which a caller must supply; argparse itself enforces "exactly one distinct
flag from this group" (a repeated ``--claim-id`` still counts as one flag
from the group's own perspective). ``--artifact-root`` is no longer
``required=True`` at the argparse level (a claim-rooted export fetches no
artifact bytes at all — derived_decisions (a)) — instead it becomes a
MANUAL, POST-PARSE shape check (:func:`run_command`'s own step 2, BELOW the
cheap ``--output-dir`` conflict check but ABOVE the ``--artifact-root``
existence/readability check and the database-reachability check): REQUIRED
with ``--crate-id``, FORBIDDEN with ``--claim-id``/``--all-claims``.

**Disclosed exit-code choice for this new shape check** (task-packets/
E8-T06.yaml's own AT5 names this a "usage refusal" without pinning an exit
code — genuinely open at the packet level): exit ``2``
(``_EXIT_DEPENDENCY_UNAVAILABLE``), mirroring ``mrr.services.cli
.report_main``'s OWN directly-analogous precedent for the EXACT same
"flag X required with Y, forbidden with Z" shape
(``--classification-file`` required-with-``public``/forbidden-with-
``internal``, also exit 2) — the closest, most literal precedent in this
very codebase, chosen over exit 3 (which this module otherwise reserves for
refusals that only fire AFTER a successful dependency check, per the
module's own established exit-code map above).

The exit-0 JSON line gains a ``"root"`` key (``"crate"``/``"claims"``) and a
``"claim_ids"`` key (task-packets/E8-T06.yaml R2: "the exit-0 JSON line
reports the root kind") — purely additive; every crate-rooted invocation's
pre-existing keys keep their pre-existing values (checked: no E8-T01..T05
test asserts the JSON line's exact key SET, only individual key lookups).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import sqlalchemy as sa
from mrr.adapters.object_store.local import LocalFilesystemArtifactStore
from mrr.domain.artifacts import ArtifactDescriptor, Classification
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


class _NeverInvokedArtifactStore:
    """A stand-in for ``mrr.domain.artifacts.ArtifactStore`` that raises on
    every call — mirrors ``mrr.services.report.service
    ._NeverInvokedArtifactStore``'s own, independently-declared precedent
    (private to its own module, hence not imported — see this module's own
    established per-module-constant convention). Used only to satisfy
    ``ExportService.__init__``'s required ``artifact_store`` parameter when
    exporting a CLAIM-ROOTED closure (task-packets/E8-T06.yaml), which never
    fetches artifact bytes: ``ExportService.export_from_claims`` always
    resolves a closure whose ``artifact_refs`` is empty (derived_decisions
    (a)), so ``ArtifactStore.get`` is provably never called on this
    instance.
    """

    def put(
        self,
        data: bytes,
        *,
        media_type: str,
        producer_run_id: str,
        classification: Classification,
        created_at: datetime,
    ) -> ArtifactDescriptor:
        raise AssertionError(
            "a claim-rooted `mrr export ro-crate` never fetches artifact bytes "
            "(export_from_claims's own closure always has empty artifact_refs) — "
            "this stand-in should never actually be invoked"
        )

    def get(self, content_hash: str) -> bytes:
        raise AssertionError(
            "a claim-rooted `mrr export ro-crate` never fetches artifact bytes "
            "(export_from_claims's own closure always has empty artifact_refs) — "
            "this stand-in should never actually be invoked"
        )

    def stat(self, content_hash: str) -> ArtifactDescriptor:
        raise AssertionError(
            "a claim-rooted `mrr export ro-crate` never fetches artifact bytes "
            "(export_from_claims's own closure always has empty artifact_refs) — "
            "this stand-in should never actually be invoked"
        )

    def exists(self, content_hash: str) -> bool:
        raise AssertionError(
            "a claim-rooted `mrr export ro-crate` never fetches artifact bytes "
            "(export_from_claims's own closure always has empty artifact_refs) — "
            "this stand-in should never actually be invoked"
        )


def _add_ro_crate_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    ro_crate_parser = subparsers.add_parser(
        "ro-crate",
        help=(
            "Export one sealed EvidenceCrate, OR the claim graph of an archival schema "
            "(task-packets/E8-T06.yaml), into a self-contained, offline-verifiable "
            "RO-Crate 1.1 directory (task-packets/E8-T01.yaml)."
        ),
    )
    ro_crate_parser.add_argument(
        "--database-url",
        required=True,
        help="SQLAlchemy PostgreSQL URL, e.g. postgresql+psycopg://user:pass@host/db",
    )
    ro_crate_parser.add_argument(
        "--artifact-root",
        default=None,
        type=Path,
        help=(
            "Root directory of an existing, readable local content-addressed artifact "
            "store (never created or written to by this command — export only reads "
            "artifact bytes that must already exist). REQUIRED with --crate-id; FORBIDDEN "
            "with --claim-id/--all-claims (a claim-rooted export fetches no bytes at all — "
            "task-packets/E8-T06.yaml)."
        ),
    )
    root_group = ro_crate_parser.add_mutually_exclusive_group(required=True)
    root_group.add_argument(
        "--crate-id",
        default=None,
        help="URN of the sealed EvidenceCrate to export, loaded from the generic object store.",
    )
    root_group.add_argument(
        "--claim-id",
        action="append",
        default=None,
        dest="claim_id",
        help=(
            "URN of a Claim to root the export on (task-packets/E8-T06.yaml). Repeatable — "
            "the union of every given claim's own closure is exported. Each MUST resolve to "
            "a stored Claim, else a typed refusal names it."
        ),
    )
    root_group.add_argument(
        "--all-claims",
        action="store_true",
        default=False,
        help=(
            "Root the export on EVERY claim the schema contains (task-packets/E8-T06.yaml) "
            "— each archival schema is exactly one run's world. Refuses (exit 3) if the "
            "schema has zero claims, rather than shipping a silent empty bundle."
        ),
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

    # --- 2. --artifact-root shape: REQUIRED with --crate-id, FORBIDDEN with
    #        --claim-id/--all-claims (task-packets/E8-T06.yaml R2 — see the
    #        module docstring's own "E8-T06 R2" section for the disclosed
    #        exit-code choice). A pure argument-consistency check, no I/O —
    #        cheaper even than the filesystem stats below, but ordered here
    #        (after the output-dir check, before the artifact-root
    #        existence check) to mirror mrr.services.cli.report_main's own
    #        directly-analogous --classification-file precedent's position
    #        in ITS own ordering.
    crate_rooted = args.crate_id is not None
    if crate_rooted and args.artifact_root is None:
        print(
            "mrr export ro-crate: --crate-id requires --artifact-root (a crate-rooted export "
            "fetches artifact bytes). Refusing to fabricate a substitute result (MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE
    if not crate_rooted and args.artifact_root is not None:
        print(
            "mrr export ro-crate: --artifact-root is forbidden with --claim-id/--all-claims "
            "(a claim-rooted export fetches no artifact bytes at all — task-packets/"
            "E8-T06.yaml). Refusing to fabricate a substitute result (MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE

    # --- 3. --artifact-root must already exist and be readable (crate-
    #        rooted only). Never created here — unlike `mrr run`, export
    #        only READS artifact bytes that must already exist (see the
    #        module docstring).
    if crate_rooted and not args.artifact_root.is_dir():
        print(
            f"mrr export ro-crate: --artifact-root {args.artifact_root} does not exist or is "
            "not a directory. Refusing to fabricate a substitute result (MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE

    # --- 4. Dependency check: the database must be reachable (MRR-NFR-012).
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

    # --- 5. Resolve the root and export exactly once.
    try:
        object_repository = PostgresObjectRepository(engine)
        edge_repository = PostgresEdgeRepository(engine)
        event_log = PostgresEventLog(engine)
        artifact_store = (
            LocalFilesystemArtifactStore(args.artifact_root)
            if crate_rooted
            else _NeverInvokedArtifactStore()
        )
        export_service = ExportService(
            object_repository, edge_repository, event_log, artifact_store
        )
        if crate_rooted:
            result = export_service.export(args.crate_id, args.output_dir)
        else:
            claim_ids = None if args.all_claims else args.claim_id
            result = export_service.export_from_claims(claim_ids, args.output_dir)
    except (DomainError, ValueError) as exc:
        print(
            f"mrr export ro-crate: export refused — {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return _EXIT_EXPORT_REFUSED
    finally:
        engine.dispose()

    payload = {
        "root": result.root,
        "crate_id": result.crate_id,
        "claim_ids": list(result.claim_ids),
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
