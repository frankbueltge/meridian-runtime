"""The ConceptCharter registry service (task-packets/K1-T04.yaml): create and
drive versioned ``ConceptCharter`` objects through ``CONCEPT_CHARTER_LIFECYCLE``
(``draft -> accepted -> superseded``, ``mrr.domain.lifecycles``).

Same ``services/`` root and layering contract as
``mrr.services.method_profile``/``mrr.services.question_model`` — see those
packages and ``mrr.services.concept_charter.service`` for the shared wiring
pattern this task reuses verbatim.
"""

from __future__ import annotations
