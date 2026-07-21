"""Source-family independence counting (task-packets/K1-T03.yaml,
docs/spec/08_RESEARCH_METHOD_KERNEL.md section 5's "independence validation"
deterministic step, MRR-MTH-015: "copied or derivative sources MUST NOT
count as independent evidence"). A pure, deterministic, framework-free
function pair, mirroring ``mrr.domain.independence`` (E3-T05)'s own
"dedup-key-then-count" shape and its "additive, non-destructive, no side
effects" invariants precisely — but over a genuinely DIFFERENT input shape.

--- A NEW module, not E3-T05 reuse -------------------------------------------

``mrr.domain.independence``'s two public functions
(``is_independent_of_producer``/``distinct_independent_reviews``) operate
EXCLUSIVELY over ``mrr.contracts.verification_result.IndependenceProfile`` —
a REVIEWER/verification-independence concept (E3-T04/T05: "who verified a
claim and how independently of its producer"), structurally unable to
accept an ``EvidenceMatrixRow``/source-family value (no shared shape; see
that module's own docstring, "Scope: DIMENSION independence, not IDENTITY
self-verification"). This module answers a DIFFERENT question: given a set
of ``EvidenceMatrixRow``-shaped evidence entries, how many DISTINCT,
independent SOURCE FAMILIES do they represent — a question about the
SOURCES an executor's own inclusion/matrix-assembly step produced, never
about who reviewed a claim afterward. ``SourceFamily``'s and
``SourceFamilyService``'s own existing docstrings both assert "the
independence calculation is E3-T05" — confirmed IMPRECISE by this module's
own author, having read E3-T05 in full: E3-T05 never imports, references, or
computes over ``SourceFamily``/``EvidenceMatrixRow`` anywhere. That
imprecision is flagged for a future housekeeping task to correct (out of
this module's own ``allowed_paths``); this module is the actual, new,
narrowly-scoped answer to "minimum independent source families per status"
(spec 08 section 5) that E3-T05 was never built to give.

--- A structural Protocol, not the concrete EvidenceMatrixRow model ---------

``SourceFamilyRow`` below is a small, ``@runtime_checkable`` ``Protocol``
(``source_family_id: str | None``, ``source_record_id: str``,
``verification_status: str``) rather than an import of
``mrr.contracts.evidence_matrix.EvidenceMatrixRow`` itself. Deliberately:
this module's own executor caller
(``mrr.services.node_runtime.synthesis_executor``) counts independence over
CORPUS ENTRIES a run has not yet persisted — before any real, URN-shaped
``SourceRecord``/``EvidenceMatrixRow`` identity is minted (MRR-FR-035
idempotency forbids the executor's own pure pipeline from minting random
URNs at all; see that module's own docstring, "Why execute() never mints an
id"). A structural Protocol lets this module count over EITHER a real,
already-persisted ``EvidenceMatrixRow`` (which structurally satisfies it)
OR the executor's own pre-persistence row representation (whose
``source_record_id`` is a portable, non-URN ``entry_id`` key) — the same
counting RULE either way, with no coupling to which stage of the pipeline
produced the row.

--- The dedup key: source_family_id, or a per-source singleton fallback -----

``family_key(row)`` returns ``row.source_family_id`` when non-null; when
null (no detected ``SourceFamily`` membership for this source), it falls
back to a SINGLETON key derived from ``row.source_record_id``
(``f"source:{row.source_record_id}"``) — an unaffiliated source is
presumptively its OWN independent family, consistent with
``mrr.contracts.source_family.SourceFamily``'s own "membership is asserted,
not assumed" additive-representation stance (that module's docstring: family
confidence/membership is only ever ADDED, never inferred by default). Two
rows sharing the identical non-null ``source_family_id`` collapse to ONE
distinct family; two rows with distinct or null ``source_family_id`` values
(each falling back to its own source record's singleton key) count
separately — this is the exact acceptance-test shape task-packets/
K1-T03.yaml names for this module.

--- Only "verified" rows count -----------------------------------------------

``distinct_independent_source_family_count`` counts distinct family keys
ONLY among rows whose ``verification_status == "verified"``. An
``"unverifiable"`` row remains present in the persisted ``EvidenceMatrix``
(MRR-MTH-015: "unverifiable rows are marked, never dropped") but contributes
ZERO to any eligibility threshold — an unresolved verification status cannot
license stronger claim language merely by existing. A ``"pending"`` row
counts toward NEITHER the numerator here NOR any other bucket this module
defines — flagged, per task-packets/K1-T03.yaml's own specification_gaps, as
this module's narrowest-defensible reading (a reviewer may prefer
``"pending"`` rows to count provisionally, or may want an explicit rule for
when a matrix may freeze while rows remain ``"pending"`` at all); this
module does not resolve that question, only documents that it is left open.

--- Additive, non-destructive stance (matches E3-T05 and SourceFamily) ------

Neither function here deletes, reorders, or reweights anything. Both take an
iterable of already-recorded ``EvidenceMatrixRow`` values and return a count
(int) or boolean computed fresh each call — no caching, no mutation of the
inputs, no side effects, no I/O, no framework import (MRR-NFR-010).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

#: The one ``verification_status`` value that counts toward any
#: independence threshold — see the module docstring's "Only 'verified'
#: rows count" section.
_COUNTS_TOWARD_INDEPENDENCE_STATUS = "verified"


@runtime_checkable
class SourceFamilyRow(Protocol):
    """The minimal structural shape this module needs from a row of
    evidence — see the module docstring's "A structural Protocol" section
    for why this is not the concrete
    ``mrr.contracts.evidence_matrix.EvidenceMatrixRow`` model. A real
    ``EvidenceMatrixRow`` satisfies this Protocol structurally, with no
    adapter needed.

    Declared as read-only ``@property`` members, not plain attributes: this
    module only ever READS these three fields, and a read-only Protocol
    lets both a frozen ``@dataclass`` (immutable attributes) and a Pydantic
    model whose ``verification_status`` is a narrower ``Literal["verified",
    "unverifiable", "pending"]`` (covariant, not invariant, with this
    Protocol's own ``str``) satisfy it structurally — a mutable/settable
    Protocol member would reject both for the opposite reasons.
    """

    @property
    def source_family_id(self) -> str | None: ...

    @property
    def source_record_id(self) -> str: ...

    @property
    def verification_status(self) -> str: ...


def family_key(row: SourceFamilyRow) -> str:
    """The dedup key this module counts distinct source families by: a
    row's own ``source_family_id`` when non-null, else a singleton key
    derived from ``source_record_id`` — see the module docstring's "The
    dedup key" section.

    Pure and deterministic: depends only on ``row``'s own two fields, with
    no side effects.
    """
    if row.source_family_id is not None:
        return row.source_family_id
    return f"source:{row.source_record_id}"


def distinct_independent_source_family_count(rows: Iterable[SourceFamilyRow]) -> int:
    """Count the DISTINCT source families represented among ``rows`` whose
    ``verification_status == "verified"`` — MRR-MTH-015's "minimum
    independent source families" building block.

    Two rows sharing one ``family_key`` count once; rows with distinct or
    null ``source_family_id`` (each falling back to its own
    ``source_record_id`` singleton) count separately. An ``"unverifiable"``
    or ``"pending"`` row is excluded from the count entirely, regardless of
    its own ``source_family_id`` — see the module docstring for the full
    rationale.

    Deterministic and order-independent — the result depends only on the SET
    of distinct family keys among the verified rows, never on how many times
    a key repeats or the order ``rows`` is iterated in. Never exceeds the
    number of ``"verified"`` rows supplied (dedup only removes, never adds);
    never negative. Consumes ``rows`` in one pass, so a single-use iterator
    (e.g. a generator) is safe to pass here.
    """
    verified_keys = {
        family_key(row)
        for row in rows
        if row.verification_status == _COUNTS_TOWARD_INDEPENDENCE_STATUS
    }
    return len(verified_keys)


def has_sufficient_independent_source_families(
    rows: Iterable[SourceFamilyRow], *, minimum: int
) -> bool:
    """``True`` iff ``distinct_independent_source_family_count(rows) >=
    minimum`` — a thin, optional convenience wrapper for a caller that only
    needs a threshold gate and not the exact count, mirroring
    ``mrr.domain.independence.has_independent_verification``'s identical
    "optional convenience" framing.

    Raises:
        ValueError: ``minimum`` is negative — a caller/programmer error,
            matching ``has_independent_verification``'s own identical guard.
    """
    if minimum < 0:
        raise ValueError(f"minimum must be >= 0, got {minimum!r}")
    return distinct_independent_source_family_count(rows) >= minimum
