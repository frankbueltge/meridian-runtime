"""The skeptic role (task-packets/E4-T04.yaml) — the second AI ROLE of Epic
E4. See ``mrr.services.skeptic.service`` for the full design.

Same ``services/`` root and layering contract as every other
``mrr.services.*`` package (``mrr.services.research_score``'s own module
docstring has the shared wiring rationale): none of the seven core/
persistence packages may import ``mrr.services`` (import-linter contract 2,
pyproject.toml).
"""

from __future__ import annotations
