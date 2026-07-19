"""The Verification application service (task-packets/E3-T04.yaml):
``VerificationService`` — records ``VerificationResult`` objects while
enforcing the self-verification prohibition (MRR-FR-070, AGENTS.md rule 8)
and driving a failed verification into the claim's status by a
deterministic rule (MRR-FR-075). Fourth task of Epic E3 (claim, evidence,
correction kernel).

Same ``services/`` root and layering contract as
``mrr.services.claim``/``mrr.services.source_family`` (E3-T02/E3-T03) — see
``mrr.services.verification.service`` for the shared wiring pattern and the
full design rationale.
"""

from __future__ import annotations
