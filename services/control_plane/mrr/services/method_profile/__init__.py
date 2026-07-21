"""The Method Profile registry service (task-packets/K0-T01.yaml): create
and drive versioned ``MethodProfile`` objects through the
``METHOD_PROFILE_LIFECYCLE`` (``draft -> accepted -> superseded``,
``mrr.domain.lifecycles``), and expose a read-side matching primitive —
"which currently-accepted profiles declare this capability name" — the
K0-T02 dispatch layer and later K1 tasks consume.

Same ``services/`` root and layering contract as
``mrr.services.research_score``/``mrr.services.capability_registry`` — see
those packages and ``mrr.services.method_profile.service`` for the shared
wiring pattern this task reuses.
"""

from __future__ import annotations
