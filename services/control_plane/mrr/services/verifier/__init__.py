"""The deterministic verifier (task-packets/E4-T05.yaml) — the crux of Epic
E4: the verification DECISION is a CHECKED TOOL, never a model oracle (Epic
E4 exit criterion, docs/spec/06_IMPLEMENTATION_PLAN.md: "no model can mutate
authoritative state directly"). Fifth task of Epic E4.

Three modules, each with a narrow, honest job:

- ``mrr.services.verifier.numeric`` — MRR-FR-073: recomputes a claimed value
  from named inputs and a declared operation drawn from a CLOSED, safe set,
  using ``decimal.Decimal`` (exact; no ``eval``/``exec``/``compile``, no
  float drift).
- ``mrr.services.verifier.source`` — MRR-FR-072: decides an
  ``mrr.contracts.evidence_anchor.AnchorValidationStatus`` by LOCAL
  inspection only (no network) of caller-supplied artifact content.
- ``mrr.services.verifier.orchestrator`` — maps either tool's outcome to a
  ``Recommendation`` by a documented, TOTAL policy and assembles a full,
  schema-valid ``mrr.contracts.verification_result.VerificationResult``
  carrying the caller-declared ``independence_profile``. Persists NOTHING —
  recording (revision + event, the self-verification gate, the MRR-FR-075
  claim-status policy) is the existing, unmodified
  ``mrr.services.verification.service.VerificationService`` (E3-T04); this
  package only PRODUCES the object that service records.

Same ``services/`` root and layering contract as every other
``mrr.services.*`` package (``mrr.services.research_score``'s own module
docstring has the shared wiring rationale): none of the core/persistence
packages may import ``mrr.services`` (import-linter contract 2,
pyproject.toml).

--- The determinism invariant, enforced twice ---------------------------------

This package imports NO ``mrr.domain.model_adapter``, NO ``mrr.adapters.llm``,
NO provider SDK, and NO network client (``httpx``/``requests``/``urllib3``/
``aiohttp``/``socket``) anywhere in it — checked both by the shared
"framework- and provider-free" import-linter contract every core package
already belongs to (this package is deliberately NOT added to that contract,
since ``mrr.services`` is never a `source_module` there; see the module
docstring of ``tests/unit/architecture/test_verifier_determinism_boundary.py``
for why an explicit AST test is this package's own, additional guarantee) and
by that dedicated AST-based architecture test, mirroring
``tests/unit/architecture/test_llm_adapter_boundary.py``'s own precedent for
``mrr.adapters.llm``.
"""

from __future__ import annotations
