"""The evidence-relation classification service (task-packets/N1-T04.yaml):
``RelationClassificationService`` — a DB-free service that drives an INJECTED
``mrr.domain.model_adapter.ModelAdapter`` over the cases of a blind
classification commission and returns a
``mrr.domain.relation_proposal.RelationProposalSet``.

Like its sibling ``mrr.services.validation``, this package opens no database
connection and constructs no repository. Unlike it, this one does reach a
model — but never one it built itself: the adapter is a constructor argument,
exactly as ``mrr.adapters.llm.structured_generation.generate_structured``
requires of its own callers, so every test in every tier drives a scripted
fake and no test can reach a network or read an API key.

See ``mrr.services.classification.relation_service`` for the design
rationale, and ``mrr.domain.relation_proposal`` for why nothing this package
produces can be called "verified".
"""

from __future__ import annotations
