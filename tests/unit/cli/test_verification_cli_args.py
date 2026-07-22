"""DB-free unit/smoke tests for ``mrr verification record`` (task-packets/
K1-T05.yaml) — mirrors ``tests/unit/services/cli/test_main.py``'s/
``test_synthesis_main.py``'s own "no PostgreSQL required" discipline:
MRR-NFR-012's explicit-degradation path (an unreachable database) is itself
exercised without a real database, by pointing ``--database-url`` at a port
nothing is listening on, and the "contract-invalid file never opens a
database connection" invariant is exercised the same way — the unreachable
URL would surface a DIFFERENT, DB-specific message if the code ever reached
it, so seeing the contract-violation message instead is itself proof the
database was never touched.

Acceptance-test mapping (task-packets/K1-T05.yaml, unit tier):

- AT4 ("a file failing the MRR-FR-072 conditional ... is rejected with the
  contract's own message, exit code 2, before any connection attempt") ->
  ``test_source_type_with_empty_evidence_inspected_rejected_before_any_connection_attempt``.
- AT5's ``--help`` half ("--help output for the family and the command
  documents every flag") -> ``test_record_subcommand_help_documents_every_flag``
  and the other ``--help``/usage tests below.

--- AT5's "unknown-claim URN produces exit 3" half: moved to the integration tier ---

task-packets/K1-T05.yaml labels this assertion "unit", but
``mrr.domain.exceptions.ObjectNotFoundError`` — the exception that actually
names the claim id in its message — is raised by
``mrr.persistence.repositories.PostgresObjectRepository.get_latest`` querying
the REAL, migrated generic ``objects`` table (docs/spec's "generic object
store" this packet's own ``forbidden_changes`` directs reusing rather than
adding a migration for). An unmigrated or fake substitute engine would
either lack that table entirely (a generic SQLAlchemy error, not
``ObjectNotFoundError`` — a materially different, less specific failure than
the one this acceptance test actually describes) or require reimplementing
``ObjectRepository`` as an in-memory fake here, which this packet's own
derived_decisions direct AGAINST ("reuse the existing binder helpers").
Testing the real, specific behavior honestly requires a real database, so
this half of AT5 is implemented at the integration tier instead —
``tests/integration/services/test_verification_cli_recording.py``'s
``test_unknown_claim_id_produces_exit_3_naming_the_claim_id`` — a disclosed
deviation (see that test's own docstring), not a weakened acceptance
criterion (AGENTS.md rule 4): the exact same fact ("unknown claim -> exit 3,
claim id named in the message") is verified, just against a real object
store rather than a fake one that could not reproduce it faithfully.

--- The target_kind mismatch check: DB-free, because it never touches the engine ---

``mrr.services.cli.verification_orchestration.record_verification`` checks
``verification.target_kind`` as its very first statement, before
constructing ANY repository over the ``engine`` argument (see that module's
own docstring). This lets the test below
(``test_record_verification_rejects_target_kind_mismatch_before_touching_the_engine``)
call it directly with an ``Engine`` pointed at an unreachable address — a
legitimate ``sqlalchemy.Engine`` object, satisfying this project's own strict
typing, that is never actually connected to.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from mrr.contracts import VerificationResult
from mrr.domain.identity import new_urn
from mrr.services.cli.main import build_parser, main
from mrr.services.cli.verification_main import build_parser as build_verification_parser
from mrr.services.cli.verification_main import main as verification_main
from mrr.services.cli.verification_orchestration import record_verification

#: 127.0.0.1:1 is a reserved, unassigned port that refuses connections
#: immediately on every CI/dev platform this project targets — mirrors
#: tests/unit/services/cli/test_main.py's own identical constant/rationale.
_UNREACHABLE_DATABASE_URL = "postgresql+psycopg://mrr:mrr@127.0.0.1:1/mrr_test"


def _independence_profile(**overrides: Any) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "principal": new_urn("person"),
        "model_family": "human-reviewer (no model invoked)",
        "prompt_family": "n/a — manual review checklist v3",
        "retrieval_path": "independent re-fetch via publisher API, not the original crawl",
        "code_path": "independent recomputation script, not the original analysis notebook",
        "data_access_path": "read-only snapshot corpus, separate credential from the proposer's",
    }
    profile.update(overrides)
    return profile


def _verification_payload(*, target_id: str, reviewer_id: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": new_urn("verification"),
        "api_version": "mrr/v1alpha1",
        "kind": "VerificationResult",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "created_by": reviewer_id,
        "content_hash": "sha256:" + "b" * 64,
        "target_id": target_id,
        "target_kind": "claim",
        "reviewer_id": reviewer_id,
        "reviewer_role": "independent reviewer",
        "independence_profile": _independence_profile(),
        "verification_type": "skeptic",
        "checks_performed": ["Searched for counterevidence and alternative explanations"],
        "evidence_inspected": [],
        "numeric_recomputation": None,
        "findings": [],
        "recommendation": "pass",
        "confidence": 0.8,
        "rationale": "Fixture rationale for a CLI unit test.",
        "conflicts_of_interest": [],
        "adjudication_relation": None,
    }
    payload.update(overrides)
    return payload


def _write_json(path: Path, content: dict[str, Any]) -> Path:
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# --help / usage-error smoke tests (AT5's --help half).
# ---------------------------------------------------------------------------


def test_top_level_help_mentions_the_verification_subcommand(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "verification" in out


def test_record_subcommand_help_documents_every_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["verification", "record", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for flag in (
        "--database-url",
        "--verification-file",
        "--claim-id",
        "--run-executor-id",
        "--actor",
        "--policy-version",
        "--correlation-id",
    ):
        assert flag in out, f"expected {flag!r} to be documented in --help output"


def test_verification_without_record_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["verification"])
    assert excinfo.value.code != 0


def test_main_parser_registers_verification_group() -> None:
    parser = build_parser()
    assert parser.prog == "mrr"
    # Sanity: parsing "verification record --help" doesn't blow up with an
    # unrecognized-subcommand error (argparse would exit 2, not 0).
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["verification", "record", "--help"])
    assert excinfo.value.code == 0


def test_build_parser_prog_name_is_mrr_verification() -> None:
    parser = build_verification_parser()
    assert parser.prog == "mrr verification"


def test_standalone_verification_main_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        verification_main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "record" in out


def test_policy_version_has_no_default_and_is_required() -> None:
    """derived_decisions (b): unlike ``mrr run``'s local-loop default, this
    command has NO default --policy-version — omitting it is a usage error.
    """
    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "verification",
                "record",
                "--database-url",
                _UNREACHABLE_DATABASE_URL,
                "--verification-file",
                "/nonexistent/does-not-matter.json",
                "--claim-id",
                new_urn("claim"),
                "--actor",
                new_urn("agent"),
            ]
        )
    assert excinfo.value.code != 0


# ---------------------------------------------------------------------------
# MRR-NFR-012: dependency checks, both entirely DB-free.
# ---------------------------------------------------------------------------


def test_missing_verification_file_is_a_dependency_failure_before_any_connection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing_file = tmp_path / "does-not-exist.json"

    exit_code = main(
        [
            "verification",
            "record",
            "--database-url",
            _UNREACHABLE_DATABASE_URL,
            "--verification-file",
            str(missing_file),
            "--claim-id",
            new_urn("claim"),
            "--actor",
            new_urn("agent"),
            "--policy-version",
            "policy-test",
        ]
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "cannot read --verification-file" in err
    # Proof no connection was ever attempted: the DB-unreachable message
    # (which WOULD appear if the code reached that check) is absent.
    assert "cannot reach the PostgreSQL database" not in err


def test_malformed_json_verification_file_is_a_dependency_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad_file = tmp_path / "not-json.json"
    bad_file.write_text("{not valid json", encoding="utf-8")

    exit_code = main(
        [
            "verification",
            "record",
            "--database-url",
            _UNREACHABLE_DATABASE_URL,
            "--verification-file",
            str(bad_file),
            "--claim-id",
            new_urn("claim"),
            "--actor",
            new_urn("agent"),
            "--policy-version",
            "policy-test",
        ]
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "is not valid JSON" in err
    assert "cannot reach the PostgreSQL database" not in err


def test_source_type_with_empty_evidence_inspected_rejected_before_any_connection_attempt(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """AT4: a file failing the MRR-FR-072 conditional (verification_type
    'source' with empty evidence_inspected) is rejected with the contract's
    own message, exit code 2, before any connection attempt.
    """
    claim_id = new_urn("claim")
    reviewer_id = new_urn("person")
    payload = _verification_payload(
        target_id=claim_id,
        reviewer_id=reviewer_id,
        verification_type="source",
        evidence_inspected=[],
    )
    verification_file = _write_json(tmp_path / "verification.json", payload)

    exit_code = main(
        [
            "verification",
            "record",
            "--database-url",
            _UNREACHABLE_DATABASE_URL,
            "--verification-file",
            str(verification_file),
            "--claim-id",
            claim_id,
            "--actor",
            new_urn("agent"),
            "--policy-version",
            "policy-test",
        ]
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "does not satisfy the VerificationResult contract" in err
    assert "MRR-FR-072" in err
    # Proof no connection was ever attempted.
    assert "cannot reach the PostgreSQL database" not in err


def test_run_reports_an_explicit_degraded_message_when_database_is_unreachable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    claim_id = new_urn("claim")
    reviewer_id = new_urn("person")
    payload = _verification_payload(target_id=claim_id, reviewer_id=reviewer_id)
    verification_file = _write_json(tmp_path / "verification.json", payload)

    exit_code = main(
        [
            "verification",
            "record",
            "--database-url",
            _UNREACHABLE_DATABASE_URL,
            "--verification-file",
            str(verification_file),
            "--claim-id",
            claim_id,
            "--actor",
            new_urn("agent"),
            "--policy-version",
            "policy-test",
        ]
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "cannot reach the PostgreSQL database" in err


# ---------------------------------------------------------------------------
# The target_kind mismatch check (record_verification's own first check,
# before touching the engine at all — see the module docstring).
# ---------------------------------------------------------------------------


def test_record_verification_rejects_target_kind_mismatch_before_touching_the_engine() -> None:
    claim_id = new_urn("claim")
    reviewer_id = new_urn("person")
    verification = VerificationResult.model_validate(
        _verification_payload(target_id=claim_id, reviewer_id=reviewer_id, target_kind="run")
    )
    # A real Engine, never actually connected to — the check under test
    # raises before any repository is constructed over it.
    unreachable_engine = sa.create_engine(_UNREACHABLE_DATABASE_URL)

    with pytest.raises(ValueError, match="target_kind"):
        record_verification(
            engine=unreachable_engine,
            verification=verification,
            claim_id=claim_id,
            actor=new_urn("agent"),
            policy_version="policy-test",
        )
