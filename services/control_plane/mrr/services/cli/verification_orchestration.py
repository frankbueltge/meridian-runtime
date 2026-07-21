"""``record_verification`` (task-packets/K1-T05.yaml): the composition
function behind ``mrr verification record`` — wires the two services a
verification recording needs (``mrr.services.verification.service
.VerificationService``, and the ``mrr.services.claim.service.ClaimService``
it drives claim-status transitions through on a failed recommendation),
resolves the target ``Claim`` from the generic object store, and calls
``VerificationService.record`` exactly once.

This module introduces **no new domain behavior** (task-packets/E2-T07.yaml's
CLI law, restated by this packet's own derivation): every write happens
inside ``VerificationService.record``'s own single atomic unit of work,
exactly as its own integration tests wire it
(``tests/integration/services/verification/test_service.py``'s
``_services_for`` helper — the binder calls below are copied from there
verbatim, not reinvented). This function only resolves the caller-supplied
``--claim-id`` into the real ``Claim`` object ``record`` requires (its own
signature takes a full ``Claim``, not an id — see that service's module
docstring's "target_id must match" section for why), and reports what
happened; it does not decide anything ``VerificationService``/``ClaimService``
do not already decide themselves.

--- Two checks that are NOT domain policy, only wiring type-safety ------------

``VerificationResult.target_kind`` (schemas/verification-result.schema.json)
names what KIND of object a verification judges — ``"claim"``, ``"run"``, or
``"artifact"``. This command always resolves ``--claim-id`` to a ``Claim``
and always calls ``VerificationService.record`` with it (R1/R2's own fixed
shape — there is no ``--run-id``/``--artifact-id`` alternative), so a
verification file whose own ``target_kind`` is not ``"claim"`` can never be
processed correctly by this command: checked FIRST, before any database
call, with a plain ``ValueError`` naming the mismatch (task-packets/
K1-T05.yaml derived_decisions (d): "target-kind" is one of the recording-
refused failure modes, exit code 3). This is a transport-shape check ("does
the file even match what this command does"), not a business rule
``VerificationService`` itself would need to enforce — it never gets to see
a target_kind other than ``"claim"`` from this caller.

Symmetrically, the generic object store (``mrr.domain.repositories
.ObjectRepository``) has no per-kind table — ``--claim-id`` could resolve to
ANY stored object kind (a ``TaskBundle``, a ``RunManifest``, ...), not only a
``Claim``. Before attempting ``Claim.model_validate`` on whatever body was
resolved (which could raise a confusing, deeply-nested
``pydantic.ValidationError`` for an unrelated object shape), this function
checks the resolved ``StoredObject.kind`` is literally ``"Claim"`` and raises
a clear, specific ``ValueError`` otherwise — again a type-safety wiring
check, not a decision ``VerificationService.record`` makes on this module's
behalf.

--- Everything else is exactly the service's own decision --------------------

Rule-8 self-verification refusal, the target_id-must-match-claim.id guard,
and the failed-verification-to-claim-status policy (MRR-FR-075) all remain
exclusively inside ``VerificationService.record`` (``services/control_plane/
mrr/services/verification/service.py``, untouched — this packet's
``forbidden_changes``) — this function calls it exactly once and lets every
exception it raises propagate unmodified; ``mrr.services.cli
.verification_main`` is the layer that turns those into typed, non-zero CLI
exit codes (never a stack trace as UX).
"""

from __future__ import annotations

from dataclasses import dataclass

from mrr.contracts import Claim, Urn, VerificationResult
from mrr.domain.identity import new_urn
from mrr.persistence.repositories import (
    PostgresEdgeRepository,
    PostgresEventLog,
    PostgresObjectRepository,
)
from mrr.services.claim.service import ClaimService
from mrr.services.claim.service import bind_edge_unit_of_work as _bind_claim_edge_uow
from mrr.services.claim.service import bind_unit_of_work as _bind_claim_uow
from mrr.services.verification.service import VerificationService
from mrr.services.verification.service import bind_unit_of_work as _bind_verification_uow
from sqlalchemy import Engine

#: The one ``target_kind`` this command ever accepts — see the module
#: docstring's "Two checks that are NOT domain policy" section.
_SUPPORTED_TARGET_KIND = "claim"

#: The ``StoredObject.kind`` a resolved ``--claim-id`` MUST carry — mirrors
#: ``mrr.contracts.claim.Claim.kind``'s own ``Literal["Claim"]``.
_CLAIM_STORED_KIND = "Claim"


