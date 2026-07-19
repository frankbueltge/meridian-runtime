"""The planner/proposer application role (task-packets/E4-T03.yaml) — the
first AI ROLE of Epic E4. See ``mrr.services.planner.service`` for the full
design.

Same ``services/`` root and layering contract as every other
``mrr.services.*`` package (``mrr.services.research_score``'s own module
docstring has the shared wiring rationale): none of the seven core/
persistence packages may import ``mrr.services`` (import-linter contract 2,
pyproject.toml).
"""

from __future__ import annotations
