"""The synthetic-fixture gate (task-packets/K1-T02.yaml, MRR-MTH-012): a
pure, framework-free decision function guarding against fixture-classified
data ever being treated as empirical evidence. No persistence, no I/O, no
provider import (MRR-NFR-010).

--- ADR-0010 dependency: this function is unblocked; its WIRING is not -----

MRR-MTH-012, AS SPECIFIED, needs a real, stored, propagating classification
value on first-class objects: "fixture-classified inputs propagate their
classification, and any attempt to derive an empirical claim from them fails
closed." ADR-0010 (docs/spec/adr/ADR-0010-OBJECT-LEVEL-DATA-CLASSIFICATION.md)
is the proposal that adds exactly that — an optional ``classification``
property on ``common.schema.json#/$defs/baseObject``/``BaseObject``,
extending docs/spec/02_DOMAIN_MODEL.md section 4's five-value vocabulary with
one new value, ``SYNTHETIC_TEST_FIXTURE``. ADR-0010's own status is
ACCEPTED, but its "staged adoption" plan is explicit that step 1 (the
schema/contract field addition itself) is its OWN separate task — NOT bundled
into the same change that accepts the ADR, and NOT this packet's job either.
As of this task, ``BaseObject`` still carries no ``classification`` field at
all (confirmed by direct reading of ``packages/contracts/mrr/contracts/
common.py`` — only the unrelated, reference-shaped ``ArtifactRef.
classification`` exists), so there is no real object anywhere in this
codebase for this function to be wired against yet.

This module is therefore deliberately split, exactly as task-packets/
K1-T02.yaml's own derived_decisions describe: the pure decision function
below is fully buildable and unit-testable TODAY (it takes a plain
``str | None`` — no schema dependency to call it with a literal string in a
test), but wiring it against any REAL stored object's classification
(``SourceRecord``, ``EvidenceAnchor``, ``Claim``, ``EvidenceMatrixRow``, ...)
is explicitly BLOCKED and NOT delivered here. The recommended next step,
once the schema/contract field lands in its own future task, is a small
follow-up slice that reads a resolved source's stored ``classification``
field and calls this already-built, already-tested function — no new design
work, only wiring.

Every alternative marker mechanism was considered and rejected for the
reasons ADR-0010 itself already gives: a K1-T02-local marker field would
fork classification into two parallel, drifting mechanisms; ``labels.
classification`` (already available today, no schema change needed) is
"unsigned-adjacent, weakly typed, and deliberately non-semantic; policy MUST
NOT hang on them" (ADR-0010's own "Alternatives considered" section) — this
module deliberately does NOT read ``labels`` for the same reason ADR-0010
rejects it as a policy foundation, even though the ADR is not yet fully
adopted (schema-wise).
"""

from __future__ import annotations

from mrr.domain.exceptions import SyntheticFixtureNotEvidenceError

#: ADR-0010's proposed ``baseObject.classification`` value marking a
#: fixture-derived object. A local, literal string — NOT imported from
#: ``mrr.contracts.common.Classification`` — because that enum does not
#: (yet) carry this value at all; there is no contracts-level symbol to
#: import until ADR-0010's own staged-adoption step 1 lands.
SYNTHETIC_TEST_FIXTURE_CLASSIFICATION = "SYNTHETIC_TEST_FIXTURE"


def assert_not_synthetic_fixture_evidence(*, source_classification: str | None) -> None:
    """MRR-MTH-012: raise ``SyntheticFixtureNotEvidenceError`` iff
    ``source_classification == "SYNTHETIC_TEST_FIXTURE"``. Every other input
    — ``None`` (unclassified) and every one of the five EXISTING
    ``mrr.contracts.common.Classification`` values (``PUBLIC``/``INTERNAL``/
    ``RESTRICTED``/``SENSITIVE``/``PARTICIPANT_IDENTIFIABLE``) — passes
    silently (returns ``None``).

    Pure and deterministic: depends only on the one argument, performs no
    I/O, and imports no persistence, provider, or framework module. Not
    wired against any real stored object's classification — see the module
    docstring's "ADR-0010 dependency" section for why, and for the
    recommended follow-up once the schema/contract field exists.
    """
    if source_classification == SYNTHETIC_TEST_FIXTURE_CLASSIFICATION:
        raise SyntheticFixtureNotEvidenceError(source_classification=source_classification)