@dataclass(frozen=True, slots=True)
class VerificationRecordingResult:
    """Every fact ``mrr verification record`` prints — mirrors
    ``mrr.services.cli.orchestration.LocalEvidenceLoopResult``'s own
    "everything a caller needs, without re-running anything" shape.

    ``claim_status`` is read back from the database AFTER
    ``VerificationService.record`` returns (not from the ``claim`` argument
    passed into it), so it reflects the claim's REAL current status whether
    or not a failed-verification transition actually happened — the "possibly
    transitioned" status R2 asks the command to print.
    """

    verification_id: Urn
    revision: int
    claim_id: Urn
    claim_status: str


def record_verification(
    *,
    engine: Engine,
    verification: VerificationResult,
    claim_id: Urn,
    run_executor_id: Urn | None = None,
    actor: Urn,
    policy_version: str,
    correlation_id: Urn | None = None,
) -> VerificationRecordingResult:
    """Resolve ``claim_id`` to a real ``Claim`` and call
    ``VerificationService.record`` exactly once. See the module docstring
    for the two wiring-only checks this function performs itself
    (``target_kind``, resolved-object ``kind``) and why neither is domain
    behavior.

    Args:
        verification: an ALREADY CONTRACT-VALID ``VerificationResult`` — this
            function does not parse or validate a raw file; that happens in
            ``mrr.services.cli.verification_main`` BEFORE any database
            connection is opened (task-packets/K1-T05.yaml invariant).
        claim_id: the URN of the claim this verification is recorded
            against, loaded from the generic object store (R2).
        run_executor_id: the producing run's executor identity, if known — an
            explicit, optional, caller-supplied value (derived_decisions (c)
            of task-packets/K1-T05.yaml), never derived from stored state.
        correlation_id: generated fresh (``new_urn("research-run")``,
            matching every other composition function in this package) when
            omitted.

    Returns:
        A ``VerificationRecordingResult`` naming the stored verification's
        id/revision and the claim's current (possibly transitioned) status.

    Raises:
        ValueError: ``verification.target_kind`` is not ``"claim"``, or the
            object resolved for ``claim_id`` is not of kind ``"Claim"`` (both
            checked before ``VerificationService.record`` is ever called) —
            or anything ``VerificationService.record`` itself raises as a
            plain ``ValueError`` (``target_id`` mismatch, non-1 revision).
        mrr.domain.exceptions.ObjectNotFoundError: ``claim_id`` resolves to
            no stored object at all ("unknown claim").
        mrr.domain.exceptions.SelfVerificationError: the rule-8 gate
            (MRR-FR-070 / AGENTS.md rule 8) refuses this recording.
        mrr.domain.exceptions.InvalidTransitionError,
        mrr.domain.exceptions.ClaimNotFoundError: propagated unmodified from
            ``VerificationService.record``'s own failed-verification claim-
            status transition (MRR-FR-075) — see that method's own docstring
            for when this can happen.
    """
    if verification.target_kind != _SUPPORTED_TARGET_KIND:
        raise ValueError(
            f"--verification-file declares target_kind={verification.target_kind!r}, but "
            "`mrr verification record` only records verifications whose target_kind is "
            f"{_SUPPORTED_TARGET_KIND!r} — it always resolves --claim-id to a Claim and calls "
            "VerificationService.record with it"
        )

    resolved_correlation_id = (
        correlation_id if correlation_id is not None else new_urn("research-run")
    )

    object_repository = PostgresObjectRepository(engine)
    event_log = PostgresEventLog(engine)
    edge_repository = PostgresEdgeRepository(engine)

    claim_record = _bind_claim_uow(engine, object_repository, event_log)
    claim_record_edge = _bind_claim_edge_uow(engine, event_log)
    claim_service = ClaimService(
        object_repository, event_log, edge_repository, claim_record, claim_record_edge
    )

    verification_record = _bind_verification_uow(engine, object_repository, event_log)
    verification_service = VerificationService(verification_record, claim_service)

    # R2: "the command loads the claim's current revision from the generic
    # object store table" — ObjectRepository.get_latest raises
    # ObjectNotFoundError for an unknown claim_id ("unknown claim", exit 3).
    stored_claim = object_repository.get_latest(claim_id)
    if stored_claim.kind != _CLAIM_STORED_KIND:
        raise ValueError(
            f"--claim-id {claim_id!r} resolves to a stored object of kind "
            f"{stored_claim.kind!r}, not {_CLAIM_STORED_KIND!r}"
        )
    claim = Claim.model_validate(stored_claim.body)

    stored_verification = verification_service.record(
        verification,
        claim,
        run_executor_id=run_executor_id,
        actor=actor,
        policy_version=policy_version,
        correlation_id=resolved_correlation_id,
    )

    updated_claim = object_repository.get_latest(claim_id)

    return VerificationRecordingResult(
        verification_id=stored_verification.id,
        revision=stored_verification.revision,
        claim_id=claim_id,
        claim_status=updated_claim.body["status"],
    )
