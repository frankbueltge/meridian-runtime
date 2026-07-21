"""``mrr synthesis run`` (task-packets/K1-T04.yaml): a thin argparse CLI over
``mrr.services.cli.synthesis_setup.establish_and_run_synthesis``, mirroring
``mrr.services.cli.main``'s own MRR-NFR-012 "explicit degradation, never a
fabricated substitute" discipline (DB-reachability and artifact-root-
writability checks before any service call; ``code_revision`` injected via
``--code-revision``/``MRR_CODE_COMMIT``, never derived by shelling out to
``git``) and its ``build_parser``/``_run_command``/``main`` argparse
subparser shape, reused verbatim in spirit (derived_decisions (l)).

Kept in its own sibling file, NOT inlined into ``main.py``, so that module's
own diff stays a one-line, additive ``"synthesis"`` subparser registration —
``register_synthesis_subcommand`` below is the one function ``main.py``
calls; every flag/default and the actual run logic live here.

The five new, this-run-specific flags (``--question-model-file``,
``--concept-charter-file``, ``--method-protocol-file``, ``--corpus-file``,
``--protocol-parameters-file``) all default to the committed
``corpora/model-collapse/*.json`` paths, so ``mrr synthesis run
--database-url ... --artifact-root ...`` alone reproduces the committed real
run with no further arguments.
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
from mrr.services.cli.synthesis_setup import establish_and_run_synthesis
from sqlalchemy.exc import SQLAlchemyError

#: This module's own repo-root resolution — mirrors tests/e2e/conftest.py's
#: identical ``REPO_ROOT = Path(__file__).resolve().parents[N]`` convention.
#: This file lives at services/control_plane/mrr/services/cli/synthesis_main.py;
#: parents[5] is the repository root, where corpora/ lives. ``corpora/`` is
#: deliberately NOT part of the wheel's own `only-include` list
#: (pyproject.toml) — these defaults only resolve when this module's own
#: source file is reachable, i.e. an editable/source checkout (this
#: project's own standing convention, `uv sync`), not an installed wheel
#: with no source tree alongside it.
_REPO_ROOT = Path(__file__).resolve().parents[5]
_CORPUS_DIR = _REPO_ROOT / "corpora" / "model-collapse"

_DEFAULT_QUESTION_MODEL_FILE = _CORPUS_DIR / "question-model.proposal.json"
_DEFAULT_CONCEPT_CHARTER_FILE = _CORPUS_DIR / "concept-charter.proposal.json"
_DEFAULT_METHOD_PROTOCOL_FILE = _CORPUS_DIR / "method-protocol.proposal.json"
_DEFAULT_CORPUS_FILE = _CORPUS_DIR / "corpus-entries.json"
_DEFAULT_PROTOCOL_PARAMETERS_FILE = _CORPUS_DIR / "protocol-parameters.sidecar.json"

_EXIT_DEPENDENCY_UNAVAILABLE = 2
_EXIT_RUN_ABORTED = 3

_CODE_COMMIT_ENV_VAR = "MRR_CODE_COMMIT"


def _resolve_code_revision(explicit: str | None) -> str | None:
    """Identical to ``mrr.services.cli.main._resolve_code_revision`` — a
    local copy, not a shared import, per this codebase's own established
    "small pattern replicated locally" precedent for CLI helpers.
    """
    return explicit or os.environ.get(_CODE_COMMIT_ENV_VAR) or None


def _load_or_generate_key(path: Path | None, *, flag: str) -> tuple[Ed25519PrivateKey, bool]:
    """Identical to ``mrr.services.cli.main._load_or_generate_key``."""
    if path is None:
        return Ed25519PrivateKey.generate(), True
    data = path.read_bytes()
    key = serialization.load_pem_private_key(data, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit(
            f"mrr synthesis run: {flag} {path} does not contain an Ed25519 private key"
        )
    return key, False


def _load_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _add_run_subparser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    run_parser = subparsers.add_parser(
        "run",
        help=(
            "Run the first real systematic_evidence_synthesis v1 crate: establish a real "
            "QuestionModel/ConceptCharter/locked MethodProtocol for the model-collapse "
            "question, then run the synthesis evidence loop over the pinned atlas corpus."
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
            "PEM-encoded Ed25519 private key for the origin practice. An ephemeral key is "
            "generated (and reported on stderr) if this is omitted."
        ),
    )
    run_parser.add_argument(
        "--node-key-file",
        type=Path,
        default=None,
        help=(
            "PEM-encoded Ed25519 private key for the executing node. An ephemeral key is "
            "generated (and reported on stderr) if this is omitted."
        ),
    )
    run_parser.add_argument(
        "--actor", default=None, help="Actor URN recorded on every domain event."
    )
    run_parser.add_argument("--policy-version", default="policy-mrr-k1-t04-first-real-run")
    run_parser.add_argument("--correlation-id", default=None)
    run_parser.add_argument("--practice-id", default=None)
    run_parser.add_argument("--origin-practice-id", default=None)
    run_parser.add_argument("--node-practice-id", default=None)
    run_parser.add_argument("--node-id", default=None)
    run_parser.add_argument("--capability-version", default="1.0.0")
    run_parser.add_argument(
        "--method-profile-id",
        default=None,
        help=(
            "An ALREADY-ACCEPTED MethodProfile id to reuse instead of proposing a fresh one. "
            "A fresh systematic_evidence_synthesis MethodProfile is proposed+accepted by "
            "default."
        ),
    )
    run_parser.add_argument(
        "--question-model-file",
        type=Path,
        default=_DEFAULT_QUESTION_MODEL_FILE,
        help="Body-only QuestionModel proposal JSON. Defaults to the committed fixture.",
    )
    run_parser.add_argument(
        "--concept-charter-file",
        type=Path,
        default=_DEFAULT_CONCEPT_CHARTER_FILE,
        help="Body-only ConceptCharter proposal JSON. Defaults to the committed fixture.",
    )
    run_parser.add_argument(
        "--method-protocol-file",
        type=Path,
        default=_DEFAULT_METHOD_PROTOCOL_FILE,
        help="Body-only MethodProtocol proposal JSON. Defaults to the committed fixture.",
    )
    run_parser.add_argument(
        "--corpus-file",
        type=Path,
        default=_DEFAULT_CORPUS_FILE,
        help="The real corpus (CorpusEntry array). Defaults to the committed fixture.",
    )
    run_parser.add_argument(
        "--protocol-parameters-file",
        type=Path,
        default=_DEFAULT_PROTOCOL_PARAMETERS_FILE,
        help="The protocol-parameters sidecar. Defaults to the committed fixture.",
    )
    run_parser.add_argument("--timeout-seconds", type=int, default=30)
    run_parser.add_argument(
        "--code-revision",
        default=None,
        help=(
            "Recorded code/workflow revision. Falls back to the MRR_CODE_COMMIT environment "
            "variable, then to an explicit unknown (null) — never a fabricated value."
        ),
    )
    run_parser.add_argument(
        "--deny-score-approval",
        action="store_true",
        help="Skip approving the Research Score, to exercise the MRR-FR-004 gate deliberately.",
    )
    run_parser.add_argument(
        "--json", dest="as_json", action="store_true", help="Print the result as JSON."
    )


def register_synthesis_subcommand(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """The ONE call ``mrr.services.cli.main.build_parser`` makes to register
    ``mrr synthesis run`` — everything else about this subcommand's flags
    and behavior lives in this module.
    """
    synthesis_parser = subparsers.add_parser(
        "synthesis",
        help="Synthesis-profile governance + run composition (task-packets/K1-T04.yaml).",
    )
    synthesis_subparsers = synthesis_parser.add_subparsers(dest="synthesis_command", required=True)
    _add_run_subparser(synthesis_subparsers)


def run_command(args: argparse.Namespace) -> int:
    """The actual execution logic for ``mrr synthesis run`` — called both by
    ``mrr.services.cli.main.main`` (the real, nested ``mrr synthesis run``
    entry point) and by this module's own standalone ``main`` (below), which
    both parse the SAME flags via ``_add_run_subparser``.
    """
    try:
        engine = sa.create_engine(args.database_url)
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
    except SQLAlchemyError as exc:
        print(
            "mrr synthesis run: cannot reach the PostgreSQL database at the given "
            f"--database-url ({exc}). Refusing to fabricate a substitute result (MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE

    try:
        args.artifact_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(
            f"mrr synthesis run: cannot create --artifact-root {args.artifact_root} ({exc}). "
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
            "mrr synthesis run: no --origin-key-file given; generated an ephemeral Ed25519 key "
            "for this run only.",
            file=sys.stderr,
        )
    if node_generated:
        print(
            "mrr synthesis run: no --node-key-file given; generated an ephemeral Ed25519 key "
            "for this run only.",
            file=sys.stderr,
        )

    try:
        question_model = _load_json_file(args.question_model_file)
        concept_charter = _load_json_file(args.concept_charter_file)
        method_protocol = _load_json_file(args.method_protocol_file)
        corpus_entries = _load_json_file(args.corpus_file)
        protocol_parameters = _load_json_file(args.protocol_parameters_file)
    except OSError as exc:
        print(
            f"mrr synthesis run: cannot read a fixture file ({exc}). Refusing to fabricate a "
            "substitute result (MRR-NFR-012).",
            file=sys.stderr,
        )
        engine.dispose()
        return _EXIT_DEPENDENCY_UNAVAILABLE

    code_revision = _resolve_code_revision(args.code_revision)

    kwargs: dict[str, Any] = {
        "engine": engine,
        "artifact_store": artifact_store,
        "origin_signing_key": origin_key,
        "node_signing_key": node_key,
        "question_model": question_model,
        "concept_charter": concept_charter,
        "method_protocol": method_protocol,
        "corpus_entries": corpus_entries,
        "protocol_parameters": protocol_parameters,
        "policy_version": args.policy_version,
        "capability_version": args.capability_version,
        "timeout_seconds": args.timeout_seconds,
        "code_revision": code_revision,
        "approve_score": not args.deny_score_approval,
    }
    for attr, key in (
        ("actor", "actor"),
        ("correlation_id", "correlation_id"),
        ("practice_id", "practice_id"),
        ("origin_practice_id", "origin_practice_id"),
        ("node_practice_id", "node_practice_id"),
        ("node_id", "node_id"),
        ("method_profile_id", "method_profile_id"),
    ):
        value = getattr(args, attr)
        if value:
            kwargs[key] = value

    try:
        result = establish_and_run_synthesis(**kwargs)
    except (DomainError, CryptoError, ValueError) as exc:
        print(f"mrr synthesis run: aborted — {type(exc).__name__}: {exc}", file=sys.stderr)
        return _EXIT_RUN_ABORTED
    finally:
        engine.dispose()

    payload = {
        "method_profile_id": result.method_profile_id,
        "question_model_id": result.question_model_id,
        "concept_charter_id": result.concept_charter_id,
        "method_protocol_id": result.method_protocol_id,
        "evidence_crate_id": result.evidence_crate_id,
        "run_manifest_id": result.run_manifest_id,
        "task_id": result.task_id,
        "research_score_id": result.research_score_id,
        "node_id": result.node_id,
        "output_hash": result.output_hash,
        "run_state": result.run_state,
        "is_deterministic": result.is_deterministic,
        "evidence_matrix_id": result.evidence_matrix_id,
        "claim_ids": list(result.claim_ids),
        "method_ruling_ids": list(result.method_ruling_ids),
        "research_decision_ids": list(result.research_decision_ids),
    }
    if args.as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for key, value in payload.items():
            print(f"{key}: {value}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser for this module alone (``prog="mrr synthesis"``),
    usable directly (``python -m mrr.services.cli.synthesis_main run ...``)
    without going through ``mrr.services.cli.main`` at all — mirrors that
    module's own ``build_parser`` shape.
    """
    parser = argparse.ArgumentParser(
        prog="mrr synthesis",
        description=(
            "Synthesis-profile governance + run composition (task-packets/K1-T04.yaml): "
            "establish a real QuestionModel/ConceptCharter/locked MethodProtocol, then run "
            "the systematic_evidence_synthesis v1 evidence loop over them."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_run_subparser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        return run_command(args)

    parser.print_help()  # pragma: no cover - unreachable while "command" is required
    return 1


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
