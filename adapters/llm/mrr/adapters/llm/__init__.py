"""Provider-neutral structured-generation layer (task-packets/E4-T02.yaml).

``mrr.adapters.llm.structured_generation`` is the first module under this
root — a higher-level function that USES an injected ``mrr.domain.
model_adapter.ModelAdapter`` (the E4-T01 port) to obtain a schema-valid
proposal from a model, with a bounded repair loop fed by Pydantic v2
validation errors. It is not itself a ``ModelAdapter``: its return type is a
parsed proposal plus an ordered audit trail, not a single
``ModelInvocationOutcome``.

This root is registered in the same import-linter "framework- and
provider-free" contract (MRR-NFR-004, MRR-NFR-010, pyproject.toml) as the
other core packages and ``mrr.adapters.object_store`` — safe here because
nothing under it imports a model-provider SDK, network client, or web/
workflow framework; it operates SOLELY through the injected port. A future
concrete vendor-SDK adapter (e.g. an Anthropic- or OpenAI-backed
``ModelAdapter`` implementation) would live under its own namespace root
(e.g. ``adapters/llm_anthropic/mrr/adapters/llm_anthropic/``) and would need
its own contract treatment at that point, since it would legitimately need a
provider SDK the current contract forbids — that root is deliberately not
created here (task-packets/E4-T02.yaml forbidden_changes: "a concrete vendor-
SDK adapter is a LATER task in its own separate package, not created here").
"""
