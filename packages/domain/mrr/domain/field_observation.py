"""Pure, framework-free field-observation intake + integrity-gate core
(task-packets/R2-T01.yaml R1). Hand-rolled, no new dependency, no I/O — every
function here takes already-computed values (a declared hash, an already-
read actual hash) and returns a plain, typed result. Mirrors
``mrr.domain.citation_audit``'s own "takes already-loaded values" precedent:
reading a descriptor file and hashing its declared inputs is the SERVICE's
job (``mrr.services.field_observation.service.FieldObservationService``),
never this module's.

--- Observation is not optimization (the honesty boundary this packet exists for) ---

This module answers exactly one question about each declared input: does its
actual sha256 (computed by the caller from the real file bytes) match the
value pinned in the committed descriptor (task-packets/R2-T01.yaml derived_
decisions (b))? It never re-implements or re-scores the frozen N2 citation
audit (``mrr.domain.citation_audit``/``mrr.services.citation_audit.service
.CitationAuditService``, reused unchanged by the service layer) and contains
no model/LLM step, no proposal, and no optimizer anywhere.

--- The gate is fail-closed, and is the ONLY thing this module decides -----

:func:`check_and_gate` raises :class:`IntegrityGateError` the moment ANY
:class:`AnchorCheckResult` is ``"anchor_mismatch"`` — naming the FIRST
mismatch in a stable, role-sorted order (task-packets/R2-T01.yaml R1:
"fail-closed, naming the FIRST mismatch in a stable order (sorted by
role)"). Whether the frozen N2 evaluator is ever invoked afterward is the
SERVICE's decision (it must not even construct
``mrr.services.citation_audit.service.CitationAuditService`` before this
gate has passed) — this module has no way to call it at all, since it does
not import anything from ``mrr.services``.

--- Two closed sets, never collapsed (AGENTS.md prohibited shortcut) --------

:data:`BatchRole` is the closed set of exactly the two named inputs a
committed observation-batch descriptor ever carries (``"manifest"``,
``"snapshot"``) — this batch shape has no third input. :data:`AnchorStatus`
is the closed set of exactly two outcomes a hash comparison can have
(``"anchor_ok"``, ``"anchor_mismatch"``) — never collapsed into a bare
``bool`` that a future caller could silently reinterpret.

--- Determinism (task-packets/R2-T01.yaml invariant) -------------------------

No wall clock anywhere in this module. :meth:`ObservationBatch.inputs` and
:func:`check_and_gate` both sort explicitly by ``role`` (never a
``dict``/``set`` iteration order, never the caller's own argument order), so
calling either twice over equal inputs yields an identical result.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

#: The closed set of exactly the two named inputs a committed observation-
#: batch descriptor carries (task-packets/R2-T01.yaml R3: "manifest" and
#: "snapshot", each an already-committed N2 fixture) — this batch shape has
#: no third input, and no caller of this module may invent one.
BatchRole = Literal["manifest", "snapshot"]

#: Every declared role, in the fixed, stable order :func:`check_and_gate`
#: and :meth:`ObservationBatch.inputs` sort by — re-exported here as the
#: single source of truth for that ordering (mirrors
#: ``mrr.domain.citation_audit.CITATION_STATUSES``'s identical role as a
#: shared, fixed-order tuple).
BATCH_ROLES: tuple[BatchRole, ...] = ("manifest", "snapshot")


class FieldObservationError(Exception):
    """Base class for every typed error this module raises."""


@dataclass(frozen=True, slots=True)
class BatchInput:
    """One declared input from a committed observation-batch descriptor
    (task-packets/R2-T01.yaml R1/R3): its role, its declared path (exactly
    as written in the descriptor — resolving it relative to the descriptor's
    own directory is the SERVICE's job, never this dataclass's), and its
    pinned sha256 anchor (already in ``"sha256:<hex>"`` form).
    """

    role: BatchRole
    path: str
    declared_sha256: str


@dataclass(frozen=True, slots=True)
class ObservationBatch:
    """The parsed shape of a committed observation-batch descriptor
    (task-packets/R2-T01.yaml R1/R3, e.g. ``corpora/e2e-survey/observation-
    batch.v1.json``). Carries exactly the two named inputs the descriptor
    declares — ``manifest`` and ``snapshot`` — as their own explicit fields,
    never a generic list a caller could reorder or extend silently.
    """

    schema_version: str
    batch_id: str
    observation_kind: str
    audit_target: str
    manifest: BatchInput
    snapshot: BatchInput

    def inputs(self) -> tuple[BatchInput, ...]:
        """Both declared inputs, sorted by ``role`` — the ONE place this
        ordering is decided (task-packets/R2-T01.yaml invariant: "no
        unordered iteration"), so a caller iterating this always sees
        ``manifest`` before ``snapshot`` regardless of how the two fields
        happen to be laid out on this dataclass.
        """
        return tuple(sorted((self.manifest, self.snapshot), key=lambda item: item.role))


#: The closed set of exactly two hash-comparison outcomes (task-packets/
#: R2-T01.yaml R1) — never collapsed into a bare ``bool``.
AnchorStatus = Literal["anchor_ok", "anchor_mismatch"]


@dataclass(frozen=True, slots=True)
class AnchorCheckResult:
    """The named result of one integrity-anchor comparison (task-packets/
    R2-T01.yaml R1: "returning a typed result", mirroring
    ``mrr.domain.citation_audit.TitleMatchResult``'s identical "never a bare
    bool" precedent). ``status`` is the definitive verdict; ``declared_
    sha256``/``actual_sha256`` are both carried so a caller (or a rendered
    report) can show the disagreement without recomputing anything.
    """

    role: BatchRole
    path: str
    declared_sha256: str
    actual_sha256: str
    status: AnchorStatus


def check_anchor(
    role: BatchRole, path: str, declared_sha256: str, actual_sha256: str
) -> AnchorCheckResult:
    """Pure comparison of two already-computed hash strings — no file I/O,
    no normalisation guessed (task-packets/R2-T01.yaml R1: "compares the two
    hash strings for exact equality (both already in 'sha256:<hex>' form; no
    normalisation guessed)"). ``"anchor_ok"`` iff the two strings are exactly
    equal, else ``"anchor_mismatch"``.
    """
    status: AnchorStatus = "anchor_ok" if declared_sha256 == actual_sha256 else "anchor_mismatch"
    return AnchorCheckResult(
        role=role,
        path=path,
        declared_sha256=declared_sha256,
        actual_sha256=actual_sha256,
        status=status,
    )


class IntegrityGateError(FieldObservationError):
    """Raised by :func:`check_and_gate` the moment ANY declared input's
    actual sha256 does not match its pinned anchor — a fail-closed refusal,
    raised BEFORE the frozen N2 evaluator is ever invoked by the service
    layer (task-packets/R2-T01.yaml invariant: "the gate is strictly before
    the evaluator"). Carries ``role``, ``path``, ``declared_sha256``, and
    ``actual_sha256`` so a caller can report exactly which input failed and
    why, without parsing the message string.
    """

    def __init__(
        self, role: BatchRole, path: str, declared_sha256: str, actual_sha256: str
    ) -> None:
        self.role = role
        self.path = path
        self.declared_sha256 = declared_sha256
        self.actual_sha256 = actual_sha256
        super().__init__(
            f"integrity gate failed for input {role!r} at {path!r}: declared sha256 "
            f"{declared_sha256!r} does not match actual sha256 {actual_sha256!r}"
        )


def check_and_gate(results: Sequence[AnchorCheckResult]) -> None:
    """Raise :class:`IntegrityGateError` the moment ANY ``results`` entry is
    ``"anchor_mismatch"`` — naming the FIRST mismatch in ``role``-sorted
    order (task-packets/R2-T01.yaml R1), never the caller's own argument
    order. Returns ``None`` (does nothing) when every result is
    ``"anchor_ok"``, however many results are given, including zero.
    """
    ordered = sorted(results, key=lambda result: result.role)
    for result in ordered:
        if result.status == "anchor_mismatch":
            raise IntegrityGateError(
                result.role, result.path, result.declared_sha256, result.actual_sha256
            )
