"""``mrr`` console-script entry point (task-packets/E2-T07.yaml): a thin
argparse CLI over ``mrr.services.cli.orchestration.run_local_evidence_loop``.
No new domain behavior lives here — this module only parses arguments, wires
concrete dependencies (a real ``sqlalchemy.Engine``, a
``LocalFilesystemArtifactStore``, Ed25519 keys loaded from disk or generated
ephemerally), calls the orchestration function, and reports the result or a
clear, typed failure.

--- MRR-NFR-012: explicit degradation, never a fabricated substitute --------

Two dependencies this CLI cannot itself provide — a reachable PostgreSQL
database and a writable artifact-store root — are checked BEFORE any service
call runs, with a clear, specific message and a non-zero exit code on
failure. Neither failure mode falls back to an in-memory or fake substitute;
the run simply does not happen, and says so.

The same principle governs ``code_revision`` (MRR-FR-053's "code or workflow
version"): a research runtime must not depend on running inside a git working
tree to know its own code version — a deployed container has no ``.git``
directory and no ``git`` binary, so shelling out to discover one would either
silently fail or, worse, resolve to whatever repository happens to be
checked out on the host rather than the actual running code. The code
revision is therefore INJECTED — via ``--code-revision`` or the
``MRR_CODE_COMMIT`` environment variable a deployment sets — never derived by
calling a subprocess. When neither is given, ``code_revision`` is ``None``:
an explicit "unknown", which ``mrr.contracts.task_bundle.ExecutionSpec
.code_revision``/``mrr.contracts.run_manifest.RunManifest.code_commit`` both
already model as a nullable field, not a fabricated placeholder string.
Note that ``EvidenceCrateSealer.seal`` (E2-T06) itself raises if the
recorded ``RunManifest.code_commit`` is ``None`` (MRR-FR-053 requires a real
value before a crate can be sealed) — so a run started without a known code
revision will run all the way through execution and Run Manifest recording,
then fail loudly and explicitly at the sealing step, never silently.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.adapters.object_store.local import LocalFilesystemArtifactStore
from mrr.crypto.exceptions import CryptoError
from mrr.domain.exceptions import DomainError
from mrr.services.cli import (
    anchoring_integrity_main,
    artifact_presence_main,
    citation_audit_main,
    export_main,
    federation_main,
    field_observation_main,
    release_main,
    report_main,
    support_audit_main,
    synthesis_main,
    validation_main,
    verification_main,
)
from mrr.services.cli.orchestration import DEFAULT_INPUT_BYTES, run_local_evidence_loop
from sqlalchemy.exc import SQLAlchemyError

#: Exit codes. 0 is success (argparse's own built-in failures, e.g. a bad
#: flag or missing required argument, use argparse's own code, 2).
_EXIT_DEPENDENCY_UNAVAILABLE = 2
_EXIT_RUN_ABORTED = 3

#: The environment variable a deployment sets to inject the running code's
#: revision (see the module docstring). No subprocess, no ``git`` call.
_CODE_COMMIT_ENV_VAR = "MRR_CODE_COMMIT"


def _resolve_code_revision(explicit: str | None) -> str | None:
    """The caller's ``--code-revision``, else the ``MRR_CODE_COMMIT``
    environment variable, else ``None`` — an explicit, honest "unknown"
    (MRR-NFR-012), never a fabricated or guessed value. See the module
    docstring for why this never shells out to ``git``.
    """
    return explicit or os.environ.get(_CODE_COMMIT_ENV_VAR) or None


def _load_or_generate_key(path: Path | None, *, flag: str) -> tuple[Ed25519PrivateKey, bool]:
    """Load an Ed25519 private key from a PEM file, or generate a fresh
    ephemeral one if ``path`` is ``None``. Returns ``(key, was_generated)``
    so the caller can print an explicit notice — an ephemeral key is honest
    and fine for a local demonstration run, but must never look like a
    persisted operator identity.
    """
    if path is None:
        return Ed25519PrivateKey.generate(), True
    data = path.read_bytes()
    key = serialization.load_pem_private_key(data, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit(f"mrr run: {flag} {path} does not contain an Ed25519 private key")
    return key, False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mrr",
        description=(
            "Meridian Research Runtime — local operator CLI. Composes the merged "
            "E2 services (ResearchScoreService, CapabilityRegistry, TaskBundleService, "
            "NodeTaskDecisionService, ReferenceTaskExecutor, RunManifestRecorder, "
            "EvidenceCrateSealer) into one complete local evidence loop, with no "
            "model/LLM dependency anywhere in it."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help=(
            "Run one complete local evidence loop: approve a Research Score, "
            "register a node's capability, negotiate and execute a deterministic "
            "Task Bundle, record the Run Manifest, and seal the Evidence Crate."
        ),
    )
    run_parser.add_argument(
        "--database-url",
        required=True,
        help="SQLAlchemy PostgreSQL URL, e.g. postgresql+psycopg://user:pass@host/db",
    )
    run_parser.add_argument(
        "--artifact-root",
        required=True,
        type=Path,
        help="Root directory for the local content-addressed artifact store.",
    )
    run_parser.add_argument(
        "--origin-key-file",
        type=Path,
        default=None,
        help=(
            "PEM-encoded Ed25519 private key for the origin practice. An ephemeral "
            "key is generated (and reported on stderr) if this is omitted."
        ),
    )
    run_parser.add_argument(
        "--node-key-file",
        type=Path,
        default=None,
        help=(
            "PEM-encoded Ed25519 private key for the executing node. An ephemeral "
            "key is generated (and reported on stderr) if this is omitted."
        ),
    )
    run_parser.add_argument(
        "--actor", default=None, help="Actor URN recorded on every domain event. Minted if omitted."
    )
    run_parser.add_argument("--policy-version", default="policy-mrr-e2-local")
    run_parser.add_argument("--correlation-id", default=None)
    run_parser.add_argument("--origin-practice-id", default=None)
    run_parser.add_argument("--node-practice-id", default=None)
    run_parser.add_argument("--node-id", default=None)
    run_parser.add_argument("--capability-name", default="reference.deterministic-transform")
    run_parser.add_argument("--capability-version", default="1.0.0")
    run_parser.add_argument(
        "--input-file",
        type=Path,
        default=None,
        help=(
            "File whose bytes become the reference task's declared input. "
            "A fixed default is used if omitted."
        ),
    )
    run_parser.add_argument("--timeout-seconds", type=int, default=30)
    run_parser.add_argument(
        "--code-revision",
        default=None,
        help=(
            "Recorded code/workflow revision. Falls back to the MRR_CODE_COMMIT "
            "environment variable, then to an explicit unknown (null) — never a "
            "fabricated value. A crate cannot be sealed without one (MRR-FR-053)."
        ),
    )
    run_parser.add_argument(
        "--deny-score-approval",
        action="store_true",
        help=(
            "Skip approving the Research Score, to exercise the MRR-FR-004 gate "
            "deliberately — the run then aborts before any Task Bundle is created."
        ),
    )
    run_parser.add_argument(
        "--json", dest="as_json", action="store_true", help="Print the result as JSON."
    )

    # Task-packets/K1-T04.yaml's one additive registration: a new "synthesis"
    # subparser group ("mrr synthesis run") delegating entirely to
    # mrr.services.cli.synthesis_main — no other line in this file changes,
    # and the "run" subcommand/_run_command above are untouched.
    synthesis_main.register_synthesis_subcommand(subparsers)

    # Task-packets/K1-T05.yaml's one additive registration: a new
    # "verification" subparser group ("mrr verification record") delegating
    # entirely to mrr.services.cli.verification_main — mirrors the synthesis
    # registration immediately above; no other line in this file changes.
    verification_main.register_verification_subcommand(subparsers)

    # Task-packets/E8-T01.yaml's one additive registration: a new "export"
    # subparser group ("mrr export ro-crate") delegating entirely to
    # mrr.services.cli.export_main — mirrors the verification registration
    # immediately above; no other line in this file changes.
    export_main.register_export_subcommand(subparsers)

    # Task-packets/E8-T03.yaml's one additive registration: a new "report"
    # subparser group ("mrr report render") delegating entirely to
    # mrr.services.cli.report_main — mirrors the export registration
    # immediately above; no other line in this file changes.
    report_main.register_report_subcommand(subparsers)

    # Task-packets/E8-T04.yaml's one additive registration: a new "release"
    # subparser group ("mrr release create" / "mrr release verify")
    # delegating entirely to mrr.services.cli.release_main — mirrors the
    # report registration immediately above; no other line in this file
    # changes.
    release_main.register_release_subcommand(subparsers)

    # Task-packets/N1-T01.yaml's one additive registration: a new "validate"
    # subparser group ("mrr validate agreement") delegating entirely to
    # mrr.services.cli.validation_main — mirrors the release registration
    # immediately above; no other line in this file changes.
    validation_main.register_validation_subcommand(subparsers)

    # Task-packets/N2-T01.yaml's one additive registration: a new "audit"
    # subparser group ("mrr audit citations") delegating entirely to
    # mrr.services.cli.citation_audit_main — mirrors the validate registration
    # immediately above; no other line in this file changes.
    citation_audit_main.register_audit_subcommand(subparsers)

    # Task-packets/R2-T01.yaml's one additive registration: a new "observe"
    # subparser group ("mrr observe field") delegating entirely to
    # mrr.services.cli.field_observation_main — mirrors the audit registration
    # immediately above; no other line in this file changes.
    field_observation_main.register_observe_subcommand(subparsers)

    # Task-packets/N2-T02b.yaml's one additive registration: attaches
    # "anchoring" onto the EXISTING "audit" group citation_audit_main
    # .register_audit_subcommand already created immediately above ("mrr
    # audit anchoring", alongside the unchanged "mrr audit citations") —
    # delegates entirely to mrr.services.cli.anchoring_integrity_main; no
    # other line in this file changes, and citation_audit_main's own parser
    # and dispatch are untouched.
    anchoring_integrity_main.register_anchoring_subcommand(subparsers)

    # Task-packets/N2-T03b.yaml's one additive registration: attaches
    # "support" onto the EXISTING "audit" group ("mrr audit support",
    # alongside the unchanged "mrr audit citations" / "mrr audit anchoring")
    # — delegates entirely to mrr.services.cli.support_audit_main; no other
    # line in this file changes, and citation_audit_main's/
    # anchoring_integrity_main's own parsers and dispatch are untouched.
    support_audit_main.register_support_subcommand(subparsers)

    # Task-packets/A2-T01.yaml's one additive registration: attaches
    # "artifacts" onto the EXISTING "audit" group ("mrr audit artifacts",
    # alongside the unchanged "mrr audit citations" / "mrr audit anchoring" /
    # "mrr audit support") — delegates entirely to
    # mrr.services.cli.artifact_presence_main; no other line in this file
    # changes, and the three siblings' own parsers and dispatch are
    # untouched.
    artifact_presence_main.register_artifacts_subcommand(subparsers)

    # Task-packets/E5-T08.yaml's one additive registration: a new
    # "federation" subparser group ("mrr federation outbox write" / "mrr
    # federation inbox accept") delegating entirely to
    # mrr.services.cli.federation_main — mirrors the anchoring registration
    # immediately above; no other line in this file changes.
    federation_main.register_federation_subcommand(subparsers)

    return parser


def _run_command(args: argparse.Namespace) -> int:
    try:
        engine = sa.create_engine(args.database_url)
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
    except SQLAlchemyError as exc:
        print(
            "mrr run: cannot reach the PostgreSQL database at the given --database-url "
            f"({exc}). Refusing to fabricate a substitute result (MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE

    try:
        args.artifact_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(
            f"mrr run: cannot create --artifact-root {args.artifact_root} ({exc}). "
            "Refusing to fabricate a substitute result (MRR-NFR-012).",
            file=sys.stderr,
        )
        engine.dispose()
        return _EXIT_DEPENDENCY_UNAVAILABLE
    artifact_store = LocalFilesystemArtifactStore(args.artifact_root)

    origin_key, origin_generated = _load_or_generate_key(
        args.origin_key_file, flag="--origin-key-file"
    )
    node_key, node_generated = _load_or_generate_key(args.node_key_file, flag="--node-key-file")
    if origin_generated:
        print(
            "mrr run: no --origin-key-file given; generated an ephemeral Ed25519 key "
            "for this run only.",
            file=sys.stderr,
        )
    if node_generated:
        print(
            "mrr run: no --node-key-file given; generated an ephemeral Ed25519 key "
            "for this run only.",
            file=sys.stderr,
        )

    input_bytes = (
        args.input_file.read_bytes() if args.input_file is not None else DEFAULT_INPUT_BYTES
    )
    code_revision = _resolve_code_revision(args.code_revision)

    kwargs: dict[str, Any] = {
        "engine": engine,
        "artifact_store": artifact_store,
        # task-packets/A2-T01.yaml: the same --artifact-root already used
        # to construct artifact_store two lines above, so the RunManifest
        # this run records carries status="recorded" naming exactly the
        # root this run actually wrote bytes to.
        "artifact_root": args.artifact_root,
        "origin_signing_key": origin_key,
        "node_signing_key": node_key,
        "policy_version": args.policy_version,
        "capability_name": args.capability_name,
        "capability_version": args.capability_version,
        "input_bytes": input_bytes,
        "timeout_seconds": args.timeout_seconds,
        "code_revision": code_revision,
        "approve_score": not args.deny_score_approval,
    }
    for attr, key in (
        ("actor", "actor"),
        ("correlation_id", "correlation_id"),
        ("origin_practice_id", "origin_practice_id"),
        ("node_practice_id", "node_practice_id"),
        ("node_id", "node_id"),
    ):
        value = getattr(args, attr)
        if value:
            kwargs[key] = value

    try:
        result = run_local_evidence_loop(**kwargs)
    except (DomainError, CryptoError, ValueError) as exc:
        # ValueError alongside the typed DomainError/CryptoError hierarchies
        # covers, among other invariant checks, EvidenceCrateSealer.seal
        # raising when code_revision was never supplied (see the module
        # docstring) — a real gap in what this run can prove, reported
        # clearly rather than as a bare traceback.
        print(f"mrr run: aborted — {type(exc).__name__}: {exc}", file=sys.stderr)
        return _EXIT_RUN_ABORTED
    finally:
        engine.dispose()

    payload = {
        "evidence_crate_id": result.evidence_crate_id,
        "run_manifest_id": result.run_manifest_id,
        "task_id": result.task_id,
        "research_score_id": result.research_score_id,
        "node_id": result.node_id,
        "output_hash": result.output_hash,
        "run_state": result.run_state,
        "is_deterministic": result.is_deterministic,
    }
    if args.as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for key, value in payload.items():
            print(f"{key}: {value}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        return _run_command(args)
    if args.command == "synthesis":
        return synthesis_main.run_command(args)
    if args.command == "verification":
        return verification_main.run_command(args)
    if args.command == "export":
        return export_main.run_command(args)
    if args.command == "report":
        return report_main.run_command(args)
    if args.command == "release":
        return release_main.run_command(args)
    if args.command == "validate":
        return validation_main.run_command(args)
    if args.command == "audit":
        # Task-packets/N2-T02b.yaml's one additive dispatch branch: within
        # the EXISTING "audit" group, route "anchoring" to its own module
        # and leave every other audit_command (i.e. "citations") going to
        # citation_audit_main.run_command exactly as before — that call
        # itself is unchanged.
        if args.audit_command == "anchoring":
            return anchoring_integrity_main.run_command(args)
        # Task-packets/N2-T03b.yaml's one additive dispatch branch: route
        # "support" to its own module — "citations"/"anchoring" dispatch
        # above is unchanged.
        if args.audit_command == "support":
            return support_audit_main.run_command(args)
        # Task-packets/A2-T01.yaml's one additive dispatch branch: route
        # "artifacts" to its own module — "citations"/"anchoring"/"support"
        # dispatch above is unchanged.
        if args.audit_command == "artifacts":
            return artifact_presence_main.run_command(args)
        return citation_audit_main.run_command(args)
    if args.command == "observe":
        return field_observation_main.run_command(args)
    if args.command == "federation":
        return federation_main.run_command(args)

    parser.print_help()  # pragma: no cover - unreachable while "command" is required
    return 1


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
