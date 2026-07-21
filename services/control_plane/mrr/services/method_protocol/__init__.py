"""The MethodProtocol registry service (task-packets/K1-T04.yaml): create and
drive versioned ``MethodProtocol`` objects through the first three edges of
``METHOD_PROTOCOL_LIFECYCLE`` (``draft -> reviewed -> locked``,
``mrr.domain.lifecycles``) — the exact three transitions this first real
run's own single, first-time confirmatory pass needs.

Same ``services/`` root and layering contract as
``mrr.services.evidence_matrix``/``mrr.services.question_model`` — see those
packages and ``mrr.services.method_protocol.service`` for the shared wiring
pattern this task reuses. ``amend``/``execute`` are NOT implemented here
(task-packets/K1-T04.yaml forbidden_changes) — a future task's job if this
specific ``MethodProtocol`` is ever amended after this run.
"""

from __future__ import annotations
