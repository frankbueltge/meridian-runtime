"""The Capability Registry application service (task-packets/E2-T02.yaml):
register signed ``NodeManifest`` objects after verifying their signature and
temporal validity, resolve the current valid manifest for a node, and match
a capability name to the nodes that currently declare it.

Same ``services/`` root and layering contract as ``mrr.services.research_score``
(E2-T01) — see that package and ``mrr.services.capability_registry.service``
for the shared wiring pattern.
"""

from __future__ import annotations
